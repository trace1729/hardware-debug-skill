---
name: hardware-debug-waveform
description: Use when analyzing a hardware failure from a VCD waveform, a XiangShan-style Chisel tree, and optionally emitted RTL, especially when the user needs waveform-to-RTL lookup, cache-aware artifact planning, or rough RTL-to-Chisel recovery.
---

# Hardware Debug Waveform

## Use

Use this skill for waveform-first hardware debugging with:

- a VCD file
- a Chisel source tree
- optionally `build/rtl`

Providing `build/rtl` is strongly preferred because it enables exact RTL authority lookup and usually improves analysis accuracy substantially.

Use the two source trees differently:

- use `build/rtl` to build persistent exact RTL ownership artifacts
- use Scala/Chisel source first for actual debugging analysis
- only read SystemVerilog when Scala is insufficient or the mapping is ambiguous

## Start

If the user has not already provided the inputs, ask for:

- VCD path
- Chisel source root
- optional emitted RTL root `build/rtl`
- optional focus scope or debug hint

Recommended prompt:

`Please provide the VCD path, the Chisel source root, and optionally the emitted RTL root (build/rtl), plus any focus scope or debug hint you want me to use.`

## Workflow

Run commands relative to the skill root:

```bash
cd hardware-debug-waveform
python scripts/hw_debug_cli.py inspect-inputs \
  --scala-root /path/to/src/main/scala/xiangshan \
  --vcd /path/to/run.vcd \
  [--rtl-root /path/to/build/rtl] \
  [--focus-scope TOP....rob] \
  [--suggestion "hang near rob tail"]
```

Then follow this order:

1. Run `inspect-inputs`.
2. Build or reuse the waveform DB.
3. If `build/rtl` exists, build or reuse RTL authority. Prefer this path.
4. Query a debug packet for the target window.
5. Use the packet's RTL match to search the relevant Scala/Chisel source first.
6. If available, apply rough Chisel mapping as a separate, lower-confidence step.
7. Only inspect emitted SystemVerilog if Scala does not explain the behavior well enough.

If `inspect-inputs` warns that the artifacts are large, tell the user before running expensive preprocessing.

## Rules

- Let the helper choose default artifact locations unless the user wants explicit paths.
- Reuse cache when available; rebuild only when needed or when the user explicitly wants it.
- Treat `rtl_authority.sqlite3` matches as exact RTL ownership.
- Use exact RTL ownership to narrow the search, then analyze the corresponding Scala/Chisel code first.
- If no `build/rtl` is provided, label the result as `waveform-only analysis`.
- Treat rough Chisel joins as guesses, not exact source truth.
- Avoid reading large SystemVerilog files unless Scala-first analysis is blocked.

Preferred wording:

- `exact RTL match`
- `waveform-only analysis`
- `rough Chisel candidate`
- `unresolved`

## Output

Keep artifact discussion short. Focus on RTL analysis.

The answer must begin with a concise summary, then follow with a more detailed analysis that expands on that summary.

Preferred response order:

1. one short artifact-status line
2. suspected RTL module or focus scope
3. short summary of the relevant waveform change pattern
4. Scala/Chisel-side analysis of the likely cause
5. rough Chisel candidates only if helpful

When `build/rtl` is available:

- use RTL matches to locate the right area
- explain the likely cause mainly from Scala/Chisel source
- mention SystemVerilog only if it adds necessary clarification

Use precise hardware terminology:

- for signals and timing, prefer digital-circuit terms such as `rising edge`, `falling edge` when appropriate
- for the overall design, prefer computer-architecture terms such as `pipeline stage`, `hazard detection`, `forwarding`, `cache hierarchy`, `fetch/decode/execute`, `instruction set architecture`, `bus arbitration`, `memory consistency`, `reorder buffer`, `issue queue`, and `commit/retire` when appropriate
- avoid vague wording when a standard circuit or architecture term is available

Do not dump long lists of exact RTL signals, raw value transitions, or detailed per-cycle changes unless the user explicitly asks.

Summarize value changes at a higher level, for example:

- which small set of signals or sub-blocks became active or stopped changing
- whether progress stopped, oscillated, or diverged from the expected pattern
- whether the behavior points to stall, backpressure, invalid handshake, wrong state transition, or bad control flow

Prefer interpretation over raw evidence listing.

## Reference

For command details, artifact layout, and schema, use:

- `README.md`
- `README_cn.md`
