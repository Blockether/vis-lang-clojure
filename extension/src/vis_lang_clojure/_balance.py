"""Conservative edit repair, ported from clj-parinferish.balance.

Candidates must pass the supplied full reader. Indentation is only evidence.
Keep unchanged code, line endings and edited spans. Use the original text to
restore lost delimiters, or to prove the narrow closer and quote exceptions.
"""

import re
import unicodedata
from array import array
from collections import Counter

from ._parinfer import JAVA_SPACE, _text, _units

DELIMITERS = "()[]{}"
OPENERS = "([{"
CLOSERS = dict(zip(OPENERS, ")]}", strict=True))
ALIGN_MAX_CELLS = 1_000_000
WINDOW_TRIES = 32


def _space(char):
    return char in "\t\n\v\f\r\x1c\x1d\x1e\x1f" or (
        unicodedata.category(char) in ("Zs", "Zl", "Zp")
        and char not in "\u00a0\u2007\u202f"
    )


def _blank(source):
    return all(_space(char) for char in source)


def _trimr(source):
    end = len(source)
    while end and _space(source[end - 1]):
        end -= 1
    return source[:end]


def _split_lines(source):
    lines = re.split(r"\r?\n", source)
    if source:
        while lines and not lines[-1]:
            lines.pop()
    return lines


def _terminated_lines(source):
    return re.findall(r"[^\n]*\n|[^\n]+", source)


def _line_ending(line):
    return "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""


def _skeleton(source):
    return "".join(char for char in source if char not in DELIMITERS + JAVA_SPACE)


def _delimiters(source):
    return "".join(char for char in source if char in DELIMITERS)


def _undelimited(source):
    return "".join(char for char in source if char not in DELIMITERS)


def _scan(source):
    stack, surplus = [], []
    line = 1
    opened = None
    comment = escaped = False
    for index, char in enumerate(source):
        if escaped:
            escaped = False
        elif opened is not None:
            if char == "\\":
                escaped = True
            elif char == '"':
                opened = None
        elif comment:
            comment = char != "\n"
        elif char == "\\":
            escaped = True
        elif char == '"':
            opened = line
        elif char == ";":
            comment = True
        elif char in OPENERS:
            stack.append(CLOSERS[char])
        elif char in DELIMITERS:
            if stack and stack[-1] == char:
                stack.pop()
            elif len(surplus) < 2:
                surplus.append((char, index, line))
        if char == "\n":
            line += 1
    return stack, opened, surplus


def _unterminated_string(source):
    return _scan(source)[1]


def _open_stack(source):
    stack, opened, surplus = _scan(source)
    return stack if opened is None and not surplus else None


def _open_string_why(source):
    line = _unterminated_string(source)
    if line is not None:
        return (
            f"no delimiter repair is possible: line {line} opens a string that is never "
            "closed, and a repair only puts back `()[]{}`"
        )
    return None


def _subsequence(before, after):
    remaining = iter(after)
    return all(any(char == other for other in remaining) for char in before)


def _surplus(before, after):
    counts = Counter(before) - Counter(after)
    return "".join(char * counts[char] for char in sorted(counts))


def _direction_why(source, candidate, subject):
    before, after = _delimiters(source), _delimiters(candidate)
    if _subsequence(after, before):
        return (
            f"the delimiter repair would delete `{_surplus(before, after)}` {subject}: "
            "it closes more than it opens, or an opener was lost"
        )
    return f"the delimiter repair would move or retype a delimiter {subject}"


def _middles(original, source):
    before, after = _split_lines(original), _split_lines(source)
    head = 0
    while head < min(len(before), len(after)) and before[head] == after[head]:
        head += 1
    tail = 0
    while (
        tail < min(len(before), len(after)) - head
        and before[-tail - 1] == after[-tail - 1]
    ):
        tail += 1
    return head, before[head : len(before) - tail], after[head : len(after) - tail]


