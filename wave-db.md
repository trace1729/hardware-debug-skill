# Legacy Wave DB Path

This document describes the spare waveform-DB path, which is also the legacy VCD-parser path.

## When To Use

Use this path when:

- the user explicitly asks for the legacy VCD parser
- the user explicitly asks to avoid `wellen`
- the user wants persisted waveform artifacts
- repeated cached VCD queries are more valuable than direct querying

Do not rely on this path for FST.

## Commands

Build the waveform DB:

```bash
python scripts/hw_debug_cli.py build-wave-db \
  --waveform /path/to/run.vcd \
  --window-len 1000 \
  [--out-dir <wave-out>]
```

Query a packet from the built DB:

```bash
python scripts/hw_debug_cli.py query-packet \
  --manifest <wave-out>/manifest.json \
  --window-id w42 \
  --out <packet-out>/packet_w42.json \
  [--authority <authority-out>/rtl_authority.sqlite3] \
  [--focus-scope TOP.SimTop.core.rob]
```

Query one signal value from the built DB:

```bash
python scripts/hw_debug_cli.py query-signal-value \
  --manifest <wave-out>/manifest.json \
  --signal TOP.SimTop.core.rob.commit_valid \
  --time 123456
```

## Main Artifacts

Typical outputs are written under:

```text
hardware-debug-waveform/artifacts/wave_db/<fingerprint>/
```

Common files:

- `manifest.json`
- `signals.json`
- `scopes.json`
- `scope_signal_index.json`
- `signal_metadata.sqlite3`
- `windows.json`
- `window_index.json`
- `signal_window_index.json`
- `changes/by_window/wN.jsonl`

## Notes

- This path is VCD-oriented.
- Treat `build-wave-db -> query --manifest` as the legacy VCD-parser flow.
- Prefer direct `--waveform` queries unless the user explicitly wants this path.
