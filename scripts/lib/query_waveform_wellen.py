from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lib.build_debug_packet import (
    _lookup_authority_rows_sqlite,
    _normalize_authority_rows,
    _window_numeric_id,
    build_debug_packet,
)
from lib.ingest_waveform_wellen import _build_scope_signal_index, _collect_metadata, _load_pywellen, _normalize_value


ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"


def _scope_path_candidates(path: str) -> list[str]:
    candidates = [path]
    if path.startswith("TOP."):
        candidates.append(path[len("TOP.") :])
    else:
        candidates.append(f"TOP.{path}")
    return list(dict.fromkeys(candidates))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _fingerprint(parts: dict[str, Any]) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _metadata_cache_root(metadata_cache_root: Path | None = None) -> Path:
    return metadata_cache_root or (ARTIFACTS_DIR / "waveform_meta")


def _query_cache_root(query_cache_root: Path | None = None) -> Path:
    return query_cache_root or (ARTIFACTS_DIR / "waveform_query")


def _metadata_cache_meta(*, wave_path: Path) -> dict[str, Any]:
    return {
        "kind": "waveform_meta",
        "waveform": _file_signature(wave_path),
    }


def _authority_cache_identity(
    *,
    authority: dict[str, Any] | None = None,
    authority_rows: list[dict[str, Any]] | None = None,
    authority_db: str | Path | None = None,
) -> dict[str, Any] | None:
    if authority_db is not None:
        return {
            "kind": "authority_db",
            "authority_db": _file_signature(Path(authority_db)),
        }
    if authority_rows is not None:
        return {
            "kind": "authority_rows",
            "sha256": _fingerprint({"rows": authority_rows}),
        }
    if authority is not None:
        return {
            "kind": "authority_object",
            "sha256": _fingerprint({"authority": authority}),
        }
    return None


def _metadata_cache_dir(*, wave_path: Path, metadata_cache_root: Path | None = None) -> Path:
    root = _metadata_cache_root(metadata_cache_root)
    return root / _fingerprint(_metadata_cache_meta(wave_path=wave_path))


def _query_cache_dir(*, cache_key: dict[str, Any], query_cache_root: Path | None = None) -> Path:
    root = _query_cache_root(query_cache_root)
    return root / _fingerprint(cache_key)


def _metadata_cache_paths(*, cache_dir: Path) -> dict[str, Path]:
    return {
        "signals": cache_dir / "signals.json",
        "scopes": cache_dir / "scopes.json",
        "scope_signal_index": cache_dir / "scope_signal_index.json",
        "cache_meta": cache_dir / "cache_meta.json",
    }


def _query_cache_paths(*, cache_dir: Path) -> dict[str, Path]:
    return {
        "result": cache_dir / "result.json",
        "cache_meta": cache_dir / "cache_meta.json",
    }


def _metadata_cache_matches(cache_dir: Path, expected: dict[str, Any]) -> bool:
    paths = _metadata_cache_paths(cache_dir=cache_dir)
    if not paths["cache_meta"].exists():
        return False
    if any(not path.exists() for key, path in paths.items() if key != "cache_meta"):
        return False
    try:
        existing = _load_json(paths["cache_meta"])
    except json.JSONDecodeError:
        return False
    return existing == expected


def _query_cache_matches(cache_dir: Path, expected: dict[str, Any]) -> bool:
    paths = _query_cache_paths(cache_dir=cache_dir)
    if not paths["cache_meta"].exists() or not paths["result"].exists():
        return False
    try:
        existing = _load_json(paths["cache_meta"])
    except json.JSONDecodeError:
        return False
    return existing == expected


def _persist_metadata_cache(*, cache_dir: Path, cache_meta: dict[str, Any], signals: list[dict[str, Any]], scopes: list[dict[str, Any]]) -> None:
    paths = _metadata_cache_paths(cache_dir=cache_dir)
    _write_json(paths["signals"], signals)
    _write_json(paths["scopes"], scopes)
    _write_json(paths["scope_signal_index"], _build_scope_signal_index(signals, scopes))
    _write_json(paths["cache_meta"], cache_meta)


def _persist_query_cache(*, cache_dir: Path, cache_meta: dict[str, Any], result: dict[str, Any]) -> None:
    paths = _query_cache_paths(cache_dir=cache_dir)
    _write_json(paths["cache_meta"], cache_meta)
    _write_json(paths["result"], result)