def changed_span(before, after):
    """Return the inclusive changed line range in after, or None for equal text."""
    if before == after:
        return None
    head, _, middle = _middles(before, after)
    end = head + len(middle)
    return min(head + 1, max(1, end)), end


def _inside(line, spans):
    return any(start <= line <= end for start, end in spans)


def _excerpt(line):
    trimmed = _trimr(line)
    start = 0
    while start < len(trimmed) and _space(trimmed[start]):
        start += 1
    trimmed = trimmed[start:]
    return trimmed[:55] + "…" if len(trimmed) > 56 else trimmed


def _delimiter_note(number, before, after):
    before, now = _delimiters(before), _delimiters(after)
    added, removed = _surplus(now, before), _surplus(before, now)
    note = f"line {number}"
    if added:
        note += f" added `{added}`"
    if removed:
        note += f" removed `{removed}`"
    return note + f" → `{_excerpt(after)}`"


def _align(before, after):
    rows, cols = len(before), len(after)
    if not rows or not cols or rows * cols > ALIGN_MAX_CELLS:
        return None
    width = cols + 1
    table = array("I", [0]) * ((rows + 1) * width)
    for i in range(rows - 1, -1, -1):
        base, below = i * width, (i + 1) * width
        for j in range(cols - 1, -1, -1):
            table[base + j] = (
                table[below + j + 1] + 1
                if before[i] == after[j]
                else max(table[below + j], table[base + j + 1])
            )
    i = j = 0
    pairs = []
    while i < rows and j < cols:
        if before[i] == after[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        elif table[(i + 1) * width + j] >= table[i * width + j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _lcs_length(before, after):
    if not before or not after or len(before) * len(after) > ALIGN_MAX_CELLS:
        return 0
    row = [0] * (len(after) + 1)
    for first in reversed(before):
        diagonal = 0
        for j in range(len(after) - 1, -1, -1):
            previous = row[j]
            row[j] = diagonal + 1 if first == after[j] else max(row[j + 1], previous)
            diagonal = previous
    return row[0]


def _reseat_line(replaced, wrote):
    pairs = _align(replaced, wrote)
    if pairs is None:
        return None
    was_from = wrote_from = 0
    seats = []
    for was_at, wrote_at in pairs + [(len(replaced), len(wrote))]:
        gap = replaced[was_from:was_at]
        dropped = _delimiters(gap)
        seat = (
            wrote_from
            if dropped and all(char in OPENERS for char in dropped)
            else wrote_at
        )
        if all(char in DELIMITERS or _space(char) for char in gap):
            seats.extend((seat, char) for char in dropped)
        was_from, wrote_from = was_at + 1, wrote_at + 1
    if not seats:
        return None
    parts, start = [], 0
    for index, char in seats:
        parts.extend((wrote[start:index], char))
        start = index
    parts.append(wrote[start:])
    return "".join(parts)


def _similar(before, after):
    first, second = _skeleton(before), _skeleton(after)
    longer = max(len(first), len(second))
    return longer > 0 and 2 * _lcs_length(first, second) > longer


def _paired_lines(original, source):
    head, before, after = _middles(original, source)
    anchors = (
        _align(
            [_skeleton(line) for line in before], [_skeleton(line) for line in after]
        )
        or []
    )
    was_at = now_at = 0
    indexes = []
    for was_to, now_to in anchors + [(len(before), len(after))]:
        gap = was_to - was_at
        if gap > 0 and gap == now_to - now_at:
            indexes.extend((was_at + k, now_at + k, False) for k in range(gap))
        if was_to < len(before):
            indexes.append((was_to, now_to, True))
        was_at, now_at = was_to + 1, now_to + 1
    return [
        (head + now + 1, before[was], after[now], same)
        for was, now, same in indexes
        if not _blank(_skeleton(before[was]))
        and (same or _similar(before[was], after[now]))
    ]


def _reseat(pairs, source):
    lines = _terminated_lines(source)
    changed = False
    for line, replaced, wrote, _ in pairs:
        if replaced != wrote:
            seated = _reseat_line(replaced, wrote)
            if seated is not None:
                lines[line - 1] = seated + _line_ending(lines[line - 1])
                changed = True
    return "".join(lines) if changed else None


def _written_lines(original, source, lines):
    if original is not None:
        head, _, middle = _middles(original, source)
        return head + 1, head + len(middle)
    return 1, len(lines)


def _span_seat(spans, lines, written):
    if len(spans) != 1:
        return None
    start, end = written
    return next(
        (
            i
            for i in range(len(lines) - 1, -1, -1)
            if start <= i + 1 <= end and _inside(i + 1, spans) and not _blank(lines[i])
        ),
        None,
    )


def _append_at(lines, index, text):
    current = lines[index]
    ending = _line_ending(current)
    body = current[: len(current) - len(ending)]
    code = _trimr(body)
    result = list(lines)
    result[index] = code + text + body[len(code) :] + ending
    return "".join(result)


def _seat_closers(lines, start, index, witness):
    before = _open_stack("".join(lines[: start - 1]))
    through = _open_stack("".join(lines[: index + 1]))
    if (
        before is None
        or through is None
        or len(before) >= len(through)
        or before != through[: len(before)]
    ):
        return None
    opened = len(through) - len(before)
    for count in range(1, opened + 1) if witness else [opened]:
        closers = "".join(reversed(through[-count:]))
        if _open_stack(_append_at(lines, index, closers)) == []:
            return closers
    return None


def _closed_at_tail(spans, original, source):
    lines = _terminated_lines(source)
    written = _written_lines(original, source, lines)
    index = _span_seat(spans, lines, written)
    if index is not None:
        missing = _seat_closers(lines, written[0], index, original is not None)
        if missing is not None:
            return _append_at(lines, index, missing)
    return None


def _requoted(spans, original, source):
    if original is not None and _unterminated_string(source) is not None:
        lines = _terminated_lines(source)
        _, replaced, _ = _middles(original, source)
        index = _span_seat(spans, lines, _written_lines(original, source, lines))
        tail = next((line for line in reversed(replaced) if not _blank(line)), "")
        if (
            index is not None
            and _trimr(tail).endswith('"')
            and not _trimr(lines[index]).endswith('"')
        ):
            return _append_at(lines, index, '"')
    return None


def _substitution(replaced, wrote):
    before, now = _delimiters(replaced), _delimiters(wrote)
    i = j = 0
    skipped = None
    while j < len(now):
        if i == len(before):
            return now[j], skipped
        if before[i] == now[j]:
            i += 1
            j += 1
            skipped = None
        else:
            skipped = skipped or before[i]
            i += 1
    return None


def _substitution_why(line, change, repaired, subject):
    typed, had = change
    previous = (
        f", where the text it replaced had `{had}`"
        if had
        else ", one more than the text it replaced has"
    )
    return (
        f"the delimiter repair would close `{typed}` {subject} on line {line}{previous}"
        ": that delimiter was retyped or added, not omitted, and closing it regroups the"
        f" line into `{_excerpt(repaired)}`"
    )


def _invention(replaced, repaired):
    before, after = _delimiters(replaced), _delimiters(repaired)
    return None if _subsequence(after, before) else _surplus(after, before)


def _invention_why(line, added, replaced):
    return (
        f"a delimiter repair exists but it adds `{added}` to line {line}, whose code this edit "
        f"did not change — the text it replaced was `{_excerpt(replaced)}` and never had that "
        "delimiter, so what this call omitted is on another line"
    )


def _changed_lines(before, after):
    return (
        [
            i + 1
            for i, (first, second) in enumerate(zip(before, after, strict=True))
            if first != second
        ]
        if len(before) == len(after)
        else None
    )


def _single_added_closer(original, source, spans):
    before, after = _split_lines(original), _split_lines(source)
    if len(before) != len(after):
        return None
    changed = [
        (i + 1, _delimiters(first), _delimiters(second))
        for i, (first, second) in enumerate(zip(before, after, strict=True))
        if _delimiters(first) != _delimiters(second)
    ]
    if len(changed) == 1:
        line, was, now = changed[0]
        added = _surplus(now, was)
        if (
            _inside(line, spans)
            and _subsequence(was, now)
            and len(now) == len(was) + 1
            and len(added) == 1
            and added in ")]}"
        ):
            return added
    return None


def _relocated_closer(source, original, spans, parses_clean):
    if original is None or not parses_clean(original) or parses_clean(source):
        return None
    added = _single_added_closer(original, source, spans)
    if added is None:
        return None
    stack, opened, surplus = _scan(source)
    if stack or opened is not None or len(surplus) != 1:
        return None
    delimiter, index, line = surplus[0]
    before, now = _split_lines(original), _split_lines(source)
    if (
        added == delimiter
        and not _inside(line, spans)
        and line <= len(before)
        and before[line - 1] == now[line - 1]
    ):
        candidate = source[:index] + source[index + 1 :]
        if parses_clean(candidate):
            after = _split_lines(candidate)
            return {
                "ok?": True,
                "content": candidate,
                "notes": [_delimiter_note(line, now[line - 1], after[line - 1])],
            }
    return None


def _verdict(source, candidate, spans, subject, pairs, parses_clean):
    if not isinstance(candidate, str) or candidate == source:
        why = _open_string_why(source) or "no delimiter repair was found"
    elif not parses_clean(candidate):
        why = (
            _open_string_why(source)
            or "a delimiter repair was found but it still would not parse"
        )
    elif _skeleton(source) != _skeleton(candidate):
        why = "the delimiter repair would rewrite code, not delimiters"
    elif not _subsequence(_delimiters(source), _delimiters(candidate)):
        why = _direction_why(source, candidate, subject)
    elif source.endswith("\n") != candidate.endswith("\n"):
        why = "the delimiter repair would change the file's final newline"
    else:
        before, after = _split_lines(source), _split_lines(candidate)
        changed = _changed_lines(before, after)
        outside = [line for line in changed or [] if not _inside(line, spans)]
        paired = {
            line: (replaced, wrote) for line, replaced, wrote, same in pairs if same
        }
        typed = invented = None
        for line in changed or []:
            if line in paired:
                replaced, wrote = paired[line]
                substitution = _substitution(replaced, wrote)
                invention = _invention(replaced, after[line - 1])
                if typed is None and substitution is not None:
                    typed = line, substitution
                if invented is None and invention is not None:
                    invented = line, invention, replaced
        if changed is None:
            why = "the delimiter repair would add or drop lines"
        elif outside:
            why = (
                f"a delimiter repair exists but it changes line {outside[0]}, "
                "outside the lines this call edited"
            )
        elif _undelimited(source) != _undelimited(candidate):
            why = (
                f"the delimiter repair would change whitespace {subject}: it re-indents or "
                "re-ends lines instead of only putting back the delimiters that were omitted"
            )
        elif typed:
            line, substitution = typed
            why = _substitution_why(line, substitution, after[line - 1], subject)
        elif invented:
            why = _invention_why(*invented)
        else:
            return {
                "ok?": True,
                "content": candidate,
                "notes": [
                    _delimiter_note(line, before[line - 1], after[line - 1])
                    for line in changed
                ],
            }
    return {"ok?": False, "why": why}


def _unquoted(parses_clean, spans, original, source):
    if original is None or source.count('"') != original.count('"') + 1:
        return None
    lines = _terminated_lines(source)
    candidates = []
    for index, current in enumerate(lines):
        ending = _line_ending(current)
        body = current[: len(current) - len(ending)]
        trimmed = _trimr(body)
        if _inside(index + 1, spans) and trimmed.endswith('"'):
            changed = list(lines)
            changed[index] = trimmed[:-1] + body[len(trimmed) :] + ending
            candidate = "".join(changed)
            if candidate not in candidates:
                candidates.append(candidate)
    repaired = [
        candidate
        for candidate in candidates
        if parses_clean(candidate) and _unterminated_string(candidate) is None
    ]
    return repaired[0] if len(repaired) == 1 else None


def _quote_verdict(source, candidate, spans, parses_clean, action):
    if (
        not isinstance(candidate, str)
        or not parses_clean(candidate)
        or _unterminated_string(candidate) is not None
    ):
        return None
    before, after = _split_lines(source), _split_lines(candidate)
    changed = _changed_lines(before, after)
    if changed is None or len(changed) != 1:
        return None
    line = changed[0]
    first, second = before[line - 1], after[line - 1]
    if action == "removed":
        first, second = second, first
    if (
        source.endswith("\n") == candidate.endswith("\n")
        and _inside(line, spans)
        and first.replace('"', "") == second.replace('"', "")
        and second.count('"') == first.count('"') + 1
    ):
        return {
            "ok?": True,
            "content": candidate,
            "notes": [f'line {line} {action} `"` → `{_excerpt(after[line - 1])}`'],
        }
    return None


def _balancer_window(source, lines, offsets, spans):
    if not spans:
        return None
    start = min(span[0] for span in spans) - 1
    end = max(span[1] for span in spans) - 1
    count = len(lines)

    def own_form(index):
        return bool(lines[index]) and lines[index][0] not in JAVA_SPACE

    heads, ends = [], []
    tries = 0
    for index in range(min(start, count - 1), -1, -1):
        if own_form(index):
            tries += 1
            if _open_stack(source[: offsets[index]]) == []:
                heads.append(index)
                if len(heads) == 2:
                    break
            if tries == WINDOW_TRIES:
                break
    tries = 0
    for index in range(end + 1, count + 1):
        if index == count or own_form(index):
            tries += 1
            if _open_stack(source[offsets[index] :]) == []:
                ends.append(index)
                if len(ends) == 2:
                    break
            if tries == WINDOW_TRIES:
                break
    if heads and ends and ends[-1] - heads[-1] < count:
        return heads[-1], ends[-1]
    return None


def _balancer_answer(balancer, source, spans):
    lines = _terminated_lines(source)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    window = _balancer_window(source, lines, offsets, spans)
    start, end = (
        (offsets[window[0]], offsets[window[1]]) if window else (0, len(source))
    )
    try:
        fixed = balancer(source[start:end])
    except Exception:
        return None
    return source[:start] + fixed + source[end:] if isinstance(fixed, str) else None


def rebalance(
    *,
    source,
    original=None,
    spans=(),
    balancer,
    parses_clean,
    subject="this edit wrote",
):
    """Return a proven repair or a refusal; None means no balancer is available."""
    if not callable(balancer):
        return None
    raw_balancer, raw_parser = balancer, parses_clean

    def balance_units(text):
        result = raw_balancer(_text(text))
        return _units(result) if isinstance(result, str) else None

    def parse_units(text):
        return raw_parser(_text(text))

    source = _units(source)
    original = _units(original) if isinstance(original, str) else None
    pairs = _paired_lines(original, source) if original is not None else []

    def verdict(candidate):
        return _verdict(
            source, candidate, spans, subject or "this edit wrote", pairs, parse_units
        )

    def attempt():
        if pairs:
            seated = verdict(_reseat(pairs, source))
            if seated["ok?"]:
                return seated
        relocated = _relocated_closer(source, original, spans, parse_units)
        if relocated:
            return relocated
        asked = verdict(_balancer_answer(balance_units, source, spans))
        if asked["ok?"]:
            return asked
        tailed = verdict(_closed_at_tail(spans, original, source))
        if tailed["ok?"]:
            return tailed
        return (
            _quote_verdict(
                source,
                _unquoted(parse_units, spans, original, source),
                spans,
                parse_units,
                "removed",
            )
            or _quote_verdict(
                source, _requoted(spans, original, source), spans, parse_units, "added"
            )
            or asked
        )

    result = attempt()
    return {
        key: _text(value)
        if isinstance(value, str)
        else [_text(note) for note in value]
        if key == "notes"
        else value
        for key, value in result.items()
    }
