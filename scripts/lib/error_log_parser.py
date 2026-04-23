from __future__ import annotations

import re
from pathlib import Path
from typing import Any


ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
ASSERT_RE = re.compile(r"Assertion failed at (?P<path>.+?):(?P<line>\d+)\.", re.IGNORECASE)
DIFFTEST_MISMATCH_RE = re.compile(r"different at pc\s*=\s*(?P<pc>0x[0-9a-fA-F]+)", re.IGNORECASE)
COMMIT_LINE_RE = re.compile(
    r"\[(?P<idx>\d+)\]\s+commit\s+pc\s+(?P<pc>[0-9a-fA-F]+)\s+inst\s+(?P<inst>[0-9a-fA-F]+)(?P<rest>.*)"
)
ABORT_RE = re.compile(r"ABORT at pc\s*=\s*(?P<pc>0x[0-9a-fA-F]+)", re.IGNORECASE)
NO_COMMIT_RE = re.compile(r"No instruction of core \d+ commits for \d+ cycles, maybe get stuck", re.IGNORECASE)
DIFFTEST_ENABLED_RE = re.compile(r"Difftest enabled", re.IGNORECASE)
ASSERT_STOP_RE = re.compile(r"There might be some assertion failed", re.IGNORECASE)


def _normalize_line(line: str) -> str:
    return ANSI_ESCAPE_RE.sub("", line).strip()


def _append_unique(items: list[str], value: str | None) -> None:
    if value and value not in items:
        items.append(value)


def _extract_mismatch_commit(lines: list[str], mismatch_pc: str | None) -> dict[str, Any] | None:
    if mismatch_pc is None:
        return None
    target_pc = int(mismatch_pc, 16)
    candidate = None
    for line in lines:
        match = COMMIT_LINE_RE.search(line)
        if match is None or int(match.group("pc"), 16) != target_pc:
            continue
        candidate = {
            "commit_idx": int(match.group("idx")),
            "pc": f"0x{int(match.group('pc'), 16):x}",
            "inst_hex": match.group("inst").lower(),
            "trace_line": line,
        }
    return candidate


def summarize_error_log(error_log: Path) -> dict[str, Any]:
    text = error_log.read_text(encoding="utf-8", errors="ignore")
    lines = [_normalize_line(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    assertion_match = None
    assertion_line = None
    assertion_stop_line = None
    mismatch_line = None
    mismatch_pc = None
    abort_line = None
    abort_pc = None
    stuck_line = None
    difftest_enabled = False

    for line in lines:
        if assertion_match is None:
            match = ASSERT_RE.search(line)
            if match is not None:
                assertion_match = match
                assertion_line = line
        if assertion_stop_line is None and ASSERT_STOP_RE.search(line):
            assertion_stop_line = line
        if mismatch_line is None:
            match = DIFFTEST_MISMATCH_RE.search(line)
            if match is not None:
                mismatch_line = line
                mismatch_pc = match.group("pc")
        if abort_line is None:
            match = ABORT_RE.search(line)
            if match is not None:
                abort_line = line
                abort_pc = match.group("pc")
        if stuck_line is None and NO_COMMIT_RE.search(line):
            stuck_line = line
        if not difftest_enabled and DIFFTEST_ENABLED_RE.search(line):
            difftest_enabled = True

    evidence_lines: list[str] = []
    assert_site = None
    mismatch_commit = _extract_mismatch_commit(lines, mismatch_pc)
    bug_type = "unknown"
    confidence = "low"

    if assertion_match is not None:
        bug_type = "assert_error"
        confidence = "high"
        assert_path = assertion_match.group("path")
        assert_site = {
            "path": assert_path,
            "line": int(assertion_match.group("line")),
            "module": Path(assert_path).stem,
        }
        _append_unique(evidence_lines, assertion_line)
        _append_unique(evidence_lines, assertion_stop_line)
        _append_unique(evidence_lines, abort_line)
    elif mismatch_line is not None:
        bug_type = "difftest_error"
        confidence = "high"
        _append_unique(evidence_lines, stuck_line)
        _append_unique(evidence_lines, mismatch_line)
        _append_unique(evidence_lines, abort_line)
    elif stuck_line is not None and (abort_line is not None or difftest_enabled):
        bug_type = "difftest_error"
        confidence = "medium"
        _append_unique(evidence_lines, stuck_line)
        _append_unique(evidence_lines, abort_line)
    elif abort_line is not None and difftest_enabled:
        bug_type = "difftest_error"
        confidence = "low"
        _append_unique(evidence_lines, abort_line)
    else:
        _append_unique(evidence_lines, assertion_line)
        _append_unique(evidence_lines, mismatch_line)
        _append_unique(evidence_lines, abort_line)

    return {
        "path": str(error_log.resolve()),
        "bug_type": bug_type,
        "confidence": confidence,
        "assert_site": assert_site,
        "mismatch_pc": mismatch_pc,
        "mismatch_commit": mismatch_commit,
        "abort_pc": abort_pc,
        "stuck_hint": stuck_line,
        "evidence_lines": evidence_lines[:4],
    }
