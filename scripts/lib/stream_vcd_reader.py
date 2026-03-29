from __future__ import annotations

from collections.abc import Iterable, Iterator


def iter_vcd_changes(
    lines: Iterable[str],
    *,
    watched_ids: set[str],
) -> Iterator[tuple[int, str, str]]:
    if not watched_ids:
        return

    cur_time = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line[0] == "#":
            cur_time = int(line[1:].strip() or "0")
            continue

        c0 = line[0]
        if c0 in "01xXzZ":
            value = c0.lower()
            vid = line[1:].strip()
            if vid in watched_ids:
                yield (cur_time, vid, value)
            continue

        if c0 in "bBrR":
            parts = line.split()
            if len(parts) < 2:
                continue
            value = parts[0][1:]
            vid = parts[1]
            if vid in watched_ids:
                yield (cur_time, vid, value)
