# Hardware Debug Waveform Skill

## 一句话总结

这个 skill 是一个可移植的硬件调试工作流：它先校验输入，再在有 `build/rtl` 时从 emitted RTL 构建精确 RTL authority 表，把 VCD 转成可查询的波形数据库，最后生成把波形证据、层级信息和 ownership 提示打包在一起的 debug packet。

## 这个 Skill 用来做什么

适用场景：

- 你有一个 VCD 波形
- 你有 XiangShan 风格的 Chisel 源码树
- 你可能还有 emitted RTL 目录，比如 `build/rtl`

核心目标是让 LLM 能真正“读”大波形，而不是直接硬啃超大的原始 VCD，也不是去分析可读性很差的 generated Verilog。

这个 skill 具备可移植性：

- 对外暴露的脚本都在 skill 目录内
- 命令都以 skill 根目录为相对路径
- 即使没有 `build/rtl`，也能以 waveform-only 模式运行

精度说明：

- 强烈建议提供 `build/rtl`，因为这样可以启用 exact RTL authority lookup，通常会明显提升最终分析的准确性
- 如果没有 `build/rtl`，流水线仍然可以分析波形证据，但无法给出 exact RTL ownership

## 目录结构

```text
hardware-debug-waveform/
├── SKILL.md
├── README.md
├── README_cn.md
└── scripts/
    ├── hw_debug_cli.py
    ├── plan_hw_debug_artifacts.py
    └── lib/
        ├── build_rtl_authority.py
        ├── ingest_waveform.py
        ├── build_debug_packet.py
        ├── rtl_parse_modules.py
        ├── rtl_build_hierarchy.py
        └── stream_vcd_reader.py
```

## 如何使用这个 Skill

### 输入

这个 skill 期望的输入为：

- `--vcd`：VCD 波形文件路径
- `--scala-root`：Chisel 源码树路径，通常是 `src/main/scala/xiangshan`
- `--rtl-root`：可选，emitted RTL 路径，通常是 `build/rtl`

可选输入：

- `--focus-scope`：指定要聚焦的波形层级 scope
- `--suggestion`：人工提供的调试提示，比如 `hang near dispatch`
- `--top`：RTL 顶层模块名，默认 `SimTop`
- `--window-len`：波形切窗长度，默认 `1000`

### 第一步

进入 skill 根目录后，先运行：

```bash
cd hardware-debug-waveform
python scripts/hw_debug_cli.py inspect-inputs \
  --scala-root /path/to/src/main/scala/xiangshan \
  --vcd /path/to/run.vcd \
  --rtl-root /path/to/build/rtl \
  --focus-scope TOP.SimTop.core.rob \
  --suggestion "hang near retire"
```

如果没有 `build/rtl`，就省略 `--rtl-root`：

```bash
python scripts/hw_debug_cli.py inspect-inputs \
  --scala-root /path/to/src/main/scala/xiangshan \
  --vcd /path/to/run.vcd
```

`inspect-inputs` 会做三件事：

- 校验路径是否存在
- 输出 RTL 树和 VCD 的体积信息
- 提示后续应该执行的精确命令

如果 `--rtl-root` 可用，建议一定带上；这是首选模式，因为通常能明显提升 RTL 侧诊断的质量和精度。

## 对外暴露的命令

### `inspect-inputs`

用于检查输入，并打印推荐的命令序列。

基础功能：

- 检查路径是否有效
- 输出文件树大小和 VCD 文件大小
- 当预处理成本较高时给出告警
- 如果没有 `--rtl-root`，自动进入 waveform-only 分析模式

### `build-authority`

从 emitted RTL 构建精确的 RTL authority 表。

基础功能：

- 递归解析 `--rtl-root` 下的 `.sv` 和 `.v`
- 抽取模块、信号声明和实例层级
- 生成精确的层级化 RTL 信号 ownership 数据库

示例：