def _load_cached_metadata(cache_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = _metadata_cache_paths(cache_dir=cache_dir)
    return _load_json(paths["signals"]), _load_json(paths["scopes"])


def _load_cached_query_result(cache_dir: Path) -> dict[str, Any]:
    return _load_json(_query_cache_paths(cache_dir=cache_dir)["result"])


def _signal_lookup_from_rows(signals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {signal["full_wave_path"]: signal for signal in signals}


def _load_or_build_metadata(
    *,
    waveform: Any,
    wave_path: Path,
    metadata_cache_root: Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]], Path]:
    cache_meta = _metadata_cache_meta(wave_path=wave_path)
    cache_dir = _metadata_cache_dir(wave_path=wave_path, metadata_cache_root=metadata_cache_root)
    if _metadata_cache_matches(cache_dir, cache_meta):
        signals, scopes = _load_cached_metadata(cache_dir)
        return signals, scopes, _signal_lookup_from_rows(signals), cache_dir

    signals, scopes, _signal_by_full_path = _collect_metadata(waveform)
    _persist_metadata_cache(cache_dir=cache_dir, cache_meta=cache_meta, signals=signals, scopes=scopes)
    return signals, scopes, _signal_lookup_from_rows(signals), cache_dir


def _load_waveform(*, wave_path: str | Path, pywellen_module: Any | None = None) -> Any:
    pywellen = pywellen_module or _load_pywellen()
    return pywellen.Waveform(path=str(wave_path))


def _resolve_signal_metadata(*, signal_by_full_path: dict[str, Any], full_wave_path: str) -> tuple[str, dict[str, Any]]:
    for candidate in _scope_path_candidates(full_wave_path):
        signal_info = signal_by_full_path.get(candidate)
        if signal_info is not None:
            return candidate, signal_info
    raise ValueError(f"signal not found in waveform metadata: {full_wave_path}")


def _resolve_focus_signal_ids(*, signals: list[dict[str, Any]], focus_scope: str | None) -> set[str] | None:
    if not focus_scope:
        return None
    allowed_paths = tuple(f"{candidate}." for candidate in _scope_path_candidates(focus_scope))
    selected_ids = {
        signal["signal_id"]
        for signal in signals
        if signal["full_wave_path"].startswith(allowed_paths)
    }
    if not selected_ids:
        raise ValueError(f"focus scope not found in waveform metadata: {focus_scope}")
    return selected_ids


def _load_authority_rows(
    *,
    authority: dict[str, Any] | None = None,
    authority_rows: list[dict[str, Any]] | None = None,
    authority_db: str | Path | None = None,
    touched_paths: list[str],
) -> list[dict[str, Any]]:
    if authority_rows is not None or authority is not None:
        return _normalize_authority_rows(authority_rows=authority_rows, authority=authority)
    if authority_db is None:
        return []
    return _lookup_authority_rows_sqlite(authority_db, touched_paths)


def query_signal_value_from_waveform(
    *,
    wave_path: str | Path,
    full_wave_path: str,
    t: int,
    window_len: int = 1000,
    pywellen_module: Any | None = None,
    metadata_cache_root: Path | None = None,
    query_cache_root: Path | None = None,
) -> dict[str, Any]:
    if t < 0:
        raise ValueError("time must be >= 0")
    if window_len < 1:
        raise ValueError("window_len must be >= 1")

    wave_path = Path(wave_path)
    query_cache_meta = {
        "kind": "signal_value_query",
        "waveform": _file_signature(wave_path),
        "full_wave_path": full_wave_path,
        "t": t,
        "window_len": window_len,
    }
    query_cache_dir = _query_cache_dir(cache_key=query_cache_meta, query_cache_root=query_cache_root)
    if _query_cache_matches(query_cache_dir, query_cache_meta):
        return _load_cached_query_result(query_cache_dir)

    waveform = _load_waveform(wave_path=wave_path, pywellen_module=pywellen_module)
    signals, _scopes, signal_by_full_path, _cache_dir = _load_or_build_metadata(
        waveform=waveform,
        wave_path=wave_path,
        metadata_cache_root=metadata_cache_root,
    )
    resolved_path, signal_info = _resolve_signal_metadata(signal_by_full_path=signal_by_full_path, full_wave_path=full_wave_path)
    signal_row = next(signal for signal in signals if signal["signal_id"] == signal_info["signal_id"])
    sig = waveform.get_signal_from_path(resolved_path)

    latest_change = None
    for change_t, value in sig.all_changes():
        if change_t > t:
            break
        latest_change = {
            "t": change_t,
            "value": _normalize_value(value, signal_info["bit_width"]),
        }

    window_idx = t // window_len
    result = {
        "version": "0.1",
        "query": {
            "full_wave_path": full_wave_path,
            "t": t,
        },
        "signal": {
            "signal_id": signal_row["signal_id"],
            "full_wave_path": resolved_path,
            "local_name": signal_row.get("local_name"),
            "bit_width": signal_row.get("bit_width"),
            "value_kind": signal_row.get("value_kind"),
        },
        "window": {
            "id": f"w{window_idx}",
            "t_start": window_idx * window_len,
            "t_end": ((window_idx + 1) * window_len) - 1,
        },
        "value_at_time": {
            "found": latest_change is not None,
            "t": latest_change["t"] if latest_change is not None else None,
            "value": latest_change["value"] if latest_change is not None else None,
            "status": "ok" if latest_change is not None else "uninitialized_before_time",
        },
    }
    _persist_query_cache(cache_dir=query_cache_dir, cache_meta=query_cache_meta, result=result)
    return result


