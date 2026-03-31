#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))


from lib.build_debug_packet import build_debug_packet_from_manifest, query_signal_value_from_manifest
from lib.build_rtl_authority import build_rtl_authority
from lib.direct_fst_query import build_debug_packet_from_fst, query_signal_value_from_fst
from lib.ingest_waveform import stream_waveform_store
from lib.wave_metadata_cache import build_wave_metadata_cache
from lib.waveform_formats import detect_waveform_format


WARN_WAVEFORM_BYTES = 1 * 1024 * 1024 * 1024
WARN_TREE_BYTES = 512 * 1024 * 1024
WARN_RTL_FILES = 1000
ARTIFACTS_DIR = SKILL_DIR / "artifacts"


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

    rtl_files = 0
    rtl_bytes = 0
    if args.rtl_root is not None:
        _validate(args.rtl_root, "rtl-root")
        rtl_files, rtl_bytes = _dir_stats(args.rtl_root)
    scala_files, scala_bytes = _dir_stats(args.scala_root)
    waveform_bytes = waveform.stat().st_size
    authority_out = args.authority_out or (
        _default_authority_out(rtl_root=args.rtl_root, top=args.top) if args.rtl_root is not None else None
    )
    wave_out = args.wave_out or _default_wave_out(vcd=waveform, window_len=args.window_len)
    packet_out = args.packet_out or _default_packet_out(wave_out=wave_out, window_id="wN")

    print("Validated inputs")
    print(f"rtl-root: {args.rtl_root if args.rtl_root is not None else '<not provided>'}")
    print(f"scala-root: {args.scala_root}")
    print(f"waveform: {waveform}")
    print()
    print("Artifact sizes")
    if args.rtl_root is not None:
        print(f"rtl-root: files={rtl_files} size={_format_bytes(rtl_bytes)}")
    else:
        print("rtl-root: <not provided>")
    print(f"scala-root: files={scala_files} size={_format_bytes(scala_bytes)}")
    print(f"waveform: size={_format_bytes(waveform_bytes)}")
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
    print("Artifact locations")
    if authority_out is not None:
        print(f"authority-out: {authority_out}")
    else:
        print("authority-out: <skipped in waveform-only mode>")
    print(f"wave-out: {wave_out}")
    print(f"packet-out-template: {packet_out}")
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
    print(f"{_self_cmd()} build-wave-db --waveform {waveform} --out-dir {wave_out} --window-len {args.window_len}")
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
