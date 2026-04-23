#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


from lib.build_debug_packet import build_debug_packet_from_manifest, query_signal_value_from_manifest
from lib.assert_error_guide import collect_assert_error_context
from lib.build_rtl_authority import build_rtl_authority
from lib.direct_fst_query import build_debug_packet_from_fst, query_signal_value_from_fst
from lib.error_log_parser import summarize_error_log
from lib.ingest_waveform import stream_waveform_store
from lib.wave_metadata_cache import build_wave_metadata_cache
from lib.waveform_formats import detect_waveform_format
from disassembler import disassemble_one


WARN_WAVEFORM_BYTES = 1 * 1024 * 1024 * 1024
WARN_TREE_BYTES = 512 * 1024 * 1024
WARN_RTL_FILES = 1000
ARTIFACTS_DIR = SKILL_DIR / "artifacts"
DEFAULT_DIFFTEST_DISASSEMBLER = SCRIPT_DIR / "disassembler.py"
DEFAULT_DIFFTEST_DISASSEMBLY_NAME = "disassembly.txt"
DEFAULT_DIFFTEST_SIGNAL_GUIDE_NAME = "waveform_search_signals.txt"
DEFAULT_DIFFTEST_SIGNAL_GUIDE_VERSION = "2026-04-10-md-v2"
DEFAULT_ASSERT_GUIDE_NAME = "assert_debug_guide.md"
DEFAULT_ASSERT_GUIDE_VERSION = "2026-04-10-md-v1"


def _format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{size}B"


def _dir_stats(root: Path) -> tuple[int, int]:
    total = 0
    files = 0
    for path in root.rglob("*"):
        if path.is_file():
            files += 1
            total += path.stat().st_size
    return files, total


def _validate(path: Path, kind: str) -> None:
    if not path.exists():
        raise SystemExit(f"{kind} does not exist: {path}")


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _tree_signature(root: Path) -> dict[str, Any]:
    file_count = 0
    total_bytes = 0
    max_mtime_ns = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        stat = path.stat()
        file_count += 1
        total_bytes += stat.st_size
        if stat.st_mtime_ns > max_mtime_ns:
            max_mtime_ns = stat.st_mtime_ns
    return {
        "path": str(root.resolve()),
        "file_count": file_count,
        "total_bytes": total_bytes,
        "max_mtime_ns": max_mtime_ns,
    }


def _fingerprint(parts: dict[str, Any]) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _authority_cache_meta(*, rtl_root: Path, top: str) -> dict[str, Any]:
    return {
        "kind": "rtl_authority",
        "top": top,
        "rtl_root": _tree_signature(rtl_root),
    }


def _wave_cache_meta(*, vcd: Path, window_len: int) -> dict[str, Any]:
    return {
        "kind": "wave_db",
        "window_len": window_len,
        "waveform": {
            "format": detect_waveform_format(vcd),
            "file": _file_signature(vcd),
        },
    }


def _wave_meta_cache_meta(*, waveform: Path) -> dict[str, Any]:
    return {
        "kind": "wave_meta",
        "waveform": {
            "format": detect_waveform_format(waveform),
            "file": _file_signature(waveform),
        },
    }


def _default_authority_out(*, rtl_root: Path, top: str) -> Path:
    meta = _authority_cache_meta(rtl_root=rtl_root, top=top)
    return ARTIFACTS_DIR / "authority" / _fingerprint(meta)


def _default_wave_out(*, vcd: Path, window_len: int) -> Path:
    meta = _wave_cache_meta(vcd=vcd, window_len=window_len)
    return ARTIFACTS_DIR / "wave_db" / _fingerprint(meta)


def _default_wave_meta_out(*, waveform: Path) -> Path:
    meta = _wave_meta_cache_meta(waveform=waveform)
    return ARTIFACTS_DIR / "wave_meta" / _fingerprint(meta)


def _default_packet_out(*, wave_out: Path, window_id: str) -> Path:
    packet_key = _fingerprint({"kind": "packet", "wave_out": str(wave_out.resolve())})
    return ARTIFACTS_DIR / "packets" / packet_key / f"packet_{window_id}.json"


def _cache_meta_path(out_dir: Path) -> Path:
    return out_dir / "cache_meta.json"


def _cache_matches(out_dir: Path, expected: dict[str, Any], required_files: list[str]) -> bool:
    meta_path = _cache_meta_path(out_dir)
    if not meta_path.exists():
        return False
    missing = [name for name in required_files if not (out_dir / name).exists()]
    if missing:
        return False
    try:
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return existing == expected


def _store_cache_meta(out_dir: Path, meta: dict[str, Any]) -> None:
    _write_json(_cache_meta_path(out_dir), meta)


def _self_cmd() -> str:
    return "python scripts/hw_debug_cli.py"


def _default_difftest_disassembly_out(*, error_log: Path) -> Path:
    return error_log.parent / DEFAULT_DIFFTEST_DISASSEMBLY_NAME


def _default_signal_guide_out(*, error_log: Path) -> Path:
    return error_log.parent / DEFAULT_DIFFTEST_SIGNAL_GUIDE_NAME


def _default_assert_guide_out(*, error_log: Path) -> Path:
    return error_log.parent / DEFAULT_ASSERT_GUIDE_NAME


def _signal_guide_marker(*, bug_type: str) -> str:
    return f"<!-- waveform-search-signals: {DEFAULT_DIFFTEST_SIGNAL_GUIDE_VERSION}; bug_type={bug_type} -->"


def _permission_denied_message(path: Path) -> str:
    return (
        f"could not write {path}. Ask the user to allow writing to that directory and rerun. "
        "No fallback file was created."
    )


def _is_permission_denied(*, exc: OSError | None = None, text: str | None = None) -> bool:
    if exc is not None and exc.errno in {13, 30}:
        return True
    if text is None:
        return False
    lowered = text.lower()
    return "permission denied" in lowered or "read-only file system" in lowered


