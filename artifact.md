
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