def build_debug_packet_from_waveform(
    *,
    wave_path: str | Path,
    window_id: str,
    window_len: int,
    focus_scope: str | None = None,
    authority: dict[str, Any] | None = None,
    authority_rows: list[dict[str, Any]] | None = None,
    authority_db: str | Path | None = None,
    pywellen_module: Any | None = None,
    metadata_cache_root: Path | None = None,
    query_cache_root: Path | None = None,
) -> dict[str, Any]:
    if window_len < 1:
        raise ValueError("window_len must be >= 1")

    wave_path = Path(wave_path)
    query_cache_meta = {
        "kind": "packet_query",
        "waveform": _file_signature(wave_path),
        "window_id": window_id,
        "window_len": window_len,
        "focus_scope": focus_scope,
        "authority": _authority_cache_identity(
            authority=authority,
            authority_rows=authority_rows,
            authority_db=authority_db,
        ),
    }
    query_cache_dir = _query_cache_dir(cache_key=query_cache_meta, query_cache_root=query_cache_root)
    if _query_cache_matches(query_cache_dir, query_cache_meta):
        return _load_cached_query_result(query_cache_dir)

    waveform = _load_waveform(wave_path=wave_path, pywellen_module=pywellen_module)
    signals, _scopes, signal_by_full_path, _cache_dir = _load_or_build_metadata(
        waveform=waveform,
        wave_path=wave_path,
        metadata_cache_root=metadata_cache_root,
    )

    focus_signal_ids = _resolve_focus_signal_ids(signals=signals, focus_scope=focus_scope)
    if focus_signal_ids is None:
        focus_signals = signals
    else:
        focus_signals = [signal for signal in signals if signal["signal_id"] in focus_signal_ids]

    window_idx = _window_numeric_id(window_id)
    t_start = window_idx * window_len
    t_end = ((window_idx + 1) * window_len) - 1

    changes: list[dict[str, Any]] = []
    active_signal_ids: set[str] = set()
    touched_focus_paths: set[str] = set()

    iter_signals = focus_signals if focus_signal_ids is not None else signals
    for signal in iter_signals:
        signal_info = signal_by_full_path[signal["full_wave_path"]]
        sig = waveform.get_signal_from_path(signal["full_wave_path"])
        for change_t, value in sig.all_changes():
            if change_t < t_start:
                continue
            if change_t > t_end:
                break
            normalized_value = _normalize_value(value, signal_info["bit_width"])
            changes.append(
                {
                    "t": change_t,
                    "signal_id": signal["signal_id"],
                    "window_id": window_id,
                    "value": normalized_value,
                }
            )
            active_signal_ids.add(signal["signal_id"])
            if focus_signal_ids is None or signal["signal_id"] in focus_signal_ids:
                touched_focus_paths.add(signal["full_wave_path"])

    authority_rows_resolved = _load_authority_rows(
        authority=authority,
        authority_rows=authority_rows,
        authority_db=authority_db,
        touched_paths=sorted(touched_focus_paths),
    )
    store = {
        "version": "0.1",
        "waveform": {"path": str(wave_path)},
        "signals": focus_signals,
        "windows": [
            {
                "id": window_id,
                "t_start": t_start,
                "t_end": t_end,
                "change_count": len(changes),
                "active_signal_count": len(active_signal_ids),
            }
        ],
        "changes": changes,
    }
    result = build_debug_packet(
        store=store,
        authority_rows=authority_rows_resolved,
        window_id=window_id,
        focus_scope=focus_scope,
        scope_already_filtered=True,
    )
    _persist_query_cache(cache_dir=query_cache_dir, cache_meta=query_cache_meta, result=result)
    return result


def load_authority_object(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