def _run_difftest_disassembler(*, error_log: Path) -> dict[str, Any]:
    out_path = _default_difftest_disassembly_out(error_log=error_log)
    command = [
        sys.executable,
        str(DEFAULT_DIFFTEST_DISASSEMBLER),
        str(error_log),
        "--output",
        str(out_path),
    ]
    result: dict[str, Any] = {
        "script_path": str(DEFAULT_DIFFTEST_DISASSEMBLER),
        "output_path": str(out_path),
        "command": " ".join(shlex.quote(part) for part in command),
    }

    if not DEFAULT_DIFFTEST_DISASSEMBLER.exists():
        result["status"] = "missing-script"
        result["message"] = "configured difftest disassembler script does not exist"
        return result

    freshness_floor = error_log.stat().st_mtime_ns
    if out_path.exists() and out_path.stat().st_mtime_ns >= freshness_floor:
        result["status"] = "cache-hit"
        return result

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if stdout:
        result["stdout"] = stdout
    if stderr:
        result["stderr"] = stderr
    result["returncode"] = completed.returncode
    if completed.returncode == 0 and out_path.exists():
        result["status"] = "generated"
    elif _is_permission_denied(text=stderr):
        result["status"] = "permission-denied"
        result["message"] = _permission_denied_message(out_path)
    else:
        result["status"] = "failed"
    return result


def _decode_mismatch_instruction(mismatch_commit: dict[str, Any] | None) -> str | None:
    if mismatch_commit is None:
        return None
    try:
        return disassemble_one(int(mismatch_commit["pc"], 16), mismatch_commit["inst_hex"])
    except (FileNotFoundError, ValueError, KeyError):
        return None


def _render_difftest_signal_guide(
    *,
    error_log: Path,
    scala_root: Path,
    focus_scope: str | None,
    error_summary: dict[str, Any],
    mismatch_instr_asm: str | None,
) -> str:
    mismatch_commit = error_summary.get("mismatch_commit") or {}
    rob_scope = focus_scope or "TOP.SimTop.core.rob"
    ctrl_scope = "TOP.SimTop.core.ctrlBlock"
    source_files = [
        ("RobBundles", scala_root / "backend/rob/RobBundles.scala"),
        ("Rob", scala_root / "backend/rob/Rob.scala"),
        ("RobDeqPtrWrapper", scala_root / "backend/rob/RobDeqPtrWrapper.scala"),
        ("CtrlBlock", scala_root / "backend/CtrlBlock.scala"),
    ]
    signal_rows = [
        ("commitValid(i) / commit_v", "Commit lane i is valid in this cycle.", "Use it to find which ROB lane carries the mismatching instruction."),
        ("isCommit / commitEn", "ROB is in normal commit mode instead of walk or blocked state.", "Confirms whether the bad instruction is being retired normally."),
        ("robIdx(i) / deqPtrVec(i)", "ROB index for the commit lane or dequeue pointer.", "Tracks whether the pointer movement matches the instruction that should retire."),
        ("debug_pc / io.commits.info(i).debug_pc", "Committed instruction PC from ROB debug info.", "Primary key to align waveform evidence with the mismatching PC from simulator_out."),
        ("debug_instr / io.commits.info(i).debug_instr", "Committed instruction encoding from ROB debug info.", "Confirms the exact committed instruction when multiple lanes are active."),
        ("commit_w / rfWen / commitType", "Commit write-enable and instruction class metadata.", "Shows whether the retirement metadata matches the instruction semantics."),
        ("commitCnt", "Number of instructions retired in the cycle.", "Helps detect over-commit, under-commit, or commit-width edge cases."),
        ("flushOut.valid / needFlush", "ROB flush is being generated for the retiring instruction.", "Checks whether the instruction should have flushed instead of committed."),
        ("redirect.valid / redirectOutValid", "Redirect is active in the same or nearby cycles.", "Correlate redirect timing with the bad commit to find ordering mistakes."),
        ("frontendCommit / toFtq.commit.valid", "Frontend-facing commit notification after ROB gating.", "Useful when commit reaches frontend but should have been suppressed by flush."),
        ("toFtq.redirect.valid", "Frontend-facing redirect generated from ROB flush or redirect generator.", "Compare commit-vs-redirect ordering for exception/flush bugs."),
        ("exception_state.valid / blockCommit", "Exception or commit-blocking condition in ROB dequeue logic.", "Explains why commit should have stalled or redirected instead of retiring."),
    ]

    def esc(text: str) -> str:
        return text.replace("|", "\\|")

    lines = [
        _signal_guide_marker(bug_type="difftest_error"),
        "# Difftest Waveform Search Guide",
        "",
        "## Context",
        f"- Source error log: `{error_log}`",
        f"- Bug type: `{error_summary.get('bug_type', 'unknown')}`",
    ]
    if error_summary.get("mismatch_pc"):
        lines.append(f"- Mismatch PC: `{error_summary['mismatch_pc']}`")
    if mismatch_commit:
        lines.append(f"- Mismatching instruction PC: `{mismatch_commit.get('pc', '<unknown>')}`")
        lines.append(f"- Mismatching instruction encoding: `{mismatch_commit.get('inst_hex', '<unknown>')}`")
        if mismatch_instr_asm is not None:
            lines.append(f"- Mismatching instruction assembly: `{mismatch_instr_asm}`")
        lines.append(f"- Mismatching trace line: `{mismatch_commit.get('trace_line', '<unknown>')}`")
    if error_summary.get("abort_pc"):
        lines.append(f"- Abort PC: `{error_summary['abort_pc']}`")
    lines.append("")
    lines.append("## Suggested Source Files")
    for label, path in source_files:
        lines.append(f"- `{label}`: `{path}`")
    lines.append("")
    lines.append("## Suggested Waveform Scopes")
    lines.append(f"- `{rob_scope}`: ROB commit/dequeue/flush state")
    lines.append(f"- `{ctrl_scope}`: frontend commit and redirect gating")
    lines.append("")
    lines.append("## Suggested Waveform Search Signals")
    lines.append("")
    lines.append("_Note: exact emitted waveform names may differ. Search by suffix or nearby hierarchy first._")
    lines.append("")
    lines.append("| Signal | Meaning | Why Inspect |")
    lines.append("| --- | --- | --- |")
    for signal_name, meaning, why in signal_rows:
        lines.append(f"| `{esc(signal_name)}` | {esc(meaning)} | {esc(why)} |")
    lines.append("")
    lines.append("## Recommended Debug Order")
    lines.append("1. Find the commit lane whose debug_pc/debug_instr matches the mismatching instruction.")
    lines.append("2. Check commitValid(i), isCommit, and robIdx(i) on that lane.")
    lines.append("3. Check whether flushOut.valid, needFlush, or redirect.valid should have blocked that commit.")
    lines.append("4. Correlate frontendCommit and toFtq.redirect.valid with the same cycle window.")
    lines.append("5. Build the final hypothesis only after the waveform timing matches the ROB/ctrl logic.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_difftest_signal_guide(
    *,
    error_log: Path,
    scala_root: Path,
    focus_scope: str | None,
    error_summary: dict[str, Any],
    mismatch_instr_asm: str | None,
) -> dict[str, Any]:
    out_path = _default_signal_guide_out(error_log=error_log)
    result: dict[str, Any] = {"output_path": str(out_path)}
    freshness_floor = error_log.stat().st_mtime_ns
    if out_path.exists():
        existing_text = out_path.read_text(encoding="utf-8", errors="ignore")
        if out_path.stat().st_mtime_ns >= freshness_floor and _signal_guide_marker(bug_type="difftest_error") in existing_text:
            result["status"] = "cache-hit"
            return result
    content = _render_difftest_signal_guide(
        error_log=error_log,
        scala_root=scala_root,
        focus_scope=focus_scope,
        error_summary=error_summary,
        mismatch_instr_asm=mismatch_instr_asm,
    )
    try:
        out_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        if _is_permission_denied(exc=exc):
            result["status"] = "permission-denied"
            result["message"] = _permission_denied_message(out_path)
        else:
            result["status"] = "failed"
            result["message"] = str(exc)
        return result
    result["status"] = "generated"
    return result


