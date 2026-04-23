---
name: hardware-debug-waveform
description: Use when analyzing a hardware failure from a waveform dump (`.vcd` or `.fst`), a XiangShan-style Chisel source tree, optional emitted RTL, and optional simulator error logs such as simulator_out.txt. Handles difftest and assert triage, waveform-to-RTL ownership lookup, cache-aware artifact planning, and rough RTL-to-Chisel recovery.
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
- optional error log path such as `simulator_out.txt`
- optional focus scope (e.g. `TOP.SimTop.core.rob`) or debug hint

Recommended prompt when discovery is insufficient:

> Please fill in this debug template and send it back:
>
> ```text
> debug_type: hardware bug debug
> waveform: /path/to/run.fst_or.vcd
> scala-root: /path/to/XiangShan/src/main/scala/xiangshan
> rtl-root: /path/to/XiangShan/build/rtl
> error-log: /path/to/simulator_out.txt
> focus-scope: TOP.SimTop.core.rob
> suggestion: what you suspect or what looks wrong
> top: SimTop
> window-len: 1000
> ```

When a user asks to debug but the required inputs are missing, prefer asking them to fill in the template above instead of asking a vague free-form question.

### Debug Request Template

```text
debug_type: hardware bug debug
waveform: /path/to/run.fst_or.vcd
scala-root: /path/to/XiangShan/src/main/scala/xiangshan
rtl-root: /path/to/XiangShan/build/rtl
error-log: /path/to/simulator_out.txt
focus-scope: TOP.SimTop.core.rob
suggestion: what you suspect or what looks wrong
top: SimTop
window-len: 1000
```


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
  [--error-log /path/to/simulator_out.txt] \
  [--focus-scope TOP.SimTop.core.rob] \
  [--suggestion "hang near rob tail"] \
  [--top SimTop]
```

`inspect-inputs` validates paths, reports artifact sizes, checks cache status, and, when an error log is provided, extracts a likely bug type such as `difftest_error` or `assert_error`.

If the bug type is `difftest_error`, `inspect-inputs` also tries to run the configured difftest disassembler helper and writes `disassembly.txt` next to the input `simulator_out.txt`. It also writes `waveform_search_signals.txt` in the same directory, using Markdown-style headings, bullets, and tables to make the saved waveform-search checklist easy to read.

If the bug type is `assert_error`, `inspect-inputs` also tries to write `assert_debug_guide.md` next to the input `simulator_out.txt`. It should also write `waveform_search_signals.txt` next to the same `simulator_out.txt`, and that file must include the asserted Verilog site, matching Scala/Chisel locations, and the waveform signals most directly involved in the trigger condition.

Then **prints the exact commands to run next**. Use those printed commands as the next steps.

If it warns that artifacts are large, tell the user before proceeding.

### Step 2 — Build RTL authority (skip if no `--rtl-root`)

```bash
python scripts/hw_debug_cli.py build-authority \
  --rtl-root /path/to/build/rtl \
  --top SimTop \
  [--out-dir <authority-out>]
```

Reuses cache automatically. Add `--force` to rebuild.

### Step 3 — Build waveform metadata cache

```bash
python scripts/hw_debug_cli.py build-wave-meta \
  --waveform /path/to/run.fst \
  [--out-dir <meta-out>]
```

Reuses cache automatically. Add `--force` to rebuild.

`--vcd` remains available as a compatibility alias for older command lines.

### Step 4 — Query a debug packet

```bash
python scripts/hw_debug_cli.py query-packet \
  --waveform /path/to/run.fst \
  --focus-scope TOP.SimTop.core.rob \
  --t-start 123000 \
  --t-end 124000 \
  --out <packet-out>/packet_t123000_124000.json \
  [--meta-dir <meta-out>] \
  [--authority <authority-out>/rtl_authority.sqlite3] \
```

Choose `t-start` and `t-end` to cover the suspected failure region. Use a narrower range when you want a compact packet for LLM analysis.

### Step 4b — (Optional) Query one signal value at one time

```bash
python scripts/hw_debug_cli.py query-signal-value \
  --waveform /path/to/run.fst \
  --signal TOP.SimTop.core.rob.commit_valid \
  --time 123456 \
  [--meta-dir <meta-out>]
