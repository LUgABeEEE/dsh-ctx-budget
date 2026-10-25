# dsh-ctx-budget

审计并压缩 agent 每轮请求的**静态上下文**。一个会话在真正开始干活之前，就已经背着
六层常驻内容：规则（AGENTS.md）、记忆库、技能元数据、工具 schema、会话日志、知识库。
这些内容每一轮都要重新进 prompt，占用预算、推高成本，还会因为过期链接和重复条目
把模型往错误方向带。

`dsh-ctx-budget` 把这六层量出来（`baseline`）、找出失效引用（`capabilities`）、
压缩技能描述（`skills`）、体检并分层归档记忆（`memory-audit` / `memory-archive`），
再从真实会话日志里推算工具面与冷启动静态上下文（`probe`）。

> **这是从生产环境抽出来的工具**：本机私有脚本、危险系统改动、机器专属断言都已删除，
> 所有路径都走 `--dsh-home` / `--workspace` / `--out` / 环境变量。
> **默认只读、默认 dry-run**；任何写操作都必须显式加 `--apply`，写前自动备份并打印备份路径。

## 为什么值得做

- **预算是真的**：规则 + 记忆 + 技能描述 + 工具 schema 每轮都在，删掉一条过期指针，
  收益是「每一轮」的，而不是一次性的。
- **过期引用会骗人**：能力地图里 `tools/old-script.py` 这种 token，脚本删了地图没删，
  模型会照着它去调用不存在的东西。静态校验比等到执行失败便宜得多。
- **记忆会烂**：短期批量抽取的条目、重复条目、带「今天/还剩 3 天」的时效性措辞，
  会持续挤占注入配额；而「必须/禁止/红线」这类长期约束绝不能被误删。
- **工具面测不全就别说测全了**：日志里的工具 description 是截断存法，system prompt 根本不落盘。
  本工具只报能算的，算不出来就输出 `null`（见「限制」）。

## 安装

Python >= 3.10，**只用标准库**，无运行时依赖。

```bash
python -m pip install -e .
# 或
pipx install .
```

安装后得到命令 `ctxbudget`；不安装也可以直接 `python -m ctxbudget ...`。

## 快速开始

```bash
ctxbudget doctor                                # 先看主目录和依赖是否齐全（只读）
ctxbudget baseline --workspace .                # 六层静态上下文快照
ctxbudget capabilities --doc CAPABILITIES.md    # 校验能力地图里的失效路径/命令
```

主目录解析优先级固定为：`--dsh-home DIR` > 环境变量 `$DSH_HOME` > `~/.dsh`。

## 作为 DSH 插件使用

同一个仓库也是一个 DSH 插件包（`package.json` 里声明了 `dsh.bundle`）：装上以后模型可以直接
调用四个**只读**工具，不需要自己记命令行。

```bash
dsh plugin add --profile web \
  https://github.com/LUgABeEEE/dsh-ctx-budget/releases/download/v0.2.0/dsh-ctx-budget-0.2.0.tgz
```

| 工具 | 作用 |
| --- | --- |
| `ctx_budget_report` | 六层静态上下文快照（规则 / 记忆 / 技能 / 工具面 / 会话 / KB） |
| `ctx_budget_audit` | 记忆库只读体检：无 tags、重复内容、超 90 天、超限文件 |
| `ctx_budget_check` | 校验能力地图里的失效路径与命令 |
| `ctx_budget_doctor` | 主目录布局与 Python 运行时自检 |

插件自带 `ctxbudget/` 这份 Python 代码，用宿主上的 `python3` 执行；换解释器用插件选项
`python`（例如 `python3.12`）或环境变量 `CTXBUDGET_PYTHON`。**`memory-archive` 不暴露给模型**——
移动条目必须由人显式跑 `--apply`，四个工具全部只读。

插件侧测试与 CLI 分开跑：`node --test`（14 个用例：参数拼装、解释器解析、缺 Python、
四个工具的注册与实际调用）。


## 子命令

### doctor

检查主目录、六个层的目录、`zstdcat/zstd` 是否可用。找不到主目录时给出提示并返回 1，
不会抛栈。

```bash
ctxbudget doctor
ctxbudget doctor --dsh-home ~/.dsh --workspace .
ctxbudget doctor --dsh-home ~/.dsh --json
```

### capabilities

解析 markdown 里的行内反引号 token，校验两类东西：

- 绝对路径 / 相对路径 token：必须真实存在，否则记为 `broken`；
- 单段命令名：`shutil.which` 找得到就记一次 ok；找不到**不算 broken**
  （文档里常把普通英文词包进反引号，硬判会大量误报）。

有 `broken` 时退出码为 1，适合放进 CI。

