# Hardware Debug Waveform Skill

## Summary

This skill provides a structured hardware-debug workflow that combines waveform evidence, emitted RTL, and Scala/Chisel source code for LLM-assisted analysis.

The recommended path now uses `wellen` for direct waveform queries:

- query one signal value at one simulation time directly from the waveform
- extract one debug packet for a chosen window directly from the waveform
- join exact RTL ownership when emitted RTL is available
- use Scala/Chisel source as the primary material for root-cause analysis

`build-wave-db` is still available, but it is now a spare path for caching, repeated queries, or workflows that need persisted waveform artifacts.

Format note:

- direct `--waveform` queries use `wellen` and support formats readable by `pywellen`, including VCD and FST
- direct `--waveform` queries persist reusable waveform metadata cache under `artifacts/waveform_meta/`
- the spare `build-wave-db` path is still VCD-oriented and should not be treated as a valid FST path

## How To Use

### Install

```bash
mkdir -p ~/.codex/skills/
cd ~/.codex/skills
git clone https://github.com/trace1729/hardware-debug-skill.git hardware-debug-waveform
```

Notes:

- the skill first uses `pywellen` from the active Python environment when available
- if `pywellen` is missing, it tries the bundled local copy under `wellen/pywellen`

### Simple Usage

```text
codex
$Hardware Debug Waveform help me debug xxx.vcd
$Hardware Debug Waveform explain this module with xxx.vcd
$Hardware Debug Waveform avoid pywellen, explain this module with xxx.vcd
```

Required inputs:

- `--scala-root`: Scala/Chisel source tree
- `--waveform`: waveform path for `inspect-inputs` and `build-wave-db`

Common optional inputs:

- `--rtl-root`: **recommand** emitted RTL root, usually `build/rtl`
- `--focus-scope`: waveform hierarchy scope to narrow analysis
- `--suggestion`: human debug hint
- `--top`: RTL top module name, default `SimTop`
- `--window-len`: time-window length, default `1000`
- explicitly specify avoiding pywellen for vcd parsing to save token and improve speed

## Basic Pipeline

### How the LLM uses the artifacts under the hood

1. `inspect-inputs` validates paths, estimates artifact size, and prints the recommended commands.
2. If emitted RTL is available, `build-authority` creates an exact RTL ownership database.
3. The preferred path uses `query-packet --waveform` or `query-signal-value --waveform`, which reads the waveform directly through `wellen`.
4. The generated packet keeps only the relevant changes for the selected window and can attach exact RTL ownership.
5. The LLM then uses `module_type`, `local_signal_name`, and `focus_scope` to locate the relevant Scala/Chisel source.
6. The direct query path reuses metadata cached under `artifacts/waveform_meta/`; `build-wave-db` is only used when fully materialized waveform artifacts are more useful.

For FST input, only the direct `--waveform` path should be used.

## Detailed Analysis

### Subcommand Introduction

#### `inspect-inputs`

Checks inputs and prints the recommended next commands.

- validates `--scala-root`, `--waveform`, and `--rtl-root`
- estimates waveform and source-tree size
- prints default artifact locations
- prints direct waveform query commands first
- keeps `build-wave-db` as a spare path

#### `build-authority`

Builds exact RTL authority from emitted RTL.

- parses `.sv` and `.v`
- reconstructs instance hierarchy
- expands hierarchical signal names
- writes JSON and SQLite lookup artifacts

#### `query-packet`

Builds one debug packet for one time window.

- preferred mode: `--waveform`
- spare mode: `--manifest`
- optional `--authority` join
- optional `--focus-scope` narrowing
- direct packet queries on very large FST files may be slow

#### `query-signal-value`

Queries one signal at one simulation time.

- preferred mode: `--waveform`
- spare mode: `--manifest`
- returns the containing window and the most recent known change at or before the query time

#### `build-wave-db`

Builds a persisted waveform database from VCD.

- now treated as a spare path
- useful for repeated queries, cache reuse, or offline artifacts
- current preprocessing implementation remains VCD-oriented
- should not currently be used as an FST ingestion path

#### `rough-map-chisel`

Adds rough Chisel candidates to a packet.

- joins on `rtl.module_type + rtl.local_signal_name`
- provides candidates only, not exact ownership

### Artifacts And Schema

#### Main Artifacts

`rtl_authority.sqlite3`

Table: `authority_lookup`

Key fields:
- `full_signal_name`
- `module_type`
- `instance_path`
- `local_signal_name`
- `source_file`

`manifest.json`

Entry point for the spare waveform DB flow.

Top-level fields:
- `version`
- `waveform`
- `summary`
- `tables`

`packet.json`

Output of one window query.

Top-level fields:
- `version`
- `query`
- `window_summary`
- `focus_signals`
- `notes`

`query-signal-value` output

Top-level fields:
- `version`
- `query`
- `signal`
- `window`
- `value_at_time`

### Artifacts Directory

Default outputs are stored under:

```text
hardware-debug-waveform/artifacts/
├── authority/<fingerprint>/
├── waveform_meta/<fingerprint>/
├── wave_db/<fingerprint>/
└── packets/<fingerprint>/
```

Meaning:

- `authority/`: cache output from `build-authority`
- `waveform_meta/`: metadata cache used by direct `--waveform` queries
- `wave_db/`: cache output from `build-wave-db`
- `packets/`: default packet output location suggested by the CLI

`<fingerprint>` is derived from file signatures and key options, so identical inputs usually reuse the same cache directory.

### Limitation

- The preferred path uses `wellen`, and the CLI still keeps the historical `--vcd` alias for compatibility.
- Direct `--waveform` queries support VCD and FST.
- `build-wave-db` is still a VCD-oriented preprocessing implementation, has not been replaced by a full `wellen` materialization flow, and should not be treated as FST support.
- Direct `query-packet --waveform` is good for on-demand analysis; prebuilt wave DB artifacts may still be better for heavy repeated queries.
- Direct `query-packet --waveform` may be slow on very large FST files.
- Without `--rtl-root`, the workflow becomes waveform-only and cannot recover exact RTL ownership.
- `rough-map-chisel` remains heuristic and must not be treated as exact source truth.