```

Use this when you need the value of one specific signal at one specific simulation time.

### Step 5 — (Optional) Add rough Chisel candidates

```bash
python scripts/hw_debug_cli.py rough-map-chisel \
  --packet <packet-out>/packet_t123000_124000.json \
  --mapping /path/to/rough-mapping.json \
  --out <packet-out>/packet_t123000_124000_rough.json
```

Only run this step if a rough mapping artifact is available. Treat results as guesses, not exact source truth.

### Step 6 — Analyze

1. If an error log is available, use its bug-type hint as a prior, not as proof.
2. For `assert_error`, start from the asserted Verilog line first, recover the actual trigger condition, locate the corresponding Scala/Chisel file and line, and explain why that condition became true.
3. For `difftest_error`, first locate the mismatching instruction from `simulator_out.txt` and print it explicitly.
4. If `disassembly.txt` was generated, use it to recover the mismatching instruction's assembly and nearby instruction stream before diving into waveform details.
5. Search the ROB commit path in Scala first for `difftest_error`. Prioritize:
   - `backend/rob/RobBundles.scala` for `commit_v`, `commit_w`, `debug_pc`, `debug_instr`, `rfWen`, `commitType`
   - `backend/rob/Rob.scala` for `io.commits.commitValid`, `io.commits.isCommit`, `io.commits.robIdx(i)`, `io.commits.info(i).debug_pc`, `io.commits.info(i).debug_instr`
   - `backend/CtrlBlock.scala` for `frontendCommit`, `rob.io.flushOut`, and redirect/flush timing
6. Read waveform evidence with `query-packet` or `query-signal-value`.
7. If `rtl.match_status == "exact"`, use `module_type` and `local_signal_name` to narrow the search to the most relevant Scala/Chisel source candidates.
8. Search the Scala root by module name, signal name, and nearby subsystem names to find the best candidates.
9. Analyze the Scala/Chisel code first.
10. Present rough Chisel candidates from step 5 only as secondary, lower-confidence hints.
11. Only inspect generated SystemVerilog if Scala cannot explain the behavior.

If you need a point lookup instead of a time-range packet, use `query-signal-value`.


## Output

Write the answer in two parts:

**Summary** (2-4 sentences)

- For a debug request, include:
  - `Bug Type Hint`: if an error log was provided, report `difftest_error`, `assert_error`, or `unknown`
  - `Assert Site`: for `assert_error`, report the asserted Verilog file and line
  - `Mismatching Instruction`: for `difftest_error`, report the mismatching PC and instruction
  - `Phenomenon`: one sentence describing the anomaly seen in the waveform
  - `Root Cause Category`: a standard hardware bug class such as state machine deadlock, data hazard, backpressure stall, or flush-handling miss
  - `Confidence`: state whether this is high confidence or low confidence
- For an exploration request, include:
  - `Function`: what the module does
  - `Structure`: its main internal buffers, state, and submodules
  - `Interconnect`: how it connects to other modules

**Detailed Analysis**

- For a debug request:
  1. Expand the `Bug Type Hint`, `Assert Site` / `Mismatching Instruction`, and `Root Cause Category` from the summary.
  2. For `assert_error`, explain the trigger condition from emitted Verilog first, then show the matching Scala/Chisel logic.
  3. For `difftest_error`, explain the ROB commit-path chain you traced from Scala/Chisel to waveform.
  4. Cite the most relevant error-log clues, waveform evidence, and Scala/Chisel logic that support the hypothesis.
  5. Give a fix recommendation if confidence is high.
  6. Otherwise give the next best debugging steps.
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
- If `--error-log` is provided, use it to infer a likely bug type, but do not treat it as sufficient proof.
- If the error log indicates `assert_error`, treat the asserted RTL file/line as the first narrowing clue.
- If the error log indicates `assert_error`, try to generate `assert_debug_guide.md` and `waveform_search_signals.txt` in the same directory as `simulator_out.txt`.
- If the error log indicates `difftest_error`, use mismatch or abort PC as a starting hint, but confirm the root cause from waveform and source.
- If the error log indicates `difftest_error`, try to generate `disassembly.txt` and `waveform_search_signals.txt` in the same directory as `simulator_out.txt`.
- If writing helper files fails because the target directory is not writable, tell the user the exact target path and ask for permission. Do not silently write fallback copies to hidden temporary locations.
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