def _render_assert_debug_guide(*, error_log: Path, assert_context: dict[str, Any], abort_pc: str | None) -> str:
    lines = [
        f"<!-- assert-debug-guide: {DEFAULT_ASSERT_GUIDE_VERSION} -->",
        "# Assert Error Debug Guide",
        "",
        "## Context",
        f"- Source error log: `{error_log}`",
        f"- Asserted Verilog site: `{assert_context['assert_path']}:{assert_context['assert_line']}`",
        f"- Verilog module: `{assert_context['verilog_module']}`",
    ]
    if abort_pc:
        lines.append(f"- Abort PC: `{abort_pc}`")
    lines.append("")
    lines.append("## Trigger Condition")
    trigger_line = assert_context.get("trigger_line")
    trigger_line_no = assert_context.get("trigger_line_no")
    if trigger_line is None:
        lines.append("- Could not recover the enclosing Verilog trigger line automatically.")
    else:
        lines.append(f"- Trigger line: `{trigger_line.strip()}`")
        if trigger_line_no is not None:
            lines.append(f"- Trigger line number: `{trigger_line_no}`")
        operands = assert_context.get("trigger_operands", [])
        if operands:
            lines.append("- Trigger operands:")
            for operand in operands:
                lines.append(f"  - `{operand}`")
    lines.append("")
    lines.append("## Verilog Context")
    lines.append("```verilog")
    for row in assert_context.get("context_lines", []):
        lines.append(f"{row['line']:>5}: {row['text']}")
    lines.append("```")
    lines.append("")
    lines.append("## Scala Candidates")
    scala_sites = assert_context.get("scala_sites", [])
    if not scala_sites:
        lines.append("- No Scala source hint was recovered from the emitted Verilog context.")
    else:
        for site in scala_sites:
            lines.append(f"- `{site['scala_file']}:{site['scala_line']}`")
            for path in site.get("resolved_paths", []):
                lines.append(f"  - resolved path: `{path}`")
    lines.append("")
    lines.append("## Recommended Analysis")
    lines.append("1. Read the Verilog trigger line and identify the exact boolean condition that must be true for the assertion to fire.")
    lines.append("2. Use the nearby Verilog context to understand what generated wires such as `_GEN_*` stand for.")
    lines.append("3. Open the resolved Scala/Chisel file(s) and locate the matching `assert(...)`, `when(...)`, or handshake logic.")
    lines.append("4. Explain the assertion in terms of the original protocol or invariant, not just the generated Verilog syntax.")
    lines.append("5. Use the waveform to check each trigger operand at the asserted cycle and show why the condition became true.")
    lines.append("6. If the Verilog points to multiple Scala assertion sites nearby, compare them and identify which one matches the failing trigger line.")
    lines.append("")
    lines.append("## Output Expectation")
    lines.append("- State the asserted invariant in plain language.")
    lines.append("- Cite the exact Verilog condition and the matching Scala/Chisel logic.")
    lines.append("- Explain which operand or protocol assumption was violated at the failing cycle.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_assert_debug_guide(
    *,
    error_log: Path,
    scala_root: Path,
    error_summary: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    out_path = _default_assert_guide_out(error_log=error_log)
    result: dict[str, Any] = {"output_path": str(out_path)}
    assert_site = error_summary.get("assert_site")
    if assert_site is None:
        result["status"] = "missing-assert-site"
        result["message"] = "assert site was not available in the parsed error summary"
        return result, None

    assert_context = collect_assert_error_context(
        assert_path=Path(assert_site["path"]),
        assert_line=int(assert_site["line"]),
        scala_root=scala_root,
    )
    result["assert_context_status"] = assert_context.get("status")
    if assert_context.get("status") != "ok":
        result["status"] = "failed"
        result["message"] = assert_context.get("message", "failed to collect assert context")
        return result, assert_context

    if out_path.exists():
        existing_text = out_path.read_text(encoding="utf-8", errors="ignore")
        if out_path.stat().st_mtime_ns >= error_log.stat().st_mtime_ns and f"<!-- assert-debug-guide: {DEFAULT_ASSERT_GUIDE_VERSION} -->" in existing_text:
            result["status"] = "cache-hit"
            return result, assert_context

    content = _render_assert_debug_guide(
        error_log=error_log,
        assert_context=assert_context,
        abort_pc=error_summary.get("abort_pc"),
    )
    try:
        out_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        if _is_permission_denied(exc=exc):
            result["status"] = "permission-denied"
            result["message"] = _permission_denied_message(out_path)
        else:
            result["status"] = "failed"
            result["message"] = str(exc)
        return result, assert_context
    result["status"] = "generated"
    return result, assert_context


def _render_assert_signal_guide(*, error_log: Path, assert_context: dict[str, Any], abort_pc: str | None) -> str:
    def esc(text: str) -> str:
        return text.replace("|", "\\|")

    primary_scala_site = assert_context.get("primary_scala_site")
    primary_scala_label = (
        f"{primary_scala_site['scala_file']}:{primary_scala_site['scala_line']}"
        if primary_scala_site is not None
        else "<unknown>"
    )
    primary_scala_paths = primary_scala_site.get("resolved_paths", []) if primary_scala_site is not None else []
    module_name = assert_context.get("verilog_module", "<unknown>")
    scala_keywords = []
    if primary_scala_site is not None:
        scala_keywords.append(Path(primary_scala_site["scala_file"]).stem)
    if module_name:
        scala_keywords.extend([module_name, module_name.lower(), module_name.replace("AXI4", "").lower()])

    trigger_line = assert_context.get("trigger_line")
    trigger_line_no = assert_context.get("trigger_line_no")
    trigger_operands = list(assert_context.get("trigger_operands", []))
    if trigger_line and "auto_out_b_bits_id" in trigger_line and "auto_out_b_bits_id" not in trigger_operands:
        trigger_operands.append("auto_out_b_bits_id")

    signal_rows: list[tuple[str, str, str, str]] = []
    for operand in trigger_operands:
        meaning = "Operand participating in the asserted Verilog condition."
        why = "Check whether this operand makes the assertion condition true at the failing cycle."
        code_pos = f"{assert_context['verilog_module']}.sv:{trigger_line_no}" if trigger_line_no else assert_context["assert_path"]
        if operand == "reset":
            meaning = "Module reset signal."
            why = "The assertion should only matter when reset is deasserted."
        elif operand.endswith("_valid"):
            meaning = "Handshake valid signal for the relevant channel."
            why = "If this becomes 1 while the matching bookkeeping signal is 0, the assertion fires."
        elif operand.endswith("_ready"):
            meaning = "Handshake ready signal for the relevant channel."
            why = "Use it to understand whether the transfer is accepted in the same cycle."
        elif operand.endswith("_bits_id"):
            meaning = "Response/request ID used to select the matching queue entry."
            why = "This ID decides which queue slot or bitmap bit should be valid."
        elif operand.startswith("_GEN_"):
            meaning = "Generated Verilog condition derived from queue-valid or protocol bookkeeping logic."
            why = "Trace this generated expression back to the Scala assertion and nearby queue logic."
            if primary_scala_site is not None:
                code_pos = f"{primary_scala_label} and {assert_context['verilog_module']}.sv:{trigger_line_no}"
        signal_rows.append((operand, meaning, why, code_pos))

    if primary_scala_site and primary_scala_site["scala_file"] == "UserYanker.scala":
        user_rows = [
            ("auto_out_b_valid", "B-channel response valid from downstream AXI.", "This is the direct response-valid side of the assertion.", f"{assert_context['verilog_module']}.sv:{trigger_line_no} / UserYanker.scala:{primary_scala_site['scala_line']}"),
            ("auto_out_b_bits_id", "B-channel response ID selecting the bookkeeping queue entry.", "Use it to determine which `wqueues(bid)` slot should be valid.", f"{assert_context['verilog_module']}.sv:{trigger_line_no} / UserYanker.scala:92-95"),
            ("_GEN_6[auto_out_b_bits_id]", "Selected `wqueues(bid).deq.valid` bit in emitted Verilog.", "This is the exact queue-valid condition required by the assertion.", f"{assert_context['verilog_module']}.sv:{trigger_line_no} and {assert_context['verilog_module']}.sv:953"),
            ("Queue1_BundleMap_<mapped_id>.io_deq_valid", "Concrete queue valid bit for the B response ID after emitted Verilog indexing.", "Check whether the queue entry exists when the B response arrives.", f"{assert_context['verilog_module']}.sv:953-1017 / UserYanker.scala:92-104"),
            ("Queue1_BundleMap_<mapped_id>.io_enq_valid", "AW-side enqueue into the corresponding write-response bookkeeping queue.", "Helps decide whether the entry was never enqueued or was lost too early.", f"{assert_context['verilog_module']}.sv:2337-2701 / UserYanker.scala:101-104"),
            ("auto_in_aw_valid / auto_out_aw_ready / auto_in_aw_bits_id", "AW handshake and ID entering the yanker.", "Correlate whether the matching AW was accepted before the B response arrived.", "UserYanker.scala:84-104"),
        ]
        for row in user_rows:
            if row[0] not in [existing[0] for existing in signal_rows]:
                signal_rows.append(row)

    lines = [
        _signal_guide_marker(bug_type="assert_error"),
        "# Assert Error Waveform Search Guide",
        "",
        "## Context",
        f"- Source error log: `{error_log}`",
        f"- Asserted Verilog site: `{assert_context['assert_path']}:{assert_context['assert_line']}`",
        f"- Verilog module: `{module_name}`",
        f"- Primary Scala site: `{primary_scala_label}`",
    ]
    if primary_scala_paths:
        for path in primary_scala_paths:
            lines.append(f"  - Scala path: `{path}`")
    if abort_pc:
        lines.append(f"- Abort PC: `{abort_pc}`")
    lines.append("")
    lines.append("## Trigger Condition")
    if trigger_line is None:
        lines.append("- Could not recover the exact Verilog trigger line automatically.")
    else:
        lines.append(f"- Trigger line: `{trigger_line.strip()}`")
        if trigger_line_no is not None:
            lines.append(f"- Trigger line number: `{trigger_line_no}`")
    lines.append("")
    lines.append("## Code Positions")
    lines.append(f"- Verilog assert site: `{assert_context['assert_path']}:{assert_context['assert_line']}`")
    if trigger_line_no is not None:
        lines.append(f"- Verilog trigger condition: `{assert_context['assert_path']}:{trigger_line_no}`")
    for site in assert_context.get("scala_sites", []):
        lines.append(f"- Scala candidate: `{site['scala_file']}:{site['scala_line']}`")
        for path in site.get("resolved_paths", []):
            lines.append(f"  - Resolved path: `{path}`")
    lines.append("")
    lines.append("## Scope Search Hints")
    lines.append("- Search waveform scopes by these keywords first:")
    for keyword in dict.fromkeys([kw for kw in scala_keywords if kw]):
        lines.append(f"  - `{keyword}`")
    lines.append("")
    lines.append("## Suggested Waveform Search Signals")
    lines.append("")
    lines.append("| Signal | Meaning | Why Inspect | Verilog / Chisel Position |")
    lines.append("| --- | --- | --- | --- |")
    for signal_name, meaning, why, code_pos in signal_rows:
        lines.append(f"| `{esc(signal_name)}` | {esc(meaning)} | {esc(why)} | `{esc(code_pos)}` |")
    lines.append("")
    lines.append("## Recommended Debug Order")
    lines.append("1. Read the asserted Verilog line and the recovered trigger condition first.")
    lines.append("2. Open the primary Scala site and identify the original protocol invariant behind the assertion.")
    lines.append("3. Search waveform by the module/scope keywords and then by the listed trigger operands.")
    lines.append("4. Check whether the response-side valid/id and the queue-valid bookkeeping side agree in the asserted cycle.")
    lines.append("5. Only after the trigger condition is understood should you broaden to nearby protocol traffic.")
    lines.append("")
    return "\n".join(lines) + "\n"


def _write_assert_signal_guide(
    *,
    error_log: Path,
    error_summary: dict[str, Any],
    assert_context: dict[str, Any] | None,
) -> dict[str, Any]:
    out_path = _default_signal_guide_out(error_log=error_log)
    result: dict[str, Any] = {"output_path": str(out_path)}
    if assert_context is None or assert_context.get("status") != "ok":
        result["status"] = "failed"
        result["message"] = "assert context was not available for waveform signal guide generation"
        return result
    freshness_floor = error_log.stat().st_mtime_ns
    if out_path.exists():
        existing_text = out_path.read_text(encoding="utf-8", errors="ignore")
        if out_path.stat().st_mtime_ns >= freshness_floor and _signal_guide_marker(bug_type="assert_error") in existing_text:
            result["status"] = "cache-hit"
            return result
    content = _render_assert_signal_guide(
        error_log=error_log,
        assert_context=assert_context,
        abort_pc=error_summary.get("abort_pc"),
    )
    try:
        out_path.write_text(content, encoding="utf-8")
    except OSError as exc:
        if _is_permission_denied(exc=exc):
            result["status"] = "permission-denied"
            result["message"] = _permission_denied_message(out_path)
        else:
            result["status"] = "failed"
            result["message"] = str(exc)
        return result
    result["status"] = "generated"
    return result


def _resolve_waveform_arg(args: argparse.Namespace) -> Path:
    waveform = getattr(args, "waveform", None)
    vcd = getattr(args, "vcd", None)
    if waveform is not None and vcd is not None and waveform != vcd:
        raise SystemExit("provide either --waveform or --vcd, not both")
    resolved = waveform or vcd
    if resolved is None:
        raise SystemExit("one of --waveform or --vcd is required")
    return resolved


def _cmd_inspect_inputs(args: argparse.Namespace) -> int:
    waveform = _resolve_waveform_arg(args)
    _validate(args.scala_root, "scala-root")
    _validate(waveform, "waveform")
    if args.error_log is not None:
        _validate(args.error_log, "error-log")

    rtl_files = 0
    rtl_bytes = 0
    if args.rtl_root is not None:
        _validate(args.rtl_root, "rtl-root")
        rtl_files, rtl_bytes = _dir_stats(args.rtl_root)
    scala_files, scala_bytes = _dir_stats(args.scala_root)
    waveform_bytes = waveform.stat().st_size
    error_log_bytes = args.error_log.stat().st_size if args.error_log is not None else 0
    error_summary = summarize_error_log(args.error_log) if args.error_log is not None else None
    mismatch_instr_asm = _decode_mismatch_instruction(error_summary.get("mismatch_commit")) if error_summary else None
    difftest_disassembly: dict[str, Any] | None = None
    difftest_signal_guide: dict[str, Any] | None = None
    assert_debug_guide: dict[str, Any] | None = None
    assert_signal_guide: dict[str, Any] | None = None
    assert_context: dict[str, Any] | None = None
    if args.error_log is not None and error_summary is not None and error_summary["bug_type"] == "difftest_error":
        difftest_disassembly = _run_difftest_disassembler(error_log=args.error_log)
        difftest_signal_guide = _write_difftest_signal_guide(
            error_log=args.error_log,
            scala_root=args.scala_root,
            focus_scope=args.focus_scope,
            error_summary=error_summary,
            mismatch_instr_asm=mismatch_instr_asm,
        )
    if args.error_log is not None and error_summary is not None and error_summary["bug_type"] == "assert_error":
        assert_debug_guide, assert_context = _write_assert_debug_guide(
            error_log=args.error_log,
            scala_root=args.scala_root,
            error_summary=error_summary,
        )
        assert_signal_guide = _write_assert_signal_guide(
            error_log=args.error_log,
            error_summary=error_summary,
            assert_context=assert_context,
        )
    authority_out = args.authority_out or (
        _default_authority_out(rtl_root=args.rtl_root, top=args.top) if args.rtl_root is not None else None
    )
    wave_out = args.wave_out or _default_wave_out(vcd=waveform, window_len=args.window_len)
    packet_out = args.packet_out or _default_packet_out(wave_out=wave_out, window_id="wN")

    print("Validated inputs")
    print(f"rtl-root: {args.rtl_root if args.rtl_root is not None else '<not provided>'}")
    print(f"scala-root: {args.scala_root}")
    print(f"waveform: {waveform}")
    print(f"error-log: {args.error_log if args.error_log is not None else '<not provided>'}")
    print()
    print("Artifact sizes")
    if args.rtl_root is not None:
        print(f"rtl-root: files={rtl_files} size={_format_bytes(rtl_bytes)}")
    else:
        print("rtl-root: <not provided>")
    print(f"scala-root: files={scala_files} size={_format_bytes(scala_bytes)}")
    print(f"waveform: size={_format_bytes(waveform_bytes)}")
    if args.error_log is not None:
        print(f"error-log: size={_format_bytes(error_log_bytes)}")
    else:
        print("error-log: <not provided>")
    print()

    warnings: list[str] = []
    if waveform_bytes >= WARN_WAVEFORM_BYTES:
        warnings.append("Waveform is very large; waveform DB generation may take minutes and produce multi-GB outputs.")
    if args.rtl_root is not None and (rtl_files >= WARN_RTL_FILES or rtl_bytes >= WARN_TREE_BYTES):
        warnings.append("RTL tree is large; authority extraction may take noticeable time and memory.")
    if warnings:
        print("Warnings")
        for warning in warnings:
            print(f"- {warning}")
        print()

    if args.suggestion:
        print(f"Debug suggestion: {args.suggestion}")
    if args.focus_scope:
        print(f"Focus scope: {args.focus_scope}")
    print()
    print("Error summary")
    if error_summary is None:
        print("bug-type: <not provided>")
    else:
        print(f"bug-type: {error_summary['bug_type']} ({error_summary['confidence']} confidence)")
        assert_site = error_summary.get("assert_site")
        if assert_site is not None:
            print(f"assert-site: {assert_site['path']}:{assert_site['line']}")
            print(f"assert-module: {assert_site['module']}")
        if assert_context is not None and assert_context.get("trigger_line") is not None:
            print(f"assert-trigger-line: {assert_context['trigger_line'].strip()}")
        if assert_context is not None:
            for site in assert_context.get("scala_sites", []):
                print(f"assert-scala-site: {site['scala_file']}:{site['scala_line']}")
                for path in site.get("resolved_paths", [])[:3]:
                    print(f"assert-scala-path: {path}")
        if error_summary.get("mismatch_pc"):
            print(f"mismatch-pc: {error_summary['mismatch_pc']}")
        mismatch_commit = error_summary.get("mismatch_commit")
        if mismatch_commit is not None:
            print(f"error-instr-pc: {mismatch_commit['pc']}")
            print(f"error-instr-encoding: {mismatch_commit['inst_hex']}")
            if mismatch_instr_asm is not None:
                print(f"error-instr-assembly: {mismatch_instr_asm}")
            print(f"error-instr-trace: {mismatch_commit['trace_line']}")
        if error_summary.get("abort_pc"):
            print(f"abort-pc: {error_summary['abort_pc']}")
        if error_summary.get("stuck_hint"):
            print(f"stuck-hint: {error_summary['stuck_hint']}")
        evidence_lines = error_summary.get("evidence_lines", [])
        if evidence_lines:
            print("error-clues:")
            for line in evidence_lines:
                print(f"- {line}")
        if difftest_disassembly is not None:
            print(f"difftest-disassembly: {difftest_disassembly['status']}")
            print(f"difftest-disassembly-out: {difftest_disassembly['output_path']}")
            if difftest_disassembly.get("stderr"):
                print(f"difftest-disassembly-note: {difftest_disassembly['stderr']}")
            elif difftest_disassembly.get("message"):
                print(f"difftest-disassembly-note: {difftest_disassembly['message']}")
        if difftest_signal_guide is not None:
            print(f"difftest-signal-guide: {difftest_signal_guide['status']}")
            print(f"difftest-signal-guide-out: {difftest_signal_guide['output_path']}")
            if difftest_signal_guide.get("message"):
                print(f"difftest-signal-guide-note: {difftest_signal_guide['message']}")
        if assert_debug_guide is not None:
            print(f"assert-debug-guide: {assert_debug_guide['status']}")
            print(f"assert-debug-guide-out: {assert_debug_guide['output_path']}")
            if assert_debug_guide.get("message"):
                print(f"assert-debug-guide-note: {assert_debug_guide['message']}")
        if assert_signal_guide is not None:
            print(f"assert-signal-guide: {assert_signal_guide['status']}")
            print(f"assert-signal-guide-out: {assert_signal_guide['output_path']}")
            if assert_signal_guide.get("message"):
                print(f"assert-signal-guide-note: {assert_signal_guide['message']}")
    print()
    print("Artifact locations")
    if authority_out is not None:
        print(f"authority-out: {authority_out}")
    else:
        print("authority-out: <skipped in waveform-only mode>")
    print(f"wave-out: {wave_out}")
    print(f"packet-out-template: {packet_out}")
    if difftest_disassembly is not None:
        print(f"difftest-disassembly-out: {difftest_disassembly['output_path']}")
    if difftest_signal_guide is not None:
        print(f"difftest-signal-guide-out: {difftest_signal_guide['output_path']}")
    if assert_debug_guide is not None:
        print(f"assert-debug-guide-out: {assert_debug_guide['output_path']}")
    if assert_signal_guide is not None:
        print(f"assert-signal-guide-out: {assert_signal_guide['output_path']}")
    print()
    print("Cache status")
    if authority_out is not None:
        authority_meta = _authority_cache_meta(rtl_root=args.rtl_root, top=args.top)
        authority_hit = _cache_matches(
            authority_out,
            authority_meta,
            ["rtl_authority.sqlite3", "rtl_authority_table.json", "rtl_authority_index.json"],
        )
        print(f"authority: {'cache hit' if authority_hit else 'rebuild required'}")
    else:
        print("authority: skipped")
    wave_meta = _wave_cache_meta(vcd=waveform, window_len=args.window_len)
    wave_hit = _cache_matches(
        wave_out,
        wave_meta,
        ["manifest.json", "signal_metadata.sqlite3", "signals.json", "windows.json"],
    )
    print(f"wave-db: {'cache hit' if wave_hit else 'rebuild required'}")
    print()
    print("Commands to run")
    if args.rtl_root is not None:
        print(f"{_self_cmd()} build-authority --rtl-root {args.rtl_root} --top {args.top} --out-dir {authority_out}")
    else:
        print("waveform-only analysis mode: exact RTL authority build is skipped")
    if difftest_disassembly is not None:
        print(difftest_disassembly["command"])
    print(f"{_self_cmd()} build-wave-db --waveform {waveform} --out-dir {wave_out} --window-len {args.window_len}")
    print(f"{_self_cmd()} build-wave-meta --waveform {waveform} --out-dir {_default_wave_meta_out(waveform=waveform)}")
    packet_cmd = f"{_self_cmd()} query-packet --manifest {wave_out / 'manifest.json'} --window-id <wN> --out {packet_out}"
    if args.rtl_root is not None:
        packet_cmd += f" --authority {authority_out / 'rtl_authority.sqlite3'}"
    if args.focus_scope:
        packet_cmd += f" --focus-scope {args.focus_scope}"
    print(packet_cmd)
    print(f"{_self_cmd()} rough-map-chisel --packet {packet_out} --mapping <rough-mapping.json> --out <rough-join.json>")
    print(f"{_self_cmd()} query-signal-value --manifest {wave_out / 'manifest.json'} --signal <full-wave-path> --time <t>")
    print()
    print("How to map back")
    print("- Use query-packet to read waveform evidence by window.")
    print("- Treat rtl_authority.sqlite3 matches as exact RTL ownership.")
    print("- For rough Chisel recovery, join module_type + local_signal_name with a rough mapping artifact if available.")
    if error_summary is not None:
        print("- Treat error-log bug-type hints as priors only; confirm the real root cause from waveform + Scala/Chisel evidence.")
        if error_summary["bug_type"] == "assert_error":
            print("- For assert_error, start from the asserted RTL module/site before broad waveform exploration.")
            print("- Read the nearby Verilog lines around the asserted line and identify the exact trigger condition.")
            print("- Use the emitted Verilog's embedded Scala source hint to open the corresponding Scala/Chisel file and line.")
            print("- Then explain why the trigger condition became true by matching the Verilog condition with the Scala/Chisel logic and waveform operands.")
        elif error_summary["bug_type"] == "difftest_error":
            print("- First identify and print the mismatching instruction from simulator_out/disassembly.txt.")
            print("- Then inspect the ROB commit path in Scala: RobBundles commit_v/commit_w/debug_pc/debug_instr, Rob.scala io.commits.commitValid/isCommit/robIdx(i), and CtrlBlock frontendCommit/flushOut/redirect.")
            print("- Then search waveform around ROB commit signals for that instruction and correlate commit gating, robIdx movement, and redirect/flush timing.")
            if difftest_disassembly is not None:
                print("- Use disassembly.txt from the simulator_out directory as a quick instruction-level view of the mismatching commit trace.")
    return 0


def _cmd_build_authority(args: argparse.Namespace) -> int:
    out_dir = args.out_dir or _default_authority_out(rtl_root=args.rtl_root, top=args.top)
    cache_meta = _authority_cache_meta(rtl_root=args.rtl_root, top=args.top)
    if not args.force and _cache_matches(
        out_dir,
        cache_meta,
        ["rtl_authority.sqlite3", "rtl_authority_table.json", "rtl_authority_index.json"],
    ):
        print(f"cache hit: reusing RTL authority at {out_dir}")
        return 0
    build_rtl_authority(rtl_root=args.rtl_root, top=args.top, out_dir=out_dir)
    _store_cache_meta(out_dir, cache_meta)
    print(f"built RTL authority at {out_dir}")
    return 0


def _cmd_build_wave_db(args: argparse.Namespace) -> int:
    waveform = _resolve_waveform_arg(args)
    out_dir = args.out_dir or _default_wave_out(vcd=waveform, window_len=args.window_len)
    cache_meta = _wave_cache_meta(vcd=waveform, window_len=args.window_len)
    if not args.force and _cache_matches(
        out_dir,
        cache_meta,
        ["manifest.json", "signal_metadata.sqlite3", "signals.json", "windows.json"],
    ):
        print(f"cache hit: reusing waveform DB at {out_dir}")
        return 0
    stream_waveform_store(waveform_path=waveform, out_dir=out_dir, window_len=args.window_len)
    _store_cache_meta(out_dir, cache_meta)
    print(f"built waveform DB at {out_dir}")
    return 0


def _cmd_build_wave_meta(args: argparse.Namespace) -> int:
    waveform = _resolve_waveform_arg(args)
    out_dir = args.out_dir or _default_wave_meta_out(waveform=waveform)
    cache_meta = _wave_meta_cache_meta(waveform=waveform)
    if not args.force and _cache_matches(
        out_dir,
        cache_meta,
        ["manifest.json", "signal_metadata.sqlite3", "signals.json", "scopes.json"],
    ):
        print(f"cache hit: reusing waveform metadata at {out_dir}")
        return 0
    build_wave_metadata_cache(waveform_path=waveform, out_dir=out_dir)
    _store_cache_meta(out_dir, cache_meta)
    print(f"built waveform metadata at {out_dir}")
    return 0


def _cmd_query_packet(args: argparse.Namespace) -> int:
    waveform = getattr(args, "waveform", None) or getattr(args, "vcd", None)
    if args.manifest is not None and waveform is not None:
        raise SystemExit("provide either --manifest or --waveform/--vcd, not both")
    if args.manifest is None and waveform is None:
        raise SystemExit("one of --manifest or --waveform/--vcd is required")

    if args.manifest is not None:
        if args.window_id is None:
            raise SystemExit("--window-id is required when using --manifest")
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if args.authority is None:
            packet = build_debug_packet_from_manifest(
                manifest=manifest,
                window_id=args.window_id,
                focus_scope=args.focus_scope,
            )
        elif args.authority.suffix == ".sqlite3":
            packet = build_debug_packet_from_manifest(
                manifest=manifest,
                authority_db=args.authority,
                window_id=args.window_id,
                focus_scope=args.focus_scope,
            )
        else:
            authority = json.loads(args.authority.read_text(encoding="utf-8"))
            packet = build_debug_packet_from_manifest(
                manifest=manifest,
                authority=authority,
                window_id=args.window_id,
                focus_scope=args.focus_scope,
            )
    else:
        if args.t_start is None or args.t_end is None:
            raise SystemExit("--t-start and --t-end are required when using --waveform/--vcd")
        waveform_path = _resolve_waveform_arg(args)
        if args.authority is None:
            packet = build_debug_packet_from_fst(
                waveform_path=waveform_path,
                t_start=args.t_start,
                t_end=args.t_end,
                meta_out_dir=args.meta_dir or _default_wave_meta_out(waveform=waveform_path),
                focus_scope=args.focus_scope,
            )
        elif args.authority.suffix == ".sqlite3":
            packet = build_debug_packet_from_fst(
                waveform_path=waveform_path,
                authority_db=args.authority,
                t_start=args.t_start,
                t_end=args.t_end,
                meta_out_dir=args.meta_dir or _default_wave_meta_out(waveform=waveform_path),
                focus_scope=args.focus_scope,
            )
        else:
            authority = json.loads(args.authority.read_text(encoding="utf-8"))
            packet = build_debug_packet_from_fst(
                waveform_path=waveform_path,
                authority=authority,
                t_start=args.t_start,
                t_end=args.t_end,
                meta_out_dir=args.meta_dir or _default_wave_meta_out(waveform=waveform_path),
                focus_scope=args.focus_scope,
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _cmd_rough_map_chisel(args: argparse.Namespace) -> int:
    packet = json.loads(args.packet.read_text(encoding="utf-8"))
    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
    mapping_rows = mapping.get("mappings", [])
    mapping_by_key = {
        (row.get("rtl_module"), row.get("rtl_signal")): row
        for row in mapping_rows
    }

    joined_signals = []
    for signal in packet.get("focus_signals", []):
        rtl = signal.get("rtl", {})
        key = (rtl.get("module_type"), rtl.get("local_signal_name"))
        rough = mapping_by_key.get(key)
        if rough is None:
            rough_info = {"match_status": "unresolved"}
        else:
            rough_info = {
                "match_status": "rough",
                "chisel_module": rough.get("chisel_module"),
                "chisel_path": rough.get("chisel_path"),
                "rtl_module": rough.get("rtl_module"),
                "rtl_signal": rough.get("rtl_signal"),
                "notes": rough.get("notes"),
            }
        joined_signals.append(
            {
                "full_wave_path": signal.get("full_wave_path"),
                "rtl": rtl,
                "rough_chisel": rough_info,
            }
        )

    out_obj = {
        "version": "0.1",
        "packet_path": str(args.packet),
        "mapping_path": str(args.mapping),
        "signals": joined_signals,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out_obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def _cmd_query_signal_value(args: argparse.Namespace) -> int:
    waveform = getattr(args, "waveform", None) or getattr(args, "vcd", None)
    if args.manifest is not None and waveform is not None:
        raise SystemExit("provide either --manifest or --waveform/--vcd, not both")
    if args.manifest is None and waveform is None:
        raise SystemExit("one of --manifest or --waveform/--vcd is required")

    if args.manifest is not None:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        value_info = query_signal_value_from_manifest(
            manifest=manifest,
            full_wave_path=args.signal,
            t=args.time,
        )
    else:
        value_info = query_signal_value_from_fst(
            waveform_path=_resolve_waveform_arg(args),
            full_wave_path=args.signal,
            t=args.time,
            meta_out_dir=args.meta_dir or _default_wave_meta_out(waveform=_resolve_waveform_arg(args)),
        )
    if args.out is None:
        print(json.dumps(value_info, indent=2, sort_keys=True))
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(value_info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hw-debug-skill", description="Skill-local hardware debug CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_p = sub.add_parser("inspect-inputs")
    inspect_p.add_argument("--rtl-root", type=Path)
    inspect_p.add_argument("--scala-root", required=True, type=Path)
    inspect_p.add_argument("--waveform", type=Path)
    inspect_p.add_argument("--vcd", type=Path)
    inspect_p.add_argument("--error-log", "--error-info", dest="error_log", type=Path)
    inspect_p.add_argument("--focus-scope")
    inspect_p.add_argument("--suggestion")
    inspect_p.add_argument("--top", default="SimTop")
    inspect_p.add_argument("--window-len", type=int, default=1000)
    inspect_p.add_argument("--authority-out", type=Path)
    inspect_p.add_argument("--wave-out", type=Path)
    inspect_p.add_argument("--packet-out", type=Path)
    inspect_p.set_defaults(func=_cmd_inspect_inputs)

    auth_p = sub.add_parser("build-authority")
    auth_p.add_argument("--rtl-root", required=True, type=Path)
    auth_p.add_argument("--top", default="SimTop")
    auth_p.add_argument("--out-dir", type=Path)
    auth_p.add_argument("--force", action="store_true")
    auth_p.set_defaults(func=_cmd_build_authority)

    wave_p = sub.add_parser("build-wave-db")
    wave_p.add_argument("--waveform", type=Path)
    wave_p.add_argument("--vcd", type=Path)
    wave_p.add_argument("--out-dir", type=Path)
    wave_p.add_argument("--window-len", type=int, default=1000)
    wave_p.add_argument("--force", action="store_true")
    wave_p.set_defaults(func=_cmd_build_wave_db)

    meta_p = sub.add_parser("build-wave-meta")
    meta_p.add_argument("--waveform", type=Path)
    meta_p.add_argument("--vcd", type=Path)
    meta_p.add_argument("--out-dir", type=Path)
    meta_p.add_argument("--force", action="store_true")
    meta_p.set_defaults(func=_cmd_build_wave_meta)

    packet_p = sub.add_parser("query-packet")
    packet_p.add_argument("--manifest", type=Path)
    packet_p.add_argument("--waveform", type=Path)
    packet_p.add_argument("--vcd", type=Path)
    packet_p.add_argument("--meta-dir", type=Path)
    packet_p.add_argument("--authority", type=Path)
    packet_p.add_argument("--window-id")
    packet_p.add_argument("--focus-scope")
    packet_p.add_argument("--t-start", type=int)
    packet_p.add_argument("--t-end", type=int)
    packet_p.add_argument("--out", required=True, type=Path)
    packet_p.set_defaults(func=_cmd_query_packet)

    rough_p = sub.add_parser("rough-map-chisel")
    rough_p.add_argument("--packet", required=True, type=Path)
    rough_p.add_argument("--mapping", required=True, type=Path)
    rough_p.add_argument("--out", required=True, type=Path)
    rough_p.set_defaults(func=_cmd_rough_map_chisel)

    value_p = sub.add_parser("query-signal-value")
    value_p.add_argument("--manifest", type=Path)
    value_p.add_argument("--waveform", type=Path)
    value_p.add_argument("--vcd", type=Path)
    value_p.add_argument("--meta-dir", type=Path)
    value_p.add_argument("--signal", required=True)
    value_p.add_argument("--time", required=True, type=int)
    value_p.add_argument("--out", type=Path)
    value_p.set_defaults(func=_cmd_query_signal_value)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
