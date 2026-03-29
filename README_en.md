# Hardware Debug Waveform Skill

## Summary

This skill is a portable hardware-debug workflow for large VCD waveforms: it validates inputs, builds an exact RTL authority table from emitted RTL when available, converts the waveform into a queryable database, and generates compact debug packets that bundle waveform evidence with hierarchy and ownership hints.

## How to use

### Install Skill

```
mkdir -p ~/.codex/skills/
cd ~/.codex/skills
git clone https://github.com/trace1729/hardware-debug-skill.git
```

```
codex
$Hardware Debug Waveform
```

### Inputs

The skill expects:

- `--vcd`: path to the waveform VCD file
- `--scala-root`: path to the Chisel source tree, usually `src/main/scala/xiangshan`

Optional inputs:

- `--rtl-root`: optional but **highly recommand** path to emitted RTL, usually `build/rtl`
- `--focus-scope`: a waveform hierarchy scope to narrow analysis
- `--suggestion`: a human hint such as `hang near dispatch` or `wrong commit behavior`
- `--top`: RTL top module name, default `SimTop`
- `--window-len`: window size for waveform preprocessing, default `1000`



### Where Artifacts Are Stored

By default, this skill stores outputs under the skill root:

```text
hardware-debug-waveform/artifacts/
├── authority/<fingerprint>/
├── wave_db/<fingerprint>/
└── packets/<fingerprint>/
```

The `<fingerprint>` is derived from the input files and key options.

For example:

- authority cache key uses the RTL tree signature and `--top`
- waveform DB cache key uses the VCD file signature and `--window-len`

You can still override the location explicitly with `--out-dir` or `--out`.

---

> below are detail, can ignore

## Exposed Commands

### `inspect-inputs`

Checks inputs and prints the recommended command sequence.

Basic function:

- verifies that the provided paths exist
- prints tree size and VCD size
- warns when preprocessing may be expensive
- supports waveform-only analysis if `--rtl-root` is omitted

### `build-authority`

Builds an exact RTL authority table from emitted RTL.

Basic function:

- parses `.sv` and `.v` files under `--rtl-root`
- extracts modules, declarations, and instance hierarchy
- emits an exact hierarchical signal ownership database

Example:

```bash
python scripts/hw_debug_cli.py build-authority \
  --rtl-root /path/to/build/rtl \
  --top SimTop
```

Cache behavior:

- if a matching cached authority artifact already exists, the command reuses it instead of rebuilding
- add `--force` to rebuild anyway

Important role:

- this step is for persistent exact waveform-to-RTL ownership
- it is not the preferred source for human or LLM reasoning
- after ownership is known, analysis should move to the relevant Scala/Chisel code first

### `build-wave-db`

Builds a canonical waveform database from the VCD.

Basic function:

- parses VCD metadata
- captures all traced signals in the VCD header
- streams value changes into time windows
- materializes metadata and query indexes on disk

Example:

```bash
python scripts/hw_debug_cli.py build-wave-db \
  --vcd /path/to/run.vcd \
  --window-len 1000
```

Cache behavior:

- if a matching cached waveform DB already exists, the command reuses it instead of rebuilding
- add `--force` to rebuild anyway

### `query-packet`

Builds a compact debug packet for one waveform window.

Basic function:

- loads waveform metadata and changes for one window
- optionally joins exact RTL ownership from an authority database
- emits a compact packet suitable for LLM analysis

Example with exact RTL:

```bash
python scripts/hw_debug_cli.py query-packet \
  --manifest /tmp/hw_wave_db/manifest.json \
  --authority /tmp/hw_debug_rtl_authority/rtl_authority.sqlite3 \
  --window-id w42 \
  --focus-scope TOP.SimTop.core.rob \
  --out /tmp/hw_packet.json
```

Example in waveform-only mode:

```bash
python scripts/hw_debug_cli.py query-packet \
  --manifest /tmp/hw_wave_db/manifest.json \
  --window-id w42 \
  --out /tmp/hw_packet.json
```

### `query-signal-value`

