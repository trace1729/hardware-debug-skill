---
name: hardware-debug-waveform
description: Use when analyzing a hardware failure from a VCD waveform, a XiangShan-style Chisel source tree, and optionally emitted RTL. Handles waveform-to-RTL ownership lookup, cache-aware artifact planning, and rough RTL-to-Chisel recovery.
---

# Hardware Debug Waveform

## Purpose

Use this skill for waveform-first hardware debugging with:

- a VCD file
- a Chisel source tree (e.g. `src/main/scala/xiangshan`)
- optionally emitted RTL (`build/rtl`)

When `build/rtl` is available, prefer it: it enables exact RTL ownership lookup and materially improves analysis accuracy.

Source trees serve different roles:

- `build/rtl` — build persistent exact RTL ownership artifacts only
- Scala/Chisel source — primary source for debugging analysis
- generated SystemVerilog — fallback only when Scala is insufficient or mapping is ambiguous

## Start

If the user has not already provided them, ask for:

- VCD path
- Chisel source root
- optional emitted RTL root (`build/rtl`)
- optional focus scope (e.g. `TOP.SimTop.core.rob`) or debug hint

Recommended prompt:

> Please provide the VCD path, the Chisel source root, and optionally the emitted RTL root (build/rtl), plus any focus scope or debug hint you want me to use.

## Workflow

All commands run from the skill root directory. Use `cd` once at the start:

```bash
cd ~/.codex/skills/hardware-debug-waveform
```

### Step 1 — Inspect inputs

```bash
python scripts/hw_debug_cli.py inspect-inputs \
  --scala-root /path/to/src/main/scala/xiangshan \
  --vcd /path/to/run.vcd \
  [--rtl-root /path/to/build/rtl] \
  [--focus-scope TOP.SimTop.core.rob] \
  [--suggestion "hang near rob tail"] \
  [--top SimTop] \
  [--window-len 1000]
```

`inspect-inputs` validates paths, reports artifact sizes, checks cache status, and **prints the exact commands to run next**. Use those printed commands as the next steps.

If it warns that artifacts are large, tell the user before proceeding.

### Step 2 — Build RTL authority (skip if no `--rtl-root`)

```bash
python scripts/hw_debug_cli.py build-authority \
  --rtl-root /path/to/build/rtl \
  --top SimTop \
  [--out-dir <authority-out>]
```

Reuses cache automatically. Add `--force` to rebuild.

### Step 3 — Build waveform DB

```bash
python scripts/hw_debug_cli.py build-wave-db \
  --vcd /path/to/run.vcd \
  --window-len 1000 \
  [--out-dir <wave-out>]
```

Reuses cache automatically. Add `--force` to rebuild.

### Step 4 — Query a debug packet

```bash
python scripts/hw_debug_cli.py query-packet \
  --manifest <wave-out>/manifest.json \
  --window-id w42 \
  --out <packet-out>/packet_w42.json \
  [--authority <authority-out>/rtl_authority.sqlite3] \
  [--focus-scope TOP.SimTop.core.rob]
```

Use the window ID that covers the suspected failure. Check `windows.json` to find active windows if unsure.

### Step 5 — (Optional) Add rough Chisel candidates

```bash
python scripts/hw_debug_cli.py rough-map-chisel \
  --packet <packet-out>/packet_w42.json \
  --mapping /path/to/rough-mapping.json \
  --out <packet-out>/packet_w42_rough.json
```

Only run this step if a rough mapping artifact is available. Treat results as guesses, not exact source truth.

### Step 6 — Analyze

1. Read `focus_signals[*].changes` as raw waveform evidence.
2. If `rtl.match_status == "exact"`, use `module_type` and `local_signal_name` to narrow the search to the most relevant Scala/Chisel source candidates.
3. Analyze the Scala/Chisel code first.
4. Present rough Chisel candidates from step 5 only as secondary, lower-confidence hints.
5. Only inspect generated SystemVerilog if Scala cannot explain the behavior.

## Rules

- Let `inspect-inputs` choose default artifact paths; only override when the user asks.
- Reuse cached artifacts; rebuild only when needed or explicitly requested.
- Treat `rtl_authority.sqlite3` matches as exact RTL ownership.
- If no `build/rtl` is provided, label the result `waveform-only analysis`.
- Treat rough Chisel joins as guesses, never as proven ownership.
- Avoid reading large SystemVerilog files unless Scala-first analysis is blocked.

## Output

Write the answer in three parts:

**Summary** (2–4 sentences)

**One short artifact-status line** (e.g. `exact RTL mode` or `waveform-only mode`)

**Detailed Analysis**: expand the analysis upon the summary


Use precise terms in detailed analysis:

- signals/timing: `rising edge`, `falling edge`, `valid`, `ready`, `handshake`, `backpressure`, `stall`, `flush`, `state transition`
- architecture/control: `pipeline stage`, `hazard detection`, `forwarding`, `cache hierarchy`, `fetch/decode/execute`, `instruction set architecture`, `bus arbitration`, `memory consistency`, `reorder buffer`, `issue queue`, `commit/retire`

Avoid: raw per-cycle value dumps, long exact-signal lists, artifact path inventories, large SystemVerilog excerpts, preprocessing detail.

## Reference

For command flags, artifact layout, and schema details, see `README.md` (English) or `README_cn.md` (Chinese).
