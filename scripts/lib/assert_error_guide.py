from __future__ import annotations

import re
from pathlib import Path
from typing import Any


SCALA_ASSERT_RE = re.compile(r"Assertion failed at (?P<file>[^:\n]+\.scala):(?P<line>\d+)")
TRIGGER_RE = re.compile(r"^\s*if\s*\((?P<expr>.+)\)\s*begin\s*$")
IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*(?:\[[^\]]+\])?")


def _dedupe_rows(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        key = tuple(row.get(k) for k in keys)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _search_roots(scala_root: Path) -> list[Path]:
    roots = [scala_root]
    parents = list(scala_root.parents)
    if len(parents) >= 4:
        roots.append(parents[3])
    deduped: list[Path] = []
    for root in roots:
        if root not in deduped:
            deduped.append(root)
    return deduped


def _resolve_scala_candidates(scala_root: Path, scala_file: str) -> list[str]:
    matches: set[str] = set()
    for root in _search_roots(scala_root):
        for path in root.rglob(scala_file):
            matches.add(str(path.resolve()))
    matches = {path for path in matches if "/.git/" not in path}
    matches = {path for path in matches if "/build/" not in path or path.endswith(scala_file)}
    return sorted(matches)


def _extract_trigger_operands(trigger_line: str | None) -> list[str]:
    if not trigger_line:
        return []
    match = TRIGGER_RE.match(trigger_line)
    expr = match.group("expr") if match else trigger_line
    blacklist = {"if", "begin"}
    operands = [token for token in IDENT_RE.findall(expr) if token not in blacklist]
    deduped: list[str] = []
    for token in operands:
        if token not in deduped:
            deduped.append(token)
    return deduped


def collect_assert_error_context(*, assert_path: Path, assert_line: int, scala_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "assert_path": str(assert_path.resolve()),
        "assert_line": assert_line,
        "verilog_module": assert_path.stem,
    }
    if not assert_path.exists():
        result["status"] = "missing-verilog"
        result["message"] = f"asserted Verilog file does not exist: {assert_path}"
        return result

    lines = assert_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = max(1, assert_line - 10)
    end = min(len(lines), assert_line + 10)
    context_lines = [{"line": lineno, "text": lines[lineno - 1]} for lineno in range(start, end + 1)]

    trigger_line = None
    trigger_line_no = None
    for row in reversed(context_lines):
        if row["line"] > assert_line:
            continue
        text = row["text"].strip()
        if not text.startswith("if "):
            continue
        if "ASSERT_VERBOSE_COND_" in text or "STOP_COND_" in text:
            continue
        if row["line"] == assert_line:
            continue
        trigger_line = row["text"]
        trigger_line_no = row["line"]
        break

    scala_sites: list[dict[str, Any]] = []
    for row in context_lines:
        for match in SCALA_ASSERT_RE.finditer(row["text"]):
            scala_file = match.group("file")
            scala_line = int(match.group("line"))
            scala_sites.append(
                {
                    "scala_file": scala_file,
                    "scala_line": scala_line,
                    "verilog_context_line": row["line"],
                    "resolved_paths": _resolve_scala_candidates(scala_root, scala_file),
                }
            )

    scala_sites = _dedupe_rows(scala_sites, ("scala_file", "scala_line"))
    primary_scala_site = None
    if scala_sites:
        primary_scala_site = min(
            scala_sites,
            key=lambda site: abs(int(site["verilog_context_line"]) - int(assert_line)),
        )

    result["status"] = "ok"
    result["context_lines"] = context_lines
    result["trigger_line"] = trigger_line
    result["trigger_line_no"] = trigger_line_no
    result["trigger_operands"] = _extract_trigger_operands(trigger_line)
    result["scala_sites"] = scala_sites
    result["primary_scala_site"] = primary_scala_site
    return result
