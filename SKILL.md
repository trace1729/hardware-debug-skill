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
5. If available, apply rough Chisel mapping as a separate, lower-confidence step.

If `inspect-inputs` warns that the artifacts are large, tell the user before running expensive preprocessing.

## Rules

- Let the helper choose default artifact locations unless the user wants explicit paths.
- Reuse cache when available; rebuild only when needed or when the user explicitly wants it.
- Treat `rtl_authority.sqlite3` matches as exact RTL ownership.
- If no `build/rtl` is provided, label the result as `waveform-only analysis`.
- Treat rough Chisel joins as guesses, not exact source truth.

Preferred wording:

- `exact RTL match`
- `waveform-only analysis`
- `rough Chisel candidate`
- `unresolved`

## Output

Keep artifact discussion short. Focus on RTL analysis.

Preferred response order:

1. one short artifact-status line
2. suspected RTL module or focus scope
3. short summary of the relevant waveform change pattern
4. likely fault mechanism
5. rough Chisel candidates only if helpful

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