```bash
python scripts/hw_debug_cli.py build-authority \
  --rtl-root /path/to/build/rtl \
  --top SimTop \
  --out-dir /tmp/hw_debug_rtl_authority
```

### `build-wave-db`

把 VCD 转成规范化的波形数据库。

基础功能：

- 解析 VCD header
- 捕获 header 中声明的全部 traced signal
- 流式读取 value change
- 按时间窗口落盘成可查询的索引和数据分片

示例：

```bash
python scripts/hw_debug_cli.py build-wave-db \
  --vcd /path/to/run.vcd \
  --out-dir /tmp/hw_wave_db \
  --window-len 1000
```

### `query-packet`

针对一个时间窗口生成紧凑的 debug packet。

基础功能：

- 读取一个窗口对应的波形变化
- 可选地关联 exact RTL authority
- 生成适合给 LLM 消费的紧凑 JSON 包

带 exact RTL 的示例：

```bash
python scripts/hw_debug_cli.py query-packet \
  --manifest /tmp/hw_wave_db/manifest.json \
  --authority /tmp/hw_debug_rtl_authority/rtl_authority.sqlite3 \
  --window-id w42 \
  --focus-scope TOP.SimTop.core.rob \
  --out /tmp/hw_packet.json
```

waveform-only 模式示例：

```bash
python scripts/hw_debug_cli.py query-packet \
  --manifest /tmp/hw_wave_db/manifest.json \
  --window-id w42 \
  --out /tmp/hw_packet.json
```

### `rough-map-chisel`

把外部 rough mapping 结果补到 packet 上，形成粗略的 Chisel 候选映射。

基础功能：

- 读取 packet
- 通过 `rtl.module_type + rtl.local_signal_name` 做 join
- 输出 rough Chisel candidate，但不宣称为精确来源

示例：

```bash
python scripts/hw_debug_cli.py rough-map-chisel \
  --packet /tmp/hw_packet.json \
  --mapping /tmp/rough-mapping.json \
  --out /tmp/hw_packet_rough.json
```

## 总体流水线

整个流程分为三个主阶段。

### 阶段一：RTL 解析

这一阶段是可选的，但它提供最强的 exact RTL ownership。

如果 `build/rtl` 可用，应优先走这一条路径，因为它能实质性提高映射准确率。

总体流程：

1. 递归发现 `build/rtl` 下的 emitted RTL 文件。
2. 解析模块定义和信号声明。
3. 从 `--top` 开始构建实例层级。
4. 把模块内的本地信号展开成精确的层级化 RTL 信号名。
5. 将结果写入 JSON 和 SQLite artifact。

这一阶段产出的价值：

- 当名字能对上时，可以得到精确的 waveform-visible RTL ownership
- 拿到 module type
- 拿到 instance path
- 拿到 local RTL signal name
- 拿到源 RTL 文件

### 阶段二：VCD 预处理

这一阶段是规范化波形存储的核心。

总体流程：

1. 解析 VCD header，收集 scope 和 traced signal。
2. 为每个对象分配稳定内部 ID，比如 `sigN`、`scopeN`。
3. 流式遍历 VCD body 中所有 value change。
4. 按固定时间窗口切分，例如 `w0`、`w1`、`w2`。
5. 建立后续快速查询所需的 metadata 和 index。

这一阶段产出的价值：

- VCD header 中所有信号的完整清单
- 完整的 scope 清单
- 可查询的信号元数据
- 按窗口组织的 value change
- 每个信号在每个窗口中的活动摘要

### 阶段三：Packet 生成

这一阶段把一个时间片所需的证据压缩成 LLM 友好的形式。

总体流程：

1. 选择一个窗口，比如 `w42`。
2. 读取该窗口对应的 change shard。
3. 可选地用 `--focus-scope` 缩小范围。
4. 如果 authority 数据库存在，就 join exact RTL ownership。
5. 输出一个适合 LLM 分析的紧凑 JSON packet。

