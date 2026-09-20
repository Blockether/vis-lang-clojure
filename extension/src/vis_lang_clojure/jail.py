"""What a jailed JVM needs before it can reach a Maven repository.

Vis can confine a tool: it may write to the workspace it was given and to Vis'
own state directory, and everything it sends leaves through a local proxy that
asks for a credential and terminates TLS with a certificate authority of its
own. A program that reads `https_proxy` needs nothing more than that variable.
A JVM does not read it — it takes its proxy from system properties — and Maven
takes its proxy from `settings.xml`, so a confined Clojure run reaches no
repository at all and fails with an unknown host.

This module prepares both, and it does so inside the sandbox, where the proxy
and its certificate bundle exist: a small shell preamble runs in front of the
real command and hands over to it with `exec`. The preamble points the JVM at
the proxy, builds a trust store from the sandbox's certificate bundle, writes a
`settings.xml` that carries the proxy and its credential, and names a Maven home
the confined run may write to. With no proxy in the environment it sets nothing
and execs the command unchanged.

The Maven home it names is Vis' own, not `$HOME`: a confined run may not write
to the real one, and a `settings.xml` of Vis' making never belongs in a
directory a person keeps their own in.
"""

from __future__ import annotations

import os

from vis_lang_interface import tool_path

HOME = "jail"
"""Directory under the boot directory that holds everything below."""

PREAMBLE_NAME = "jvm-preamble.sh"
"""The shell preamble, written next to the home it prepares."""

TRUST_NAME = "VisTrust.java"
"""Source of the helper that turns a certificate bundle into a trust store."""

TRUST_PASSWORD = "changeit"
"""Password of the generated trust store, which holds public certificates only."""

REPOSITORY_VARIABLE = "VIS_LANG_CLOJURE_MAVEN_REPO"
"""Variable naming the shared Maven repository a confined run was granted."""

PREAMBLE = """#!/bin/sh
# Prepare a confined JVM for Vis' sandbox proxy, then run the real command.
#
# Written by vis-lang-clojure. Edits are lost: the extension rewrites this file
# whenever its content differs from the release it ships.
set -u

home=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
url=${https_proxy:-${HTTPS_PROXY:-${http_proxy:-${HTTP_PROXY:-}}}}

if [ -z "$url" ]; then
  exec "$@"
fi

rest=${url#*://}
credential=
authority=$rest
case $rest in
  *@*)
    credential=${rest%@*}
    authority=${rest##*@}
    ;;
esac
authority=${authority%%/*}
host=${authority%%:*}
port=${authority##*:}
if [ "$port" = "$authority" ]; then
  port=
fi
user=${credential%%:*}
password=
case $credential in
  *:*) password=${credential#*:} ;;
esac

options="-Duser.home=$home"
if [ -n "$host" ] && [ -n "$port" ]; then
  options="$options -Dhttp.proxyHost=$host -Dhttp.proxyPort=$port"
  options="$options -Dhttps.proxyHost=$host -Dhttps.proxyPort=$port"
  options="$options -Dhttp.nonProxyHosts=localhost|127.0.0.1"
fi

java=java
if [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/java" ]; then
  java=$JAVA_HOME/bin/java
fi

bundle=${SSL_CERT_FILE:-${CURL_CA_BUNDLE:-${NODE_EXTRA_CA_CERTS:-}}}
if [ -n "$bundle" ] && [ -r "$bundle" ]; then
  key=$(cksum < "$bundle" | tr ' ' '-')
  store=$home/trust-$key.p12
  if [ ! -f "$store" ]; then
    "$java" "$home/__TRUST__" "$bundle" "$store" >/dev/null 2>&1 || true
  fi
  if [ -f "$store" ]; then
    options="$options -Djavax.net.ssl.trustStore=$store"
    options="$options -Djavax.net.ssl.trustStoreType=PKCS12"
    options="$options -Djavax.net.ssl.trustStorePassword=__PASSWORD__"
  fi
fi

mkdir -p "$home/.m2"

# Downloaded artifacts are shared with the person's own tools: the extension
# grants that directory to this run and names it here, and the JVM reaches it
# through the Maven home this preamble owns.
repository=${__REPOSITORY__:-}
if [ -n "$repository" ]; then
  mkdir -p "$repository"
  if [ ! -e "$home/.m2/repository" ]; then
    ln -s "$repository" "$home/.m2/repository" 2>/dev/null || true
  fi
fi
umask 077
{
  echo '<settings>'
  if [ -n "$repository" ]; then
    echo "  <localRepository>$repository</localRepository>"
  fi
  echo '  <proxies>'
  echo '    <proxy>'
  echo '      <id>vis-sandbox</id>'
  echo '      <active>true</active>'
  echo '      <protocol>http</protocol>'
  echo "      <host>$host</host>"
  echo "      <port>$port</port>"
  if [ -n "$user" ]; then
    echo "      <username>$user</username>"
    echo "      <password>$password</password>"
  fi
  echo '      <nonProxyHosts>localhost|127.0.0.1</nonProxyHosts>'
  echo '    </proxy>'
  echo '  </proxies>'
  echo '</settings>'
} > "$home/.m2/settings.xml"

JAVA_TOOL_OPTIONS="$options${JAVA_TOOL_OPTIONS:+ $JAVA_TOOL_OPTIONS}"
export JAVA_TOOL_OPTIONS
exec "$@"
"""
"""The preamble, with `__TRUST__` and `__PASSWORD__` still to fill in."""

