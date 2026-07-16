## Context

**当前状态：**
- tinydb MVP 已交付：`src/tinydb/database.py` 提供 `Database.execute(sql) -> list[Row]`；用户通过 Python 脚本或 REPL（如 `python3 -i`）调用。
- `examples/demo.py` 是 35 行一次性脚本，硬编码 3 条 INSERT + 2 条 SELECT + 1 条 DELETE，跑完即退出。
- 终端交互场景缺失：每次想试一条 SQL 都得改脚本重启，调试体验差。

**约束：**
- 零运行时依赖（项目 `dependencies = []` 是 MVP 的硬约束）。
- 不修改 `database.py` / `executor.py` / `parser.py` 等核心模块。
- 跨平台：Unix-like + Windows（readline 行为差异）。
- 教学定位：代码可读性优于花哨技巧。

**利益相关方：**
- 直接用户：tinydb 开发者（写 MVP 的同学）、教学场景的讲师与学生。
- 间接用户：希望快速验证 SQL 语法的潜在贡献者。

## Goals / Non-Goals

**Goals：**
- 通过 `tinydb-repl` console script 启动 REPL，pip install 后即可用。
- 单文件 `src/tinydb/repl.py`（≤ 350 行）实现完整 REPL。
- 复用现有 `Database.execute(sql)`：REPL 不重做 SQL 解析/执行，只是交互壳。
- 元命令：`.exit` / `.quit` / `.help` / `.tables` / `.schema <name>` / `.read <file>`。
- 多行延续：检测未闭合的单引号 `'...` 或左括号 `(...`，进入续行模式（`...>` prompt）直到闭合。
- 持久化历史：Unix 上 `~/.tinydb_history`；Windows 上 fallback 到内存。
- 表格化输出：SELECT 结果按列名对齐（auto-fit，列宽 cap 30）。

**Non-Goals：**
- 不实现语法高亮 / 自动补全（需 prompt_toolkit，破 zero-dep）。
- 不实现 `.mode` 输出格式切换 / `.import` / `.dump` / `.export`。
- 不修改 SQL 语法或执行器行为。
- 不引入 REPL 远程协议 / 多用户 / 协作。
- 不做 Windows readline fallback 完整模拟（pyreadline3 仍是用户自选依赖）。

## Decisions

### D1. REPL 复用 `Database.execute`，不在 REPL 内做 token 累积

- **方案 A（采纳）**：REPL 持一个 `Database` 实例；逐行读入；用启发式判断"语句未闭合"则 append 到 buffer；闭合后一次性 `db.execute(buffer)`。
- **方案 B（拒绝）**：REPL 内 tokenize → 自己管理 `StatementList` 流式执行。
- **理由**：MVP 的 `Database.execute` 已支持 `StatementList`（多 `;` 分隔），增量调用就够。重复实现解析器会偏离"REPL 是壳"的定位。

### D2. 多行延续启发式：未闭合 `'` 或 `(` 触发续行

- **方案 A（采纳）**：扫描当前 buffer，若 `' ` 数为奇数 或 `(` 数 > `)` 数，进入 `...>` prompt 继续累加，直到闭合或 `.exit`。
- **方案 B（拒绝）**：复用 tokenizer 在 readline 之外流式 token 化。
- **理由**：方案 A 简洁（20 行实现），对 MVP SQL 子集足够；边界场景（注释含 `'`、转义 `''`）由 tokenizer 在 execute 阶段处理，REPL 不重复。

### D3. 元命令分发：`.` 前缀 → 内部 dispatcher，**不进** tokenizer

- **方案 A（采纳）**：行首剥离前导空白，若以 `.` 开头则走 `_handle_meta()`；否则走 SQL 路径。
- **方案 B（拒绝）**：把 `.tables` 也塞进 SQL 语法，扩展 parser。
- **理由**：元命令不是 SQL；放进 parser 会污染 AST 形状。剥离后 SQL 路径保持纯净。

### D4. `.schema <name>` 输出用回向生成的 DDL 字符串（基于 catalog JSON）

