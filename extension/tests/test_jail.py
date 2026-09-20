"""What a confined JVM is given before it runs."""

import os
import shutil
import ssl
import subprocess
from pathlib import Path

import pytest

from vis_lang_clojure import jail

PROXY = "http://token-123@127.0.0.1:41234"
"""A sandbox proxy, spelled the way Vis spells it."""


def preamble_run(script, arguments, **environment):
    """Run `script` in front of `arguments`, with nothing else in the environment."""
    return subprocess.run(
        ["/bin/sh", str(script), *arguments],
        capture_output=True,
        text=True,
        env={"PATH": os.environ.get("PATH", ""), **environment},
        check=False,
    )


def options(script, **environment):
    """The JVM options the preamble exports before it hands over."""
    done = preamble_run(
        script,
        ["/bin/sh", "-c", 'printf %s "${JAVA_TOOL_OPTIONS:-}"'],
        **environment,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_the_command_runs_behind_the_preamble(tmp_path):
    command = jail.prepared(("java", "-version"), str(tmp_path))
    assert Path(command[0]).name == "sh"
    assert command[1] == str(Path(tmp_path) / jail.HOME / jail.PREAMBLE_NAME)
    assert command[2:] == ("java", "-version")
    assert os.access(command[1], os.X_OK)


def test_without_a_proxy_nothing_is_prepared(tmp_path):
    script = jail.preamble(str(tmp_path))
    assert options(script) == ""
    assert not (Path(tmp_path) / jail.HOME / ".m2" / "settings.xml").exists()


def test_a_proxy_reaches_the_jvm_as_system_properties(tmp_path):
    # A JVM ignores `https_proxy`, so a confined Clojure run used to fail on an
    # unknown host before it had downloaded anything.
    script = jail.preamble(str(tmp_path))
    home = str(Path(tmp_path) / jail.HOME)
    exported = options(script, https_proxy=PROXY)
    assert f"-Duser.home={home}" in exported
    assert "-Dhttp.proxyHost=127.0.0.1" in exported
    assert "-Dhttp.proxyPort=41234" in exported
    assert "-Dhttps.proxyHost=127.0.0.1" in exported
    assert "-Dhttps.proxyPort=41234" in exported


def test_options_already_in_the_environment_are_kept(tmp_path):
    script = jail.preamble(str(tmp_path))
    exported = options(script, https_proxy=PROXY, JAVA_TOOL_OPTIONS="-Xmx512m")
    assert exported.endswith("-Xmx512m")


def test_maven_is_told_about_the_proxy_and_its_credential(tmp_path):
    # Maven takes its proxy from settings.xml, and the sandbox proxy answers
    # 407 without the credential the environment carries.
    script = jail.preamble(str(tmp_path))
    settings = Path(tmp_path) / jail.HOME / ".m2" / "settings.xml"
    options(script, https_proxy=PROXY)
    written = settings.read_text()
    assert "<host>127.0.0.1</host>" in written
    assert "<port>41234</port>" in written
    assert "<username>token-123</username>" in written
    assert oct(settings.stat().st_mode & 0o777) == "0o600"


def test_a_proxy_without_a_credential_asks_for_none(tmp_path):
    script = jail.preamble(str(tmp_path))
    settings = Path(tmp_path) / jail.HOME / ".m2" / "settings.xml"
    options(script, https_proxy="http://127.0.0.1:8080")
    written = settings.read_text()
    assert "<host>127.0.0.1</host>" in written
    assert "<username>" not in written


def test_a_trust_store_that_is_already_built_is_named(tmp_path):
    script = jail.preamble(str(tmp_path))
    home = Path(tmp_path) / jail.HOME
    bundle = tmp_path / "ca.pem"
    bundle.write_text("a bundle this test never has to parse\n")
    key = subprocess.run(
        ["/bin/sh", "-c", f"cksum < {bundle} | tr ' ' '-'"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    store = home / f"trust-{key}.p12"
    store.write_bytes(b"")
    exported = options(script, https_proxy=PROXY, SSL_CERT_FILE=str(bundle))
    assert f"-Djavax.net.ssl.trustStore={store}" in exported
    assert "-Djavax.net.ssl.trustStoreType=PKCS12" in exported


def test_without_a_certificate_bundle_no_trust_store_is_named(tmp_path):
    script = jail.preamble(str(tmp_path))
    exported = options(
        script, https_proxy=PROXY, SSL_CERT_FILE=str(tmp_path / "missing.pem")
    )
    assert "trustStore" not in exported


@pytest.mark.skipif(
    shutil.which("java") is None, reason="the trust store is built by a JDK"
)
def test_the_certificate_bundle_becomes_a_trust_store(tmp_path):
    bundle = ssl.get_default_verify_paths().cafile
    if not bundle or not os.path.exists(bundle):
        pytest.skip("this machine has no certificate bundle to convert")
    script = jail.preamble(str(tmp_path))
    home = Path(tmp_path) / jail.HOME
    exported = options(
        script, https_proxy=PROXY, SSL_CERT_FILE=bundle, PATH=os.environ["PATH"]
    )
    built = list(home.glob("trust-*.p12"))
    assert len(built) == 1
    assert built[0].stat().st_size > 0
    assert f"-Djavax.net.ssl.trustStore={built[0]}" in exported


def test_the_preamble_is_rewritten_when_it_is_stale(tmp_path):
    script = Path(jail.preamble(str(tmp_path)))
    script.write_text("#!/bin/sh\nexit 7\n")
    assert (
        Path(jail.preamble(str(tmp_path)))
        .read_text()
        .startswith("#!/bin/sh\n# Prepare")
    )


def test_the_shared_repository_is_reachable_from_the_maven_home(tmp_path):
    # The extension grants the person's own Maven repository to the run and
    # names it here; the JVM keeps writing to the Maven home the preamble owns,
    # and that home points at the shared repository.
    shared = tmp_path / "person" / ".m2" / "repository"
    script = jail.preamble(str(tmp_path))
    done = preamble_run(
        script,
        ["/bin/sh", "-c", "true"],
        https_proxy=PROXY,
        **{jail.REPOSITORY_VARIABLE: str(shared)},
    )
    assert done.returncode == 0, done.stderr
    home = Path(tmp_path) / jail.HOME
    assert (home / ".m2" / "repository").is_symlink()
    assert os.path.realpath(home / ".m2" / "repository") == os.path.realpath(shared)
    assert shared.is_dir()
    settings = (home / ".m2" / "settings.xml").read_text()
    assert f"<localRepository>{shared}</localRepository>" in settings


def test_without_a_shared_repository_the_maven_home_keeps_its_own(tmp_path):
    script = jail.preamble(str(tmp_path))
    done = preamble_run(script, ["/bin/sh", "-c", "true"], https_proxy=PROXY)
    assert done.returncode == 0, done.stderr
    home = Path(tmp_path) / jail.HOME
    assert not (home / ".m2" / "repository").exists()
    assert "<localRepository>" not in (home / ".m2" / "settings.xml").read_text()