```bash
ctxbudget capabilities --doc CAPABILITIES.md --base .
ctxbudget capabilities --doc docs/CAPABILITIES.md --base . --json
```

输出示例：

```text
capabilities: CAPABILITIES.md（base=/repo）
checked ok=12 broken=1
  BROKEN relpath tools/old-script.py
         -> /repo/tools/old-script.py
```

### skills

压缩 `SKILL.md` frontmatter 里的 `description`。只改这一个键，其它 frontmatter 与正文原样保留。
压缩策略：超过 `--max-len`（默认 180）时优先断在句末标点；没有合适句末时按长度切，
英文回退到词边界，最后剪掉尾部悬空标点。

```bash
ctxbudget skills                                  # dry-run：只打印 old -> new
ctxbudget skills --root ~/.dsh/skills --max-len 180
ctxbudget skills --root ~/.dsh/skills --apply     # 真正写盘
```

`--apply` 时为每个被修改的文件生成 `SKILL.md.bak-<stamp>`，并把备份路径打印出来。
不引入 PyYAML：包内自带一个只认扁平键的最小 frontmatter 解析器。

### memory-audit

只读体检 `<dsh-home>/memory/global.json` 与 `memory/projects/*.json`：条目分布、
无 tags、完全相同内容重复组、超 90 天条目、超限文件（>300 条或 >20k tokens 估算）。

```bash
ctxbudget memory-audit
ctxbudget memory-audit --json
ctxbudget memory-audit --out ./reports      # 写 markdown + json，不碰记忆库
```

### memory-archive

按分数把低价值条目移出主库，写进 `memory/archive/projects/<key>-<stamp>.json`，
主库留一条「档案索引」指针条目指路（只指路，不编造结论）。

**为什么不是按年龄归档**：生产库里的记忆大量是短期批量抽取的，`createdAt` 几乎一样，
按年龄切分不开价值。能区分价值的是 `useCount` / `updatedAt`（被强化过）/ 类型 /
是否含时效性措辞。因此打分规则里：

- `useCount > 0`、`pinned`、含规范性措辞（必须/禁止/不得/红线/切记…）→ **必留**；
- 每个热 tag 至少留 3 条（tag 出现 >= 10 次）或 1 条，保证主题覆盖；
- 再按分数补到 `--keep`（默认 280），硬上限 `288` 条，给最多 12 条指针留位置，
  归档后单文件总数 `<= 300`；
- 必留条目优先于硬上限：如果必留本身超限，会给出 warning 而不是强行归档规范性条目。

```bash
ctxbudget memory-archive                       # dry-run：只算方案
ctxbudget memory-archive --keep 280
ctxbudget memory-archive --keep 280 --apply    # 真正移动条目
ctxbudget memory-archive --apply --key <project-key> --out ./reports
```

`--apply` 时先按天备份整个 `memory/` 目录到 `<dsh-home>/backup/memory-<date>/`
（同一天复用一份），再写档案与主库（临时文件 + 原子替换），并打印备份与档案路径。
移出的条目**原样保存在档案文件里**，不是删除。

### probe

从会话日志里挖真实数据，不调用模型：

- `request/header.tools`：这次请求实际带的工具数组 → 工具个数与字符数；
- `assistant/message.data.usage`：`inputTokens + cacheReadTokens` = 真实 prompt；
- 冷启动首步（`cacheReadTokens == 0` 且首条 usage 在首轮）可推算
  `static ≈ prompt1 - 首条用户消息 tokens`，算不出就输出 `null`。

```bash
ctxbudget probe
ctxbudget probe --since 7d
ctxbudget probe --since 24h --json
ctxbudget probe --since 7d --out ./reports/probe.json
```

`.jsonl` 直接读；`.jsonl.zstd` 需要 `zstdcat`（或 `zstd -dc`），缺失时跳过并在输出里说明。

### baseline

六层只读快照，可选附带成本层：

| 层 | 内容 | 数据来源 |
| --- | --- | --- |
| L1 rules | 全局/项目 AGENTS.md 字节数 | 文件大小 |
| L2 memory | 条目数 / 字符数 / token 估算 | `memory/**.json` |
| L3 skills | 技能数 / 元数据字符数 / token 估算 | `skills/*/SKILL.md` frontmatter |
| L4 tools | 插件条目、停用数、常驻 MCP；schema token = null | 配置 |
| L5 sessions | 会话日志文件数 / 磁盘占用 | `sessions/` |
| L6 kb | 知识库表与行数（只读 sqlite） | `--kb` 指向的库 |
| X2 cost | 余额 / 当日用量 / 最小单轮 tokens（可选） | `--cost` |