- **方案 A（采纳）**：从 `Database.catalog.tables[name].schema` 读取，格式化成 `CREATE TABLE name(col1 TYPE1, col2 TYPE2);`。
- **方案 B（拒绝）**：直接打印 catalog JSON 原文。
- **理由**：DDL 形式对用户更友好，与 `CREATE TABLE` 输入对称。

### D5. 持久化历史用 `~/.tinydb_history`（Unix）；Windows 静默 fallback

- **方案 A（采纳）**：Unix 上 `readline.read_history_file()` / `write_history_file()`；Windows 上捕获 `ImportError`，回退到内置 `input()`，每次启动无历史但功能可用。
- **方案 B（拒绝）**：Windows 上要求 `pip install pyreadline3`。
- **理由**：不破 zero-dep 原则；Windows 用户本来就在降级体验中（无 Unix terminal line discipline），能跑即可。

### D6. 表格化输出：auto-fit 列宽，cap 30 字符

- **方案 A（采纳）**：列宽 = min(max(len(header), max(value)), 30)。超长值截断加 `…`。空结果集输出 `(no rows)`。
- **方案 B（拒绝）**：固定宽度 20。
- **理由**：MVP 表通常列少（≤ 5）、值短；auto-fit 比固定宽更易读；30 cap 防止单 cell 撑爆终端。

### D7. 错误显示：`ERROR: <ExceptionClass>: <message>` 单行格式

- **方案 A（采纳）**：捕获 `TinydbError` / `Exception`，打印单行；不打印 traceback（除非 `.debug on`）。
- **方案 B（拒绝）**：完整 traceback。
- **理由**：REPL 用户要快速看到"哪条 SQL 错"，traceback 是噪音。详细错误由 `--debug` flag 触发。

### D8. `__init__.py` 不强制导出 `repl` 模块

- **方案 A（采纳）**：`src/tinydb/repl.py` 是独立模块；`__init__.py` 不改。console script 直接 `tinydb.repl:main`。
- **方案 B（拒绝）**：`from tinydb.repl import main` 加进 `__init__.py`。
- **理由**：REPL 是工具，不是 API 表面；放 `__init__.py` 会让 `import tinydb` 加载额外代码（虽然只是 import 不会执行 main，但增加 import 失败面）。

## Risks / Trade-offs

| Risk | → Mitigation |
|---|---|
| 多行启发式漏判（如 `'\\'` 转义） | 仅在 tokenizer/parser 报错时显示 ERROR；不预判语法合法性 |
| readline 在 Windows 不可用 | 静默 fallback 到内置 input；README 明示 Unix-only 历史 |
| 子进程测试偶发（pexpect 在 CI 不稳） | integration 测试用 `subprocess.Popen` + select / os.read 替代 pexpect；如不可行则降级到手动验证 |
| 表格化对 UTF-8 多字节字符宽计算错位 | 用 `len(str.encode('utf-8'))` 计算字节宽；列分隔用 `│` 时按字节对齐；MVP 数据多为 ASCII，可接受 |
| `repl.py` 突破 350 行预算 | 重构为 `repl/` 子包；预算上限仅软约束，记录到 plan 即可 |

## Migration Plan

N/A。这是纯增量功能，无现有调用方需要迁移。

- 部署：合并到 main 后，`pip install -e ".[dev]"` 即可使用 `tinydb-repl`。
- 回滚：删除 `src/tinydb/repl.py` + `pyproject.toml` 中的 `[project.scripts]` 行。无副作用。

## Open Questions

- **Q1**：`.read <file>` 在文件不存在时的错误格式：`ERROR: FileNotFoundError: seed.sql` vs `ERROR: cannot read file: seed.sql`？倾向后者（与 `.tables` 等元命令一致的 tinydb 风格）。
- **Q2**：是否提供 `--database <path>` CLI flag 覆盖默认 `:memory:`？倾向"是"，让 REPL 启动即绑定文件：`tinydb-repl --database data.db`。
- **Q3**：`.exit` 与 Ctrl-D 的退出码是否区分？倾向都退出码 0。

上述 3 个问题可在 design/build 阶段按需决策，记录到 plan 即可，不阻塞本 open 阶段。