这一阶段产出的价值：

- 时间范围摘要
- 当前窗口真正发生变化的信号
- 若可用则附带 exact RTL ownership
- 若无法证明 ownership，则明确标记 unresolved

## 主要 Artifact 与 Schema

### RTL Authority 相关 Artifact

#### `rtl_authority.sqlite3`

这是主要的 exact RTL 查询数据库。

表名：`authority_lookup`

- `full_signal_name`：精确的层级化 RTL 信号名
- `module_type`：拥有该信号的 emitted RTL 模块类型
- `instance_path`：拥有该信号的实例层级路径
- `local_signal_name`：模块内部的本地信号名
- `signal_kind`：声明类型，例如 wire/reg/port
- `direction`：若是端口，则记录方向
- `decl_width_bits`：声明位宽
- `source_file`：声明该信号的 emitted RTL 文件
- `provenance`：当前固定为 `emitted_rtl_exact`

主要用途：

- 从 waveform path 精确查到 emitted RTL owner

#### `rtl_authority_table.json`

这是 authority 结果的完整 JSON 导出。

顶层结构：

- `version`
- `top`
- `rtl_root`
- `summary`
- `signals`
- `coverage_gaps`

`summary` 包含：

- `rtl_file_count`
- `module_count`
- `signal_count`
- `cached_module_template_count`

`signals` 中每一项字段与 `authority_lookup` 表一致。

#### `rtl_authority_index.json`

这是以精确层级信号名为 key 的字典版本。

结构示意：

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

主要用途：

- 不方便使用 SQLite 时，直接用 JSON 做 exact lookup

### Waveform DB 相关 Artifact

#### `manifest.json`

这是整个 waveform DB 的入口文件。

顶层结构：

- `version`
- `waveform`
- `summary`
- `tables`

`waveform` 包含：

- `path`
- `format`

`summary` 包含：

- `signal_count`
- `scope_count`
- `window_count`
- `change_count`

`tables` 记录其它 artifact 的路径：

- `signals`
- `signal_metadata_db`
- `scopes`
- `scope_signal_index`
- `windows`
- `window_changes_dir`
- `window_index`
- `signal_window_index`

#### `signals.json`

这是从 VCD header 提取出的信号清单。

每条记录包含：

- `signal_id`：稳定内部 ID，例如 `sig123`
- `vcd_id_code`：VCD 使用的短符号
- `scope_id`：所属 scope ID
- `full_wave_path`：完整层级波形路径
- `local_name`：该 scope 内的本地信号名
- `bit_width`
- `value_kind`：`scalar` 或 `vector`

#### `scopes.json`

这是从 VCD header 提取出的层级 scope 清单。

每条记录包含：

- `scope_id`：稳定内部 ID，例如 `scope12`
- `full_scope_path`：完整层级 scope 路径
- `parent_scope_id`
- `scope_kind`
- `local_name`

#### `scope_signal_index.json`

这是 scope 到 signal 的索引。

结构示意：

```json
{
  "TOP.SimTop.core.rob": ["sig10", "sig11", "sig12"]
}
```

主要用途：

- 快速列出某个 scope 下有哪些 signal

#### `signal_metadata.sqlite3`

这是可查询的 signal metadata 数据库。

表名：`signal_metadata`

- `signal_id`
- `scope_id`
- `full_scope_path`
- `full_wave_path`
- `local_name`
- `bit_width`
- `value_kind`

主要用途：

- 按 scope 或 signal path 查询元数据，而不必一次性加载较大的 JSON

#### `windows.json`

这是每个时间窗口的摘要。

每条记录包含：

- `id`：窗口 ID，例如 `w42`
- `t_start`
- `t_end`
- `change_count`
- `active_signal_count`

主要用途：

- 在打开具体 change shard 前，先找活跃或可疑的窗口

#### `window_index.json`

