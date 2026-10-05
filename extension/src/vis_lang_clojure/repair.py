"""Propose local structural repairs, then require that the source reads."""

from dataclasses import dataclass

from ._balance import rebalance
from ._parinfer import parse


@dataclass(frozen=True)
class RepairResult:
    source: str
    notes: tuple[str, ...]


def repair_source(
    source, *, original=None, spans=(), parses_clean, subject="this edit wrote"
):
    """Repair invalid source within edited lines, without formatting or writing it."""
    if parses_clean(source):
        return None
    repaired, _ = _rebalanced(source, original, spans, parses_clean, subject)
    return repaired


def repair_code(source, *, parses_clean):
    """Repair the delimiters of code to evaluate, or say why no repair is safe.

    Every line can change: no earlier version shows which lines the author wrote.

    Returns:
        The `RepairResult` and "", or None and the reason that no repair is safe.
    """
    lines = ((1, source.count("\n") + 1),)
    return _rebalanced(source, None, lines, parses_clean, "in this code")


def _rebalanced(source, original, spans, parses_clean, subject):
    result = rebalance(
        source=source,
        original=original,
        spans=spans,
        parses_clean=parses_clean,
        balancer=lambda text: parse(text, mode="indent").text,
        subject=subject,
    )
    if result and result["ok?"] and result["content"] != source and result["notes"]:
        return RepairResult(result["content"], tuple(result["notes"])), ""
    return None, (result or {}).get("why") or "no delimiter repair was found"
