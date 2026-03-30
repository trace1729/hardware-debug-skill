---
name: wavedrom
description: Generate WaveDrom timing diagrams (WaveJSON format) from natural language descriptions or signal specifications. Use this skill whenever the user mentions timing diagrams, waveforms, digital signal timing, clock diagrams, or asks about signals like SPI, I2C, UART, AXI, or any bus/protocol waveform. Also trigger when users say things like "draw a waveform", "show me the timing for...", "diagram the handshake", or describe how digital signals change over time.
---

# WaveDrom Skill

## Overview

This skill produces WaveJSON — the JSON-based format used by [WaveDrom](https://wavedrom.com) — to render timing diagrams for digital signals. Given a plain-English description of signals and their behavior, output a complete, valid WaveJSON block that the user can paste into the WaveDrom editor or embed in Markdown.

## Workflow

1. **Understand the signals** — identify signal names, their type (clock, data, control, bus), and how they change over time relative to each other
2. **Determine the number of time steps** — count how many clock cycles or logical steps are needed to show the behavior clearly
3. **Compose each wave string** — character by character, tracking state transitions
4. **Add labels, groups, edges, and config** as needed
5. **Output a fenced code block** using ` ```json ` so it renders cleanly, then explain what it shows
6. **Optionally render to an image file** — if the user wants a file (SVG/PNG) or Bash tool access is available, offer to run `wavedrom-cli` (see **Rendering to Image Files**)

## WaveJSON Reference

### Basic structure

```json
{ "signal": [
  { "name": "clk",  "wave": "p.....|..." },
  { "name": "data", "wave": "x.345x|=.x", "data": ["head", "body", "tail", "data"] },
  { "name": "req",  "wave": "0.1..0|1.0" },
  {},
  { "name": "ack",  "wave": "1.....|01." }
]}
```

### Wave characters

| Char | Meaning |
|------|---------|
| `0`  | Logic low |
| `1`  | Logic high |
| `x`  | Unknown / undefined |
| `z`  | High impedance (tri-state) |
| `.`  | Continue previous state for one more period |
| `=`  | Multi-bit data (label comes from `data` array) |
| `2`–`9` | Colored data states (label from `data` array, colors cycle) |
| `p`  | Positive clock (rising-edge tick mark) |
| `P`  | Positive clock with arrow |
| `n`  | Negative clock (falling-edge tick mark) |
| `N`  | Negative clock with arrow |
| `h`  | Pull-high (clock high) |
| `l`  | Pull-low (clock low) |
| `u`  | Rising edge (no tick mark) |
| `d`  | Falling edge (no tick mark) |
| `\|` | Gap / break in time axis |

### Signal properties

| Property | Type | Description |
|----------|------|-------------|
| `name`   | string | Signal label (use `""` for a spacer lane with no label) |
| `wave`   | string | Sequence of wave characters |
| `data`   | string or array | Labels for `=` and `2`–`9` states; space-separated string or JSON array |
| `phase`  | number | Fractional time offset (e.g. `0.5` shifts half a period) |
| `period` | number | Stretch each character N times (e.g. `2` doubles the width) |
| `node`   | string | Named anchor characters aligned with the wave, for use in `edge` |

### Top-level properties

| Key      | Description |
|----------|-------------|
| `signal` | Array of signal objects (and group arrays) |
| `edge`   | Array of edge annotation strings connecting nodes |
| `config` | `{ "hscale": N }` — horizontal scale factor (default 1) |
| `head`   | `{ "text": "Title", "tick": 0 }` — diagram header |
| `foot`   | `{ "text": "Caption", "tock": 0 }` — diagram footer |

### Groups and spacers

Wrap signals in an array to create a labeled group:
```json
["Group Name",
  { "name": "sig_a", "wave": "01..." },
  { "name": "sig_b", "wave": "10..." }
]
```

Use `{}` (empty object) or `{ "name": "" }` to insert a blank spacer row between signal groups.

### Edge annotations

Connect named `node` anchors between signals using the `edge` array:
```json
{ "signal": [
  { "name": "A", "wave": "01.", "node": ".a." },
  { "name": "B", "wave": "0.1", "node": "..b" }
],
  "edge": ["a~>b delay"]
}
```

Edge syntax: `"source connector target label"`

| Connector | Style |
|-----------|-------|
| `~`       | Curved line |
| `-`       | Straight line |
| `~>`      | Curved arrow |
| `->`      | Straight arrow |
| `<->`     | Bidirectional arrow |
| `-|>`     | Right-angle arrow |

## Common Patterns

### Clock with enable and data

```json
{ "signal": [
  { "name": "clk",    "wave": "p........" },
  { "name": "enable", "wave": "0..1...0." },
  { "name": "data",   "wave": "x..=====x", "data": ["D0","D1","D2","D3","D4"] }
]}
```

### SPI transaction

```json
{ "signal": [
  { "name": "SCLK", "wave": "0.p...p...p...p...0" },
  { "name": "MOSI", "wave": "x.=...=...=...=...x", "data": ["b7","b6","b5","b4"] },
  { "name": "MISO", "wave": "x.=...=...=...=...x", "data": ["b7","b6","b5","b4"] },
  { "name": "CS",   "wave": "10................1" }
]}
```

### Request/acknowledge handshake

```json
{ "signal": [
  { "name": "req", "wave": "010......", "node": ".a" },
  { "name": "ack", "wave": "0....10..", "node": "....b" }
],
  "edge": ["a~>b propagation delay"]
}
```

### Grouped signals with header

```json
{ "signal": [
  ["Master",
    { "name": "MOSI", "wave": "x345x", "data": ["A","B","C"] }
  ],
  {},
  ["Slave",
    { "name": "MISO", "wave": "x.=.x", "data": ["ACK"] }
  ]
],
  "head": { "text": "SPI Exchange", "tick": 0 },
  "config": { "hscale": 2 }
}
```

## Output Format

Always output the diagram as a fenced code block:

````
```json
{ "signal": [ ... ] }
```
````

After the code block, briefly explain:
- What the diagram shows
- Key timing relationships or events
- How to use it: paste at https://wavedrom.com/editor.html, or use `wavedrom-cli` to render to SVG/PNG (see **Rendering to Image Files** below)

If the user's description is ambiguous about exact timing, make a reasonable assumption and note it — then offer to adjust.

## Rendering to Image Files

If the user wants an SVG or PNG file (not just JSON to paste in the browser), use `wavedrom-cli`.

### Install (once, requires Node.js 14+)

```bash
npm install -g wavedrom-cli
```

### Generate an image

Save the WaveJSON to a file, then render it:

```bash
# Save the diagram
cat > diagram.json5 << 'EOF'
{ "signal": [ ... ] }
EOF

# SVG — crisp, scalable, best for docs and embedding
wavedrom-cli -i diagram.json5 -s diagram.svg

# PNG — raster image
wavedrom-cli -i diagram.json5 -p diagram.png

# Both at once
wavedrom-cli -i diagram.json5 -s diagram.svg -p diagram.png
```

### PDF (via Inkscape)

```bash
wavedrom-cli -i diagram.json5 | inkscape -p --export-filename=diagram.pdf
```

### When to offer this

- User asks to "save", "export", or "generate" a file
- User mentions embedding in a document, CI pipeline, or Makefile
- Bash tool access is available — proactively offer to run it; check first with `which wavedrom-cli` and suggest the install command if it's missing