TRUST = """// Turn a PEM certificate bundle into a PKCS12 trust store.
//
// Written by vis-lang-clojure. Edits are lost: the extension rewrites this file
// whenever its content differs from the release it ships.
import java.io.BufferedInputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.security.KeyStore;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.util.Collection;

public class VisTrust {
    public static void main(String[] arguments) throws Exception {
        CertificateFactory factory = CertificateFactory.getInstance("X.509");
        Collection<? extends Certificate> certificates;
        try (InputStream in = new BufferedInputStream(Files.newInputStream(Paths.get(arguments[0])))) {
            certificates = factory.generateCertificates(in);
        }
        KeyStore store = KeyStore.getInstance("PKCS12");
        store.load(null, null);
        int index = 0;
        for (Certificate certificate : certificates) {
            store.setCertificateEntry("vis-" + index++, certificate);
        }
        Path target = Paths.get(arguments[1]);
        Path written = Paths.get(arguments[1] + ".part");
        try (OutputStream out = Files.newOutputStream(written)) {
            store.store(out, "__PASSWORD__".toCharArray());
        }
        Files.move(written, target, StandardCopyOption.REPLACE_EXISTING);
        System.out.println(index);
    }
}
"""
"""The trust store helper, with `__PASSWORD__` still to fill in."""


def jail_home(boot):
    """Where the preamble keeps the Maven home it prepares.

    Args:
        boot: The boot directory.

    Returns:
        A directory inside `boot`, created when it is missing.
    """
    home = os.path.join(boot, HOME)
    os.makedirs(home, exist_ok=True)
    return home


def _written(path, content):
    """Write `content` to `path` unless it is already there."""
    try:
        with open(path, encoding="utf-8") as reading:
            if reading.read() == content:
                return path
    except OSError:
        pass
    part = f"{path}.part"
    with open(part, "w", encoding="utf-8") as writing:
        writing.write(content)
    os.replace(part, path)
    return path


def preamble(boot):
    """The preamble script, written into the boot directory when it is stale.

    Args:
        boot: The boot directory.

    Returns:
        Path of the script to run in front of a JVM command.
    """
    home = jail_home(boot)
    _written(
        os.path.join(home, TRUST_NAME), TRUST.replace("__PASSWORD__", TRUST_PASSWORD)
    )
    script = _written(
        os.path.join(home, PREAMBLE_NAME),
        PREAMBLE.replace("__TRUST__", TRUST_NAME)
        .replace("__PASSWORD__", TRUST_PASSWORD)
        .replace("__REPOSITORY__", REPOSITORY_VARIABLE),
    )
    os.chmod(script, 0o700)
    return script


def prepared(command, boot):
    """`command`, run behind the preamble.

    The preamble decides at run time whether anything needs preparing, so a
    command is wrapped whether or not this process is confined: only the
    sandbox knows, and only from inside it.

    Args:
        command: Program and arguments to run.
        boot: The boot directory.

    Returns:
        The command to run instead, the preamble first.

    Raises:
        ToolMissing: There is no shell to run the preamble.
    """
    shell = tool_path("sh", "A POSIX shell is required to start Clojure.")
    return (shell, preamble(boot), *command)