Queries one signal's value at one simulation time.

Basic function:

- resolves the signal from waveform metadata
- finds the window containing the requested time
- walks backward to the most recent change at or before that time
- returns the value if known

Example:

```bash
python scripts/hw_debug_cli.py query-signal-value \
  --manifest /tmp/hw_wave_db/manifest.json \
  --signal TOP.SimTop.core.rob.commit_valid \
  --time 123456
```

### `rough-map-chisel`

Adds rough Chisel candidates to a packet by joining against an external rough mapping artifact.

Basic function:

- reads a packet
- joins on `rtl.module_type + rtl.local_signal_name`
- emits rough Chisel candidates without claiming exact source truth

Example:

```bash
python scripts/hw_debug_cli.py rough-map-chisel \
  --packet /tmp/hw_packet.json \
  --mapping /tmp/rough-mapping.json \
  --out /tmp/hw_packet_rough.json
```

## General Pipeline

The pipeline has three main phases.

### Phase 1: RTL Parsing

This phase is optional, but it provides exact RTL ownership and is the strongest mapping layer.

If `build/rtl` is available, this should be treated as the preferred path because it materially improves mapping accuracy.

However, its main purpose is indexing and ownership recovery, not primary source-level reasoning.

General flow:

1. Recursively discover emitted RTL files under `build/rtl`.
2. Parse module definitions and signal declarations.
3. Build instance hierarchy starting from `--top`.
4. Expand module-local signals into exact hierarchical RTL signal names.
5. Store those results in JSON and SQLite artifacts.

What this phase gives you:

- exact waveform-visible RTL ownership when names match
- module type
- instance path
- local RTL signal name
- source RTL file

How to use that output:

- use it to identify the right module and signal region
- then search the relevant Scala/Chisel source first
- avoid diving into generated SystemVerilog unless Scala leaves an important gap

### Phase 2: VCD Preprocessing

This phase is the canonical waveform ingestion stage.

General flow:

1. Parse the VCD header to collect scopes and traced signals.
2. Assign stable internal IDs such as `sigN` and `scopeN`.
3. Stream all value changes from the VCD body.
4. Partition changes into fixed time windows such as `w0`, `w1`, `w2`.
5. Build metadata files and indexes for quick lookup.

What this phase gives you:

- full signal inventory from the VCD header
- full scope inventory
- queryable signal metadata
- per-window value changes
- per-signal/per-window activity summaries

### Phase 3: Packet Generation

This phase packages only the evidence needed for one debug slice.

General flow:

1. Select one window, for example `w42`.
2. Load the change shard for that window.
3. Optionally narrow to `--focus-scope`.
4. Join exact RTL ownership if an authority database is available.
5. Emit a compact JSON packet for LLM consumption.

What this phase gives you:

- time range summary
- touched signals in the selected window
- exact RTL ownership where available
- unresolved signals where ownership could not be proven

## Main Artifacts And Schemas

### RTL Authority Artifacts

#### `rtl_authority.sqlite3`

Primary exact RTL lookup database.

Table: `authority_lookup`

- `full_signal_name`: exact hierarchical RTL signal name
- `module_type`: emitted RTL module type that owns the signal
- `instance_path`: hierarchical instance path of the owning instance
- `local_signal_name`: signal name local to the module
- `signal_kind`: declaration kind such as wire/reg/port
- `direction`: port direction if applicable
- `decl_width_bits`: declared bit width
- `source_file`: emitted RTL file that declared the signal
- `provenance`: currently `emitted_rtl_exact`

Primary use:

- exact lookup from waveform path to emitted RTL owner

#### `rtl_authority_table.json`

Full JSON export of the authority extraction.

Top-level structure:

- `version`
- `top`
- `rtl_root`
- `summary`
- `signals`
- `coverage_gaps`

`summary` contains:

- `rtl_file_count`
- `module_count`
- `signal_count`
- `cached_module_template_count`

Each row in `signals` contains the same fields as `authority_lookup`.

#### `rtl_authority_index.json`