```bash
ctxbudget baseline
ctxbudget baseline --workspace . --kb ~/kb
ctxbudget baseline --workspace . --kb ~/kb --cost --json
ctxbudget baseline --workspace . --out ./reports                # 目录：写 baseline-<stamp>.json
ctxbudget baseline --workspace . --out ./reports/baseline.json  # 文件
```

## 估算口径

源码里保留两个常量（`ctxbudget/memory.py`），并注明是**估算**：

```python
CN_CHARS_PER_TOKEN = 2.0   # 中文约 2 字符/token
EN_CHARS_PER_TOKEN = 3.5   # 英文约 3.5 字符/token
```

它们只用于横向比较与找超限，**不等于供应商计费**。真实 token 以 usage 字段为准。

## 安全与回滚

- **默认只读**：`capabilities` / `doctor` / `memory-audit` / `probe` / `baseline` 不写任何文件。
- **默认 dry-run**：`skills` 与 `memory-archive` 不加 `--apply` 只打印计划。
- **写前备份**：技能 → `<file>.bak-<stamp>`；记忆 → `<dsh-home>/backup/memory-<date>/`。
- **不删数据**：归档 = 移出主库 + 原样写进档案文件 + 指针条目；回滚只需把档案里的条目搬回。
- **原子写**：`memory` 主库与技能文件都先写 `.tmp` 再 `os.replace`。
- **不联网**：全部离线运行，不调用模型、不访问网络。

退出码：`0` 正常；`1` 检出问题（`capabilities` 有 broken、`doctor` 找不到主目录、
`skills --apply` 写失败）；`2` 参数或输入文件错误。

## 限制（请当作已知事实，而不是免责声明）

1. **工具 description 是被截断的**：会话日志里的 `request/header.tools` 存不下长描述，
   所以 `tools_chars` / `tools_tokens_est` 只是**下界**，真实工具面会更大。
2. **system prompt 不落盘**：日志里的 `system/message` 正文是空的，无法直接量。
3. **静态上下文只能从冷启动推算**：非冷启动（`cacheReadTokens > 0`）时无法把
   「缓存读 + 新输入」拆成静态部分，输出 `null`，绝不编数。
4. **字符/token 是估算**；中文/英文混排时误差更明显。
5. **只认识 DSH 风格布局**：`memory/*.json`、`skills/*/SKILL.md`、`sessions/**/session*.jsonl[.zstd]`、
   `profiles/web/cordis.patch.yml`。别的 agent 框架需要自己改读取层。
6. **`.jsonl.zstd` 依赖外部 `zstdcat`/`zstd`**；没有就跳过压缩日志。
7. **归档打分是启发式**：分数、必留规则、`288` 预算都是生产经验值，不是唯一正确答案；
   必留条目本身超限时不会强删。
8. **不自动改 AGENTS.md、不清理备份**；备份文件需要自己按需删除。
9. **`skills` 的 frontmatter 解析只支持扁平键**（含 `description: >` / `|` 块标量）；
   复杂嵌套 YAML 请先用 PyYAML 校验。

## 开发与测试

```bash
python -m unittest discover -s tests -v
python -m ctxbudget --help
```

测试全部使用临时目录与合成数据，不依赖本机任何真实记忆库/会话日志。
CI 在 Ubuntu 上跑 Python 3.10 / 3.11 / 3.12 / 3.13，`fail-fast: false`，
除单测外还有一组基于临时目录的冒烟。

> 自检提示：Python 会把源码绝对路径写进 `__pycache__/*.pyc`，所以「仓库里没有本机路径」的
> grep 检查请先删掉 `__pycache__`（或加 `--exclude-dir=__pycache__`）；`.gitignore` 已忽略这些产物。

## 许可

[MIT](LICENSE)

## English summary

`dsh-ctx-budget` is a small, standard-library-only Python CLI that audits the **static
context budget** an agent pays on every request: rules, memory, skill metadata, tool
surface, session logs and knowledge base.

Commands: `doctor` (environment check), `capabilities` (verify backticked paths and
commands in a markdown capability map), `skills` (compress `SKILL.md` descriptions with
backups), `memory-audit` (read-only memory report: duplicates, stale entries, over-limit
files), `memory-archive` (score-based tiering with archive files and pointer entries),
`probe` (recover the real tool surface and cold-start static context from session logs),
and `baseline` (six-layer snapshot, optional cost layer).

**Read-only by default; writes require `--apply` and are always backed up first.**
Token counts are estimates (2 chars/token for CJK, 3.5 for English). Tool descriptions in
logs are truncated and system prompts are not persisted, so tool-surface numbers are a
lower bound and static-context numbers exist only for cold-start sessions (otherwise
`null`). Paths are never hard-coded: use `--dsh-home` / `--workspace` / `--out` or
`$DSH_HOME`. Licensed MIT.
