from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from lib.rtl_build_hierarchy import build_signal_hierarchy
from lib.rtl_parse_modules import parse_rtl_files


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _discover_rtl_files(rtl_root: Path) -> list[Path]:
    files = sorted(list(rtl_root.rglob("*.sv")) + list(rtl_root.rglob("*.v")))
    return [path for path in files if path.is_file()]


def _write_authority_sqlite(path: Path, rows: list[dict[str, Any]]) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("drop table if exists authority_lookup")
        conn.execute("drop index if exists authority_lookup_full_signal_name_idx")
        conn.execute(
            """
            create table authority_lookup (
                full_signal_name text,
                module_type text,
                instance_path text,
                local_signal_name text,
                signal_kind text,
                direction text,
                decl_width_bits integer,
                source_file text,
                provenance text
            )
            """
        )
        conn.executemany(
            """
            insert into authority_lookup(
                full_signal_name, module_type, instance_path, local_signal_name,
                signal_kind, direction, decl_width_bits, source_file, provenance
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["full_signal_name"],
                    row["module_type"],
                    row["instance_path"],
                    row["local_signal_name"],
                    row["signal_kind"],
                    row["direction"],
                    row["decl_width_bits"],
                    row["source_file"],
                    row["provenance"],
                )
                for row in rows
            ],
        )
        conn.execute("create index authority_lookup_full_signal_name_idx on authority_lookup(full_signal_name)")
        conn.commit()
    finally:
        conn.close()


def build_rtl_authority(*, rtl_root: Path, top: str, out_dir: Path, version: str = "0.1") -> dict[str, Path]:
    rtl_files = _discover_rtl_files(rtl_root)
    if not rtl_files:
        raise ValueError(f"no emitted RTL files found under {rtl_root}")
    modules = parse_rtl_files(rtl_files)
    rows, hierarchy_stats = build_signal_hierarchy(modules, top_name=top, include_stats=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    authority_obj = {
        "version": version,
        "top": top,
        "rtl_root": str(rtl_root),
        "summary": {
            "rtl_file_count": len(rtl_files),
            "module_count": len(modules),
            "signal_count": len(rows),
            "cached_module_template_count": hierarchy_stats["cached_module_template_count"],
        },
        "signals": [
            {
                "module_type": row.module_type,
                "instance_path": row.instance_path,
                "local_signal_name": row.local_signal_name,
                "full_signal_name": row.full_signal_name,
                "signal_kind": row.signal_kind,
                "direction": row.direction,
                "decl_width_bits": row.decl_width_bits,
                "source_file": row.source_file,
                "provenance": "emitted_rtl_exact",
            }
            for row in rows
        ],
        "coverage_gaps": [],
    }
    authority_path = out_dir / "rtl_authority_table.json"
    _write_json(authority_path, authority_obj)
    authority_index = {row["full_signal_name"]: row for row in authority_obj["signals"]}
    authority_index_path = out_dir / "rtl_authority_index.json"
    _write_json(authority_index_path, authority_index)
    authority_db_path = out_dir / "rtl_authority.sqlite3"
    _write_authority_sqlite(authority_db_path, authority_obj["signals"])
    return {
        "rtl_authority_db": authority_db_path,
        "rtl_authority_index": authority_index_path,
        "rtl_authority_table": authority_path,
    }