Dictionary form keyed by exact hierarchical signal name.

Shape:

```json
{
  "SimTop.core.rob.headPtr": {
    "module_type": "...",
    "instance_path": "...",
    "local_signal_name": "...",
    "full_signal_name": "...",
    "signal_kind": "...",
    "direction": "...",
    "decl_width_bits": 8,
    "source_file": "...",
    "provenance": "emitted_rtl_exact"
  }
}
```

Primary use:

- simple JSON-based exact lookup when SQLite is not convenient

### Waveform DB Artifacts

#### `manifest.json`

Entry point for the waveform database.

Top-level structure:

- `version`
- `waveform`
- `summary`
- `tables`

`waveform` contains:

- `path`
- `format`

`summary` contains:

- `signal_count`
- `scope_count`
- `window_count`
- `change_count`

`tables` contains paths to the other artifacts:

- `signals`
- `signal_metadata_db`
- `scopes`
- `scope_signal_index`
- `windows`
- `window_changes_dir`
- `window_index`
- `signal_window_index`

#### `signals.json`

Signal inventory collected from the VCD header.

Each row contains:

- `signal_id`: stable internal ID like `sig123`
- `vcd_id_code`: compact VCD symbol
- `scope_id`: owning scope ID
- `full_wave_path`: full hierarchical waveform path
- `local_name`: local signal name inside its scope
- `bit_width`
- `value_kind`: `scalar` or `vector`

#### `scopes.json`

Hierarchy inventory collected from the VCD header.

Each row contains:

- `scope_id`: stable internal ID like `scope12`
- `full_scope_path`: full hierarchical scope path
- `parent_scope_id`
- `scope_kind`
- `local_name`

#### `scope_signal_index.json`

Scope-to-signal index.

Shape:

```json
{
  "TOP.SimTop.core.rob": ["sig10", "sig11", "sig12"]
}
```

Primary use:

- quickly enumerate which signals belong to a given scope

#### `signal_metadata.sqlite3`

Queryable signal metadata database.

Table: `signal_metadata`

- `signal_id`
- `scope_id`
- `full_scope_path`
- `full_wave_path`
- `local_name`
- `bit_width`
- `value_kind`

Primary use:

- query signal metadata by scope or signal path without loading large JSON files

#### `windows.json`

Summary of each time window.

Each row contains:

- `id`: window ID such as `w42`
- `t_start`
- `t_end`
- `change_count`
- `active_signal_count`

Primary use:

- identify active or interesting windows before opening full change shards

#### `window_index.json`

Maps each window to its on-disk change shard.

Each row contains:

- `window_id`
- `path`
- `change_count`

Primary use:

- locate the JSONL file for a chosen window quickly

#### `signal_window_index.json`

Per-signal/per-window summary index.

Each row contains:

- `signal_id`
- `window_id`
- `first_t`
- `last_t`
- `change_count`

Primary use:

- answer whether a given signal changed in a given window
- find the first and last change time for that signal within the window

#### `changes/by_window/wN.jsonl`

Raw change shard for one window.

Each line contains:

- `t`: simulation time
- `signal_id`
- `window_id`
- `value`

Primary use:

- reconstruct detailed waveform activity for that time slice

### Packet Artifacts

#### `packet.json`

Compact debug packet for one query window.

Top-level structure:

- `version`
- `query`
- `window_summary`
- `focus_signals`
- `notes`

`query` contains:

- `window_id`
- `focus_scope`

`window_summary` contains:

- `t_start`
- `t_end`
- `change_count`
- `active_signal_count`

Each row in `focus_signals` contains:

- `signal_id`
- `full_wave_path`
- `bit_width`
- `changes`
- `rtl`

Each row in `changes` contains:

- `t`
- `signal_id`
- `window_id`
- `value`

`rtl` is either:

- exact:
  - `match_status: exact`
  - `module_type`
  - `source_file`
  - `local_signal_name`
- unresolved:
  - `match_status: unresolved`

`notes` may contain unresolved-count summaries.

#### `rough-join.json`

