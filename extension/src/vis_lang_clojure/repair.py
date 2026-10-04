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
    result = rebalance(
        source=source,
        original=original,
        spans=spans,
        parses_clean=parses_clean,
        balancer=lambda text: parse(text, mode="indent").text,
        subject=subject,
    )
    if result and result["ok?"] and result["content"] != source and result["notes"]:
        return RepairResult(result["content"], tuple(result["notes"]))
    return None