这是窗口到磁盘分片文件的映射。

每条记录包含：

- `window_id`
- `path`
- `change_count`

主要用途：

- 快速定位某个窗口对应的 JSONL 数据分片

#### `signal_window_index.json`

这是按“信号-窗口”组织的摘要索引。

每条记录包含：

- `signal_id`
- `window_id`
- `first_t`
- `last_t`
- `change_count`

主要用途：

- 判断某个 signal 是否在某个窗口发生变化
- 查看该 signal 在该窗口中的第一次和最后一次变化时间

#### `changes/by_window/wN.jsonl`

这是某一个窗口对应的原始 change shard。

每一行包含：

- `t`：仿真时间
- `signal_id`
- `window_id`
- `value`

主要用途：

- 重建这一时间片内更细粒度的波形活动

### Packet 相关 Artifact

#### `packet.json`

这是单次查询生成的紧凑 debug packet。

顶层结构：

- `version`
- `query`
- `window_summary`
- `focus_signals`
- `notes`

`query` 包含：

- `window_id`
- `focus_scope`

`window_summary` 包含：

- `t_start`
- `t_end`
- `change_count`
- `active_signal_count`

`focus_signals` 中每一项包含：

- `signal_id`
- `full_wave_path`
- `bit_width`
- `changes`
- `rtl`

`changes` 中每一项包含：

- `t`
- `signal_id`
- `window_id`
- `value`

`rtl` 有两种情况：

- exact：
  - `match_status: exact`
  - `module_type`
  - `source_file`
  - `local_signal_name`
- unresolved：
  - `match_status: unresolved`

`notes` 可能包含 unresolved 数量摘要。

#### `rough-join.json`

这是补上 rough Chisel candidate 后的结果。

顶层结构：

- `version`
- `packet_path`
- `mapping_path`
- `signals`

`signals` 中每一项包含：

- `full_wave_path`
- `rtl`
- `rough_chisel`

`rough_chisel` 有两种情况：

- rough：
  - `match_status: rough`
  - `chisel_module`
  - `chisel_path`
  - `rtl_module`
  - `rtl_signal`
  - `notes`
- unresolved：
  - `match_status: unresolved`

## LLM 应该如何使用这些 Artifact

推荐顺序：

1. 先运行 `inspect-inputs`。
2. 构建 waveform DB。
3. 如果有 emitted RTL，就构建 RTL authority。
4. 针对可疑窗口生成 packet。
5. 阅读 `focus_signals[*].changes`，把它当作波形证据。
6. 对 `rtl.match_status == exact` 的条目，把它视为权威的 emitted RTL ownership。
7. 如果有 rough Chisel mapping，只能把它当作候选，不要表述成已证明的 source ownership。

在输出最终调试结论时，artifact 相关内容要尽量少。

推荐输出结构：

- 先用一句很短的话说明当前是 `exact RTL mode` 还是 `waveform-only mode`
- 然后主要聚焦可疑 RTL 模块、精确的信号证据和可能的故障机理
- 如果 rough Chisel candidate 有帮助，再作为很小的补充带上

尽量不要把篇幅花在下面这些内容上：

- artifact 清单
- 大段文件路径
- 预处理实现细节
- schema 说明

除非用户明确要求这些细节。

建议使用的措辞：

- `exact RTL match`
- `waveform-only analysis`
- `rough Chisel candidate`
- `unresolved`

## 性能说明

- 最贵的是第一次 VCD ingestion。
- 后续查询会便宜很多，因为主要依赖窗口分片和索引。
- 对于非常大的波形，输出 artifact 也可能达到多 GB。

## 最小使用示例

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

## 当前限制

- 这个 skill 不能证明 exact Chisel ownership。
- 精确映射目前只到 emitted RTL 为止。
- rough Chisel mapping 本质上是启发式结果，必须明确标注。
- waveform-only 模式可以工作，但不会有 exact RTL ownership。