Packet plus rough Chisel candidates.

Top-level structure:

- `version`
- `packet_path`
- `mapping_path`
- `signals`

Each row in `signals` contains:

- `full_wave_path`
- `rtl`
- `rough_chisel`

`rough_chisel` is either:

- rough:
  - `match_status: rough`
  - `chisel_module`
  - `chisel_path`
  - `rtl_module`
  - `rtl_signal`
  - `notes`
- unresolved:
  - `match_status: unresolved`

## How The LLM Should Use These Artifacts

Recommended order:

1. Run `inspect-inputs`.
2. Build the waveform DB.
3. Build RTL authority if emitted RTL is available.
4. Query a packet for one suspect window.
5. Use `query-signal-value` if you need the value of one signal at one exact time.
6. Read `focus_signals[*].changes` as raw evidence, but summarize the pattern instead of echoing detailed value dumps.
7. Treat `rtl.match_status == exact` as authoritative emitted RTL ownership.
8. Use the matched RTL module and signal names to find the most relevant Scala/Chisel source and analyze that first.
9. If rough Chisel mapping exists, present it only as a candidate, never as proven ownership.
10. Only fall back to SystemVerilog when Scala/Chisel analysis cannot explain the behavior clearly enough.

When writing the final debugging answer, keep artifact discussion very short.

Preferred answer shape:

- one short line on artifact mode, for example `exact RTL mode` or `waveform-only mode`
- then focus mainly on suspected RTL module, a compact summary of the waveform change pattern, and the likely mechanism from Scala/Chisel analysis
- include rough Chisel candidates only as a small follow-up when useful

The final answer should start with a concise summary, followed by a more detailed analysis section that expands the summary.

Use precise terminology:

- for signals and timing, prefer digital-circuit terms such as `rising edge`, `falling edge`, `handshake`, `backpressure`, `stall`, `flush`, `valid`, and `ready`
- for the design and behavior, prefer computer-architecture terms such as `pipeline stage`, `hazard detection`, `forwarding`, `cache hierarchy`, `fetch/decode/execute`, `instruction set architecture`, `bus arbitration`, `memory consistency`, and `commit/retire`

Avoid spending much space on:

- artifact inventories
- file path dumps
- long exact-signal listings
- raw per-cycle value transitions
- long generated SystemVerilog excerpts
- preprocessing mechanics
- schema details

unless the user explicitly asks for those details.

Wording discipline:

- use `exact RTL match` for authority-backed results
- use `waveform-only analysis` when no authority database exists
- use `rough Chisel candidate` for approximate source recovery
- use `unresolved` when the artifact cannot prove the match

## Notes On Performance

- VCD ingestion is the expensive one-time cost.
- Later queries become much cheaper because they operate on window shards and metadata indexes.
- Large artifacts can still produce multi-GB outputs, especially for large waveforms.

## Minimal Example

```bash
cd hardware-debug-waveform

python scripts/hw_debug_cli.py inspect-inputs \
  --scala-root /proj/src/main/scala/xiangshan \
  --rtl-root /proj/build/rtl \
  --vcd /proj/run.vcd

python scripts/hw_debug_cli.py build-authority \
  --rtl-root /proj/build/rtl \
  --top SimTop \
  --out-dir /tmp/hw_debug_rtl_authority

python scripts/hw_debug_cli.py build-wave-db \
  --vcd /proj/run.vcd \
  --out-dir /tmp/hw_wave_db \
  --window-len 1000

python scripts/hw_debug_cli.py query-packet \
  --manifest /tmp/hw_wave_db/manifest.json \
  --authority /tmp/hw_debug_rtl_authority/rtl_authority.sqlite3 \
  --window-id w42 \
  --out /tmp/hw_packet.json
```

## Limitations

- Exact Chisel ownership is not proven by this skill.
- Exact mapping stops at emitted RTL unless another artifact proves more.
- Rough Chisel mapping is heuristic and must be labeled clearly.
- Waveform-only mode still works, but exact RTL ownership will be unavailable.
