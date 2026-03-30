---
name: hardware-debug-waveform
description: Use when analyzing a hardware failure from a waveform dump (`.vcd` or `.fst`), a XiangShan-style Chisel source tree, and optionally emitted RTL. Handles waveform-to-RTL ownership lookup, cache-aware artifact planning, and rough RTL-to-Chisel recovery.
---

# Hardware Debug Waveform

## Overview

Use this skill to debug hardware failures from large waveform dumps (`.vcd` or `.fst`) with a Scala/Chisel source tree and, when available, emitted RTL.

Core approach:

- use the waveform to identify the failure pattern
- use emitted RTL to recover exact ownership and hierarchy
- use Scala/Chisel source as the primary material for root-cause analysis
- use generated SystemVerilog only as a fallback

## Workflow

### Step0 - Resolve Input

If the user has not already provided them, first try to discover them reliably from local context. Only ask the user when one or more required inputs cannot be found with high confidence.

Required or useful inputs:

- waveform path (`.vcd` or `.fst`)
- Chisel source root
- optional emitted RTL root (`build/rtl`)
- optional focus scope (e.g. `TOP.SimTop.core.rob`) or debug hint

Recommended prompt when discovery is insufficient:

> Please provide the waveform path (`.vcd` or `.fst`), the Chisel source root, and optionally the emitted RTL root (build/rtl), plus any focus scope or debug hint you want me to use.


All commands run from the skill root directory. Use `cd` once at the start:

```bash
cd ~/.codex/skills/hardware-debug-waveform
```

### Step 1 — Inspect inputs

```bash
python scripts/hw_debug_cli.py inspect-inputs \
  --scala-root /path/to/src/main/scala/xiangshan \
  --waveform /path/to/run.fst \
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
  --waveform /path/to/run.fst \
  --window-len 1000 \
  [--out-dir <wave-out>]
```

Reuses cache automatically. Add `--force` to rebuild.

`--vcd` remains available as a compatibility alias for older command lines.

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

### Step 4b — (Optional) Query one signal value at one time

```bash
python scripts/hw_debug_cli.py query-signal-value \
  --manifest <wave-out>/manifest.json \
  --signal TOP.SimTop.core.rob.commit_valid \
  --time 123456
```

Use this when you need the value of one specific signal at one specific simulation time.

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
3. Search the Scala root by module name, signal name, and nearby subsystem names to find the best candidates.
4. Analyze the Scala/Chisel code first.
5. Present rough Chisel candidates from step 5 only as secondary, lower-confidence hints.
6. Only inspect generated SystemVerilog if Scala cannot explain the behavior.

If you need a point lookup instead of a window summary, use `query-signal-value`.


## Output

Write the answer in two parts:

**Summary** (2-4 sentences)

- For a debug request, include:
  - `Phenomenon`: one sentence describing the anomaly seen in the waveform
  - `Root Cause Category`: a standard hardware bug class such as state machine deadlock, data hazard, backpressure stall, or flush-handling miss
  - `Confidence`: state whether this is high confidence or low confidence
- For an exploration request, include:
  - `Function`: what the module does
  - `Structure`: its main internal buffers, state, and submodules
  - `Interconnect`: how it connects to other modules

**Detailed Analysis**

- For a debug request:
  1. Expand the `Root Cause Category` from the summary.
  2. Cite the most relevant waveform evidence and Scala/Chisel logic that support the hypothesis.
  3. Give a fix recommendation if confidence is high.
  4. Otherwise give the next best debugging steps.
- For an exploration request:
  1. Support `Function` by using the Scala/Chisel source to explain what the module does, and by using waveform evidence to analyze its key pipeline signals and timing behavior when sufficient evidence is available.
  2. Support `Structure` with the main state, buffers, queues, or submodules.
  3. Support `Interconnect` with the other modules, or interfaces that matter most.

Use precise terms in the detailed analysis:

- signals/timing: `rising edge`, `falling edge`, `valid`, `ready`, `handshake`, `backpressure`, `stall`, `flush`, `state transition`
- architecture/control: `pipeline stage`, `hazard detection`, `forwarding`, `cache hierarchy`, `fetch/decode/execute`, `instruction set architecture`, `bus arbitration`, `memory consistency`, `reorder buffer`, `issue queue`, `commit/retire`

Avoid: raw per-cycle value dumps, long exact-signal lists, large artifact path inventories, large SystemVerilog excerpts, and preprocessing detail.
Include only the few source files or artifact paths that materially support the analysis.

## Rules

- Let `inspect-inputs` choose default artifact paths; only override when the user asks.
- Reuse cached artifacts; rebuild only when needed or explicitly requested.
- Treat `rtl_authority.sqlite3` matches as exact RTL ownership.
- If no `build/rtl` is provided, label the result `waveform-only analysis`.
- Treat rough Chisel joins as guesses, never as proven ownership.
- Avoid reading large SystemVerilog files unless Scala-first analysis is blocked.

## Reference

For command flags, artifact layout, and schema details:

- `README_en.md` (English reference)
- `README.md` (Chinese reference)

For wavedrom language:

- `wavedrom.md`
