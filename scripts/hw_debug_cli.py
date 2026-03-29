#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


from lib.build_debug_packet import build_debug_packet_from_manifest
from lib.build_rtl_authority import build_rtl_authority
from lib.ingest_waveform import stream_waveform_store


WARN_VCD_BYTES = 1 * 1024 * 1024 * 1024
WARN_TREE_BYTES = 512 * 1024 * 1024
WARN_RTL_FILES = 1000


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


def _self_cmd() -> str:
    return "python scripts/hw_debug_cli.py"


def _cmd_inspect_inputs(args: argparse.Namespace) -> int:
    _validate(args.scala_root, "scala-root")
    _validate(args.vcd, "vcd")

    rtl_files = 0
    rtl_bytes = 0
    if args.rtl_root is not None:
        _validate(args.rtl_root, "rtl-root")
        rtl_files, rtl_bytes = _dir_stats(args.rtl_root)
    scala_files, scala_bytes = _dir_stats(args.scala_root)
    vcd_bytes = args.vcd.stat().st_size

    print("Validated inputs")
    print(f"rtl-root: {args.rtl_root if args.rtl_root is not None else '<not provided>'}")
    print(f"scala-root: {args.scala_root}")
    print(f"vcd: {args.vcd}")
    print()
    print("Artifact sizes")
    if args.rtl_root is not None:
        print(f"rtl-root: files={rtl_files} size={_format_bytes(rtl_bytes)}")
    else:
        print("rtl-root: <not provided>")
    print(f"scala-root: files={scala_files} size={_format_bytes(scala_bytes)}")
    print(f"vcd: size={_format_bytes(vcd_bytes)}")
    print()

    warnings: list[str] = []
    if vcd_bytes >= WARN_VCD_BYTES:
        warnings.append("VCD is very large; waveform DB generation may take minutes and produce multi-GB outputs.")
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
    print("Commands to run")
    if args.rtl_root is not None:
        print(f"{_self_cmd()} build-authority --rtl-root {args.rtl_root} --top {args.top} --out-dir {args.authority_out}")
    else:
        print("waveform-only analysis mode: exact RTL authority build is skipped")
    print(f"{_self_cmd()} build-wave-db --vcd {args.vcd} --out-dir {args.wave_out} --window-len {args.window_len}")
    packet_cmd = f"{_self_cmd()} query-packet --manifest {args.wave_out / 'manifest.json'} --window-id <wN> --out {args.packet_out}"
    if args.rtl_root is not None:
        packet_cmd += f" --authority {args.authority_out / 'rtl_authority.sqlite3'}"
    if args.focus_scope:
        packet_cmd += f" --focus-scope {args.focus_scope}"
    print(packet_cmd)
    print(f"{_self_cmd()} rough-map-chisel --packet {args.packet_out} --mapping <rough-mapping.json> --out <rough-join.json>")
    print()
    print("How to map back")
    print("- Use query-packet to read waveform evidence by window.")
    print("- Treat rtl_authority.sqlite3 matches as exact RTL ownership.")
    print("- For rough Chisel recovery, join module_type + local_signal_name with a rough mapping artifact if available.")
    return 0


def _cmd_build_authority(args: argparse.Namespace) -> int:
    build_rtl_authority(rtl_root=args.rtl_root, top=args.top, out_dir=args.out_dir)
    return 0


def _cmd_build_wave_db(args: argparse.Namespace) -> int:
    stream_waveform_store(vcd_path=args.vcd, out_dir=args.out_dir, window_len=args.window_len)
    return 0


def _cmd_query_packet(args: argparse.Namespace) -> int:
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
    args.out.write_text(json.dumps(out_obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hw-debug-skill", description="Skill-local hardware debug CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_p = sub.add_parser("inspect-inputs")
    inspect_p.add_argument("--rtl-root", type=Path)
    inspect_p.add_argument("--scala-root", required=True, type=Path)
    inspect_p.add_argument("--vcd", required=True, type=Path)
    inspect_p.add_argument("--focus-scope")
    inspect_p.add_argument("--suggestion")
    inspect_p.add_argument("--top", default="SimTop")
    inspect_p.add_argument("--window-len", type=int, default=1000)
    inspect_p.add_argument("--authority-out", type=Path, default=Path("/tmp/hw_debug_rtl_authority_skill"))
    inspect_p.add_argument("--wave-out", type=Path, default=Path("/tmp/hw_wave_db_skill"))
    inspect_p.add_argument("--packet-out", type=Path, default=Path("/tmp/hw_debug_packet_skill.json"))
    inspect_p.set_defaults(func=_cmd_inspect_inputs)

    auth_p = sub.add_parser("build-authority")
    auth_p.add_argument("--rtl-root", required=True, type=Path)
    auth_p.add_argument("--top", default="SimTop")
    auth_p.add_argument("--out-dir", required=True, type=Path)
    auth_p.set_defaults(func=_cmd_build_authority)

    wave_p = sub.add_parser("build-wave-db")
    wave_p.add_argument("--vcd", required=True, type=Path)
    wave_p.add_argument("--out-dir", required=True, type=Path)
    wave_p.add_argument("--window-len", type=int, default=1000)
    wave_p.set_defaults(func=_cmd_build_wave_db)

    packet_p = sub.add_parser("query-packet")
    packet_p.add_argument("--manifest", required=True, type=Path)
    packet_p.add_argument("--authority", type=Path)
    packet_p.add_argument("--window-id", required=True)
    packet_p.add_argument("--focus-scope")
    packet_p.add_argument("--out", required=True, type=Path)
    packet_p.set_defaults(func=_cmd_query_packet)

    rough_p = sub.add_parser("rough-map-chisel")
    rough_p.add_argument("--packet", required=True, type=Path)
    rough_p.add_argument("--mapping", required=True, type=Path)
    rough_p.add_argument("--out", required=True, type=Path)
    rough_p.set_defaults(func=_cmd_rough_map_chisel)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
