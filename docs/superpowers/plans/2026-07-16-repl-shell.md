---
change: repl-shell
design-doc: docs/superpowers/specs/2026-07-16-repl-shell-design.md
base-ref: a14dec13620f81639857f9bb9dfbecd93c86c42f
archived-with: 2026-07-16-repl-shell
---

# repl-shell 实施计划

> **供执行代理使用：** 必须加载 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，逐个任务实施本计划；使用复选框（`- [ ]`）跟踪步骤。每个可提交子任务验收后，先定向勾选 `openspec/changes/repl-shell/tasks.md` 中对应的唯一复选框，再创建该子任务的唯一提交。

**目标：** 在不修改 tinydb MVP 核心模块的前提下，交付零运行时依赖的 `tinydb-repl` 交互式 SQL shell，覆盖多行输入、元命令、历史、表格输出、文件数据库和单行错误恢复。

**架构：** 新增单一壳层模块 `src/tinydb/repl.py`，所有 SQL 仍经 `Database.execute(sql)` 执行；REPL 只负责输入状态机、AST peek 结果分派、元命令和终端 I/O。单元测试直接覆盖纯逻辑和受控 I/O，集成测试通过已安装的 `tinydb-repl` console script 驱动真实子进程；核心模块保持不变。

**技术栈：** Python 3.11+、stdlib（`os`、`sys`、`pathlib`、可选 `readline`、`subprocess`）、现有 `tinydb` API；开发依赖沿用 `pytest`、`pytest-cov`。运行时依赖保持为零。

---

## 0. 实施边界与权威来源

1. 实现细节直接采用 Design Doc §1–§14 的既有探查结论；实施任务中**不重复探查** `Row`、`Database(path)`、异常字符串、AST `Select` 或空 SELECT/DML 的返回差异。
2. 当 `tasks.md` 的早期措辞与 Design Doc 的最终决策不同，以 Design Doc 和 delta spec 为准：
   - `_make_prompt` 接收已解析的 `db_path`，不反向探查 `Database` 内部字段。
   - `_is_unterminated` 使用 SQL-aware 字符状态机，不采用朴素奇偶计数。
   - `.read` 复用 `_is_unterminated` 与 `_run_sql`；测试阶段再以 RED 用例收紧“同一物理行多语句、逐条反馈”。
   - CLI 仅支持 `--database <path>`，不支持位置参数；README 示例也只使用该 flag。
   - `src/tinydb/__init__.py` 不导出 REPL，不修改任何 MVP 核心模块。
3. **无 Spec Patch：** Design Doc §12 已确认 8 requirements × 26 scenarios 与现有 API 一致，本阶段不修改 `openspec/changes/repl-shell/specs/repl-shell/spec.md`。
4. **行数预算红线：** `src/tinydb/repl.py` 必须 `≤ 350` 行；每个源码提交前执行 `wc -l src/tinydb/repl.py`，超限时立即停止后续任务并在当前任务内压缩/拆分，未恢复到 `≤ 350` 不得验收。
5. 覆盖率硬门槛：合并测试 `≥ 85%`；MVP 基线为 `93.33%`，REPL 模块目标 `≥ 90%`，不得以排除文件或降低 `pyproject.toml` 门槛通过验收。

## 1. 文件结构（实施前锁定）

| 文件 | 操作 | 单一职责 |
|---|---|---|
| `src/tinydb/repl.py` | 新建 | CLI、交互循环、SQL-aware 缓冲、元命令、历史、输出；硬上限 350 行 |
| `pyproject.toml` | 修改 | 注册 `tinydb-repl = "tinydb.repl:main"`；不加依赖 |
| `tests/unit/test_repl.py` | 新建 | 状态机、元命令、表格、历史、SQL 分派、CLI 的单元测试 |
| `tests/integration/test_repl_process.py` | 新建 | console script 子进程的 CRUD、元命令、多行、错误恢复、持久化 |
| `README.md` | 修改 | REPL 启动、flag、元命令、历史与平台差异 |
| `examples/repl_smoke.sh` | 新建 | 可重复的管道式真实 CLI smoke |

**禁止修改：** `src/tinydb/__init__.py`、`database.py`、`executor.py`、`parser.py`、`pager.py`、`slotted_page.py`、`catalog.py`、`type_system.py`、`row_codec.py`、`tokenizer.py`、`errors.py`。

## 2. 里程碑与执行模式

| 里程碑 | tasks.md | 提交单元 | 模式 |
|---|---|---:|---|
| 骨架与最小 SQL 循环 | §1–§2 | 7 | Direct：实现 → 增量验证 |
| 多行状态机 | §3 | 2 | Direct：实现 → 增量验证 |
| 元命令 | §4 | 6 | Direct：实现 → 增量验证 |
| 历史 | §5 | 3 | Direct：实现 → 增量验证 |
| 输出 | §6 | 3 | Direct：实现 → 增量验证 |
| 正式测试套件 | §7 | 2 | **TDD：RED → GREEN → IMPROVE** |
| CLI 与文档 | §8–§9 | 4 | Direct：实现 → 增量验证 |
| 行数审计与自检 | §10 | 4 项只读验收 | 不制造空提交 |

`tasks.md` §1–§9 恰好形成 27 个可提交子任务；§10 的 4 项为最终只读/运行验收，不创建空提交。增量 pytest 命令统一使用 `-o addopts=''`，避免只跑局部文件时被项目级 coverage 配置误判；最终 §10.2 必须恢复 `pyproject.toml` 的完整 coverage 配置。

---

# 里程碑 A：骨架（tasks.md §1–§2）

### Task 1：1.1 创建 REPL 模块和入口桩

**模式：** Direct（实现 → 测试增量验证）  
**输入文件：** `src/tinydb/database.py`（只读 API）、Design Doc §1  
**输出文件：** 新建 `src/tinydb/repl.py`  
**测试文件：** 无新增；使用导入级 smoke  
**验收信号：** 模块可导入、`main()` 返回 `0`、文件行数 `≤ 350`

- [x] **Step 1：创建最小模块**

```python
"""Interactive SQL shell for tinydb; stdlib-only and isolated from the MVP core."""


def main() -> int:
    """Run the tinydb REPL."""
    return 0
```

- [x] **Step 2：执行增量验证**

Run:
```bash
.venv/bin/python -c "from tinydb.repl import main; assert main() == 0"
wc -l src/tinydb/repl.py
```
Expected: Python 命令退出码 `0`；`wc` 输出不超过 `350`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): add shell module entry point"
```

### Task 2：1.2 注册 console script

**模式：** Direct  
**输入文件：** `pyproject.toml`、`src/tinydb/repl.py`  
**输出文件：** 修改 `pyproject.toml`  
**测试文件：** 无新增；使用构建元数据查询  
**验收信号：** 项目脚本名精确为 `tinydb-repl`，目标精确为 `tinydb.repl:main`，`dependencies = []` 未变

- [x] **Step 1：在 `[project]` 后添加脚本表**

```toml
[project.scripts]
tinydb-repl = "tinydb.repl:main"
```

- [x] **Step 2：验证元数据**

Run:
```bash
.venv/bin/python -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); assert d['project']['scripts']['tinydb-repl']=='tinydb.repl:main'; assert d['project']['dependencies']==[]"
```
Expected: 退出码 `0`，无输出。

- [x] **Step 3：验收、勾选并提交**

```bash
git add pyproject.toml openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): register tinydb-repl console script"
```

### Task 3：1.3 验证 editable 安装与入口发现

**模式：** Direct，只读验收；通过时不制造空提交  
**输入文件：** `pyproject.toml`、`src/tinydb/repl.py`  
**输出文件：** 无  
**测试文件：** 无新增；真实安装/console smoke  
**验收信号：** editable 安装成功，`.venv/bin/tinydb-repl --help` 可执行且退出 `0`

- [x] **Step 1：安装当前工作树**

Run:
```bash
.venv/bin/python -m pip install -e ".[dev]"
```
Expected: `Successfully installed tinydb-0.1.0` 或已安装 editable 包的成功信息。

- [x] **Step 2：验证入口**

Run:
```bash
test -x .venv/bin/tinydb-repl
.venv/bin/tinydb-repl --help
```
Expected: 两条命令退出码均为 `0`；此阶段允许入口桩不打印帮助文本，Task 25（8.1）将补齐精确 usage。

- [x] **Step 3：定向勾选 1.3**

仅修改并提交 `openspec/changes/repl-shell/tasks.md` 的 1.3 状态，不创建空提交：

```bash
git add openspec/changes/repl-shell/tasks.md
git commit -m "chore(repl): verify editable console entry"
```

### Task 4：2.1 实现数据库提示符

**模式：** Direct  
**输入文件：** `src/tinydb/repl.py`、Design Doc §8.1  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22（7.1）将固化单测；当前用内联断言  
**验收信号：** 内存库和文件库提示符均携带已解析路径，不读取 `Database` 私有状态

- [x] **Step 1：添加提示符常量与函数**

```python
PRIMARY_PROMPT_PREFIX = "tinydb"
CONTINUATION_PROMPT = "...> "


def _make_prompt(db_path: str) -> str:
    return f"{PRIMARY_PROMPT_PREFIX}> [{db_path}] "
```

- [x] **Step 2：验证两种路径**

Run:
```bash
.venv/bin/python -c "from tinydb.repl import _make_prompt; assert _make_prompt(':memory:') == 'tinydb> [:memory:] '; assert _make_prompt('/tmp/a.db') == 'tinydb> [/tmp/a.db] '"
wc -l src/tinydb/repl.py
```
Expected: 退出码 `0`；行数 `≤ 350`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): format database-aware prompt"
```

### Task 5：2.2 实现单行读取包装

**模式：** Direct  
**输入文件：** `src/tinydb/repl.py`、Design Doc §2  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22（7.1）将用 monkeypatch 固化  
**验收信号：** 正常输入原样返回；`EOFError` 被转换为 `None`；其他异常不吞掉

- [x] **Step 1：添加读取函数**

```python
def _read_one_statement(prompt: str) -> str | None:
    try:
        return input(prompt)
    except EOFError:
        return None
```

- [x] **Step 2：验证 EOF 路径**

Run:
```bash
.venv/bin/python - <<'PY'
import builtins
from tinydb.repl import _read_one_statement
old = builtins.input
try:
    builtins.input = lambda prompt: (_ for _ in ()).throw(EOFError())
    assert _read_one_statement("tinydb> ") is None
finally:
    builtins.input = old
PY
```
Expected: 退出码 `0`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): normalize EOF from line input"
```

### Task 6：2.3 实现 SQL 执行和 AST peek 分派

**模式：** Direct  
**输入文件：** `src/tinydb/database.py`、`parser.py`、`tokenizer.py`（仅使用 Design Doc 已确认接口）  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22（7.1）将覆盖 DDL、DML、空 SELECT、非空 SELECT、错误  
**验收信号：** DDL/DML 输出 `OK`；空 SELECT 输出 `(no rows)`；非空 SELECT 暂逐行输出 `Row(...)`；错误为单行且无 traceback

- [x] **Step 1：添加导入和 `_run_sql`**

```python
import sys

from tinydb.database import Database
from tinydb.parser import Select, parse
from tinydb.tokenizer import tokenize


def _run_sql(db: Database, sql: str) -> None:
    try:
        statements = parse(tokenize(sql)).statements
        last_is_select = bool(statements) and isinstance(statements[-1], Select)
    except Exception:
        last_is_select = False

    try:
        rows = db.execute(sql)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return

    if not last_is_select:
        print("OK")
    elif not rows:
        print("(no rows)")
    else:
        for row in rows:
            print(repr(row))
```

- [x] **Step 2：验证三类结果和错误**

Run:
```bash
.venv/bin/python - <<'PY'
from tinydb.database import Database
from tinydb.repl import _run_sql
with Database(":memory:") as db:
    _run_sql(db, "CREATE TABLE t(id INT);")
    _run_sql(db, "SELECT * FROM t;")
    _run_sql(db, "SELECT FROM;")
PY
```
Expected: stdout 依次包含 `OK`、`(no rows)`；stderr 只有一行 `ERROR: ParseError: ...`，无 traceback。

- [x] **Step 3：行数和核心文件保护检查**

Run:
```bash
wc -l src/tinydb/repl.py
git diff --name-only HEAD -- src/tinydb
```
Expected: `repl.py ≤ 350`；源码变更列表只有 `src/tinydb/repl.py`。

- [x] **Step 4：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): execute SQL with AST-aware output"
```

### Task 7：2.4 串接最小交互主循环

**模式：** Direct  
**输入文件：** `src/tinydb/repl.py`、Design Doc §2  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 23（7.2）将做真实进程覆盖  
**验收信号：** prompt → read → execute 循环工作；EOF 返回 `0`；Ctrl-C 清空当前输入并继续；数据库始终关闭

- [x] **Step 1：用最小循环替换入口桩**

```python
def main() -> int:
    db_path = ":memory:"
    db = Database(db_path)
    try:
        while True:
            try:
                line = _read_one_statement(_make_prompt(db_path))
            except KeyboardInterrupt:
                print("\n(Use .exit or Ctrl-D to exit)")
                continue
            if line is None:
                return 0
            if not line.strip():
                continue
            _run_sql(db, line)
    finally:
        db.close()
```

- [x] **Step 2：管道验证 EOF 正常退出**

Run:
```bash
printf 'CREATE TABLE t(id INT);\n' | .venv/bin/tinydb-repl
```
Expected: 输出包含数据库提示符与 `OK`，进程退出码 `0`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): connect interactive SQL loop"
```

---

# 里程碑 B：多行（tasks.md §3）

### Task 8：3.1 实现 SQL-aware 未终止状态机

**模式：** Direct  
**输入文件：** Design Doc §4 的字符状态机和测试矩阵  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22（7.1）参数化覆盖  
**验收信号：** 正确识别单/双引号、`''`/`""`、行注释、块注释和括号；注释或字符串中的括号不误计数

- [x] **Step 1：添加字符级状态机**

```python
def _is_unterminated(buf: str) -> bool:
    in_sq = False
    in_dq = False
    in_lc = False
    in_bc = False
    parens = 0
    i = 0
    while i < len(buf):
        char = buf[i]
        nxt = buf[i + 1] if i + 1 < len(buf) else ""
        if in_lc:
            in_lc = char != "\n"
            i += 1
            continue
        if in_bc:
            if char == "*" and nxt == "/":
                in_bc = False
                i += 2
            else:
                i += 1
            continue
        if in_sq:
            if char == "'" and nxt == "'":
                i += 2
            elif char == "'":
                in_sq = False
                i += 1
            else:
                i += 1
            continue
        if in_dq:
            if char == '"' and nxt == '"':
                i += 2
            elif char == '"':
                in_dq = False
                i += 1
            else:
                i += 1
            continue
        if char == "-" and nxt == "-":
            in_lc = True
            i += 2
        elif char == "/" and nxt == "*":
            in_bc = True
            i += 2
        elif char == "'":
            in_sq = True
            i += 1
        elif char == '"':
            in_dq = True
            i += 1
        elif char == "(":
            parens += 1
            i += 1
        elif char == ")":
            parens -= 1
            i += 1
        else:
            i += 1
    return in_sq or in_dq or in_lc or in_bc or parens > 0
```

- [x] **Step 2：运行核心矩阵**

Run:
```bash
.venv/bin/python - <<'PY'
from tinydb.repl import _is_unterminated
cases = {
    "SELECT 1;": False,
    "INSERT INTO t(id) VALUES (": True,
    "INSERT INTO t(id) VALUES (1)": False,
    "INSERT INTO t(name) VALUES ('o''b');": False,
    "INSERT INTO t(name) VALUES ('o''": True,
    "SELECT 1 -- ( ignored\n": False,
    "SELECT 1 /* unterm comment": True,
}
for sql, expected in cases.items():
    assert _is_unterminated(sql) is expected, sql
PY
wc -l src/tinydb/repl.py
```
Expected: 退出码 `0`；行数 `≤ 350`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): detect unterminated SQL buffers"
```

### Task 9：3.2 将主循环改为 buffer 累积

**模式：** Direct  
**输入文件：** `src/tinydb/repl.py`、Design Doc §2 状态转移  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 23（7.2）覆盖跨行 INSERT  
**验收信号：** 未闭合时显示 `...> ` 且不执行；闭合后只执行一次并清空 buffer；空 buffer 的空行被忽略

- [x] **Step 1：用缓冲版本替换 `main` 循环体**

```python
def main() -> int:
    db_path = ":memory:"
    db = Database(db_path)
    buf = ""
    try:
        while True:
            try:
                prompt = CONTINUATION_PROMPT if buf else _make_prompt(db_path)
                line = _read_one_statement(prompt)
            except KeyboardInterrupt:
                print("\n(Use .exit or Ctrl-D to exit)")
                buf = ""
                continue
            if line is None:
                return 0
            if not line.strip() and not buf:
                continue
            buf += line + "\n"
            if _is_unterminated(buf):
                continue
            _run_sql(db, buf)
            buf = ""
    finally:
        db.close()
```

- [x] **Step 2：验证跨行 INSERT**

Run:
```bash
printf "CREATE TABLE t(id INT, name TEXT);\nINSERT INTO t(id, name) VALUES (\n1, 'alice');\nSELECT * FROM t;\n" | .venv/bin/tinydb-repl
```
Expected: 输出包含 `...> `，CREATE 与 INSERT 各有 `OK`，SELECT 包含 `Row(id=1, name='alice')`，退出码 `0`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): accumulate multiline SQL input"
```

---

# 里程碑 C：元命令（tasks.md §4）

### Task 10：4.1 建立元命令 dispatcher

**模式：** Direct  
**输入文件：** Design Doc §3 dispatcher 表、当前 buffer 主循环  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22（7.1）覆盖返回值和未知命令  
**验收信号：** 仅空 buffer 下以 `.` 开头的行进入 dispatcher；未知命令不进入 SQL parser

- [x] **Step 1：添加 dispatcher 骨架**

```python
def _handle_meta(line: str, db: Database) -> bool:
    stripped = line.lstrip()
    if not stripped.startswith("."):
        return False
    command = stripped.split(maxsplit=1)[0]
    print(f"ERROR: unknown command: {command}", file=sys.stderr)
    return True
```

- [x] **Step 2：在 `_run_sql` 之前接入，仅允许空 buffer 分发**

```python
            if not buf and line.lstrip().startswith("."):
                _handle_meta(line, db)
                continue
```

- [x] **Step 3：验证未知命令不会出现 ParseError**

Run:
```bash
printf '.foo\n' | .venv/bin/tinydb-repl 2>&1
```
Expected: 恰有 `ERROR: unknown command: .foo`，不包含 `ParseError` 或 traceback。

- [x] **Step 4：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): dispatch dot-prefixed meta commands"
```

### Task 11：4.2 实现 `.exit` / `.quit`

**模式：** Direct  
**输入文件：** `src/tinydb/repl.py`、delta spec 退出 scenarios  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22/23 覆盖异常和进程退出码  
**验收信号：** 两个命令均通过内部异常退出，返回码 `0`，数据库 `finally` 关闭

- [x] **Step 1：添加内部退出异常和分支**

```python
class _ExitRepl(Exception):
    """Internal control flow for .exit and .quit."""


# 放在 _handle_meta 的 unknown 分支之前
    if command in {".exit", ".quit"}:
        raise _ExitRepl
```

- [x] **Step 2：在主循环捕获控制流异常**

```python
            if not buf and line.lstrip().startswith("."):
                try:
                    _handle_meta(line, db)
                except _ExitRepl:
                    return 0
                continue
```

- [x] **Step 3：验证两个退出命令**

Run:
```bash
printf '.exit\n' | .venv/bin/tinydb-repl
printf '.quit\n' | .venv/bin/tinydb-repl
```
Expected: 两次退出码均为 `0`，无 ERROR/traceback。

- [x] **Step 4：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): exit cleanly from meta commands"
```

### Task 12：4.3 实现 `.help`

**模式：** Direct  
**输入文件：** Design Doc §3、delta spec `.help` scenario  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22/23  
**验收信号：** stdout 同时包含六个命令名和 Ctrl-D/Ctrl-C 提示

- [x] **Step 1：添加固定帮助文本和分支**

```python
HELP_TEXT = """Meta commands:
  .exit               exit the REPL
  .quit               exit the REPL
  .help               show this help
  .tables             list tables
  .schema <name>      show CREATE TABLE
  .read <path>        execute a SQL file
Shortcuts: Ctrl-D exits; Ctrl-C clears the current buffer."""


# 放在 _handle_meta 的 unknown 分支之前
    if command == ".help":
        print(HELP_TEXT)
        return True
```

- [x] **Step 2：验证清单**

Run:
```bash
printf '.help\n.exit\n' | .venv/bin/tinydb-repl
```
Expected: 输出包含 `.exit`、`.quit`、`.help`、`.tables`、`.schema`、`.read`、`Ctrl-D`、`Ctrl-C`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): document built-in meta commands"
```

### Task 13：4.4 实现 `.tables`

**模式：** Direct  
**输入文件：** Design Doc §3 已确认的 `db.catalog.tables` 接口  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22/23  
**验收信号：** 表名按稳定排序一行一个；空库无额外输出

- [x] **Step 1：在 dispatcher unknown 分支前添加**

```python
    if command == ".tables":
        for name in sorted(db.catalog.tables):
            print(name)
        return True
```

- [x] **Step 2：验证两个表**

Run:
```bash
printf 'CREATE TABLE users(id INT);\nCREATE TABLE orders(id INT);\n.tables\n.exit\n' | .venv/bin/tinydb-repl
```
Expected: 输出分别包含 `orders` 和 `users` 的独立行，不包含 ERROR。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): list catalog tables"
```

### Task 14：4.5 实现 `.schema <name>`

**模式：** Direct  
**输入文件：** Design Doc §3 已确认的 `TableInfo.schema` 形状  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22（7.1）覆盖已知/未知/缺参；缺参 UX 在 TDD GREEN 中收紧  
**验收信号：** 已知表精确输出 `CREATE TABLE users(id INT, name TEXT);`；未知表输出指定 ERROR

- [x] **Step 1：让 dispatcher 提取余下参数**

```python
    parts = stripped.split(maxsplit=1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) == 2 else ""
```

- [x] **Step 2：在 unknown 分支前添加 schema 分支**

```python
    if command == ".schema":
        table = db.catalog.get_table(argument)
        if table is None:
            print(f"ERROR: no such table: {argument}", file=sys.stderr)
            return True
        columns = ", ".join(f"{name} {type_name}" for name, type_name in table.schema)
        print(f"CREATE TABLE {argument}({columns});")
        return True
```

- [x] **Step 3：验证回向 DDL 和未知表**

Run:
```bash
printf 'CREATE TABLE users(id INT, name TEXT);\n.schema users\n.schema ghost\n.exit\n' | .venv/bin/tinydb-repl 2>&1
```
Expected: 包含精确行 `CREATE TABLE users(id INT, name TEXT);` 与 `ERROR: no such table: ghost`。

- [x] **Step 4：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): render table schemas as DDL"
```

### Task 15：4.6 实现 `.read <path>`

**模式：** Direct  
**输入文件：** Design Doc §7、`_is_unterminated`、`_run_sql`  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22 覆盖缺失文件；Task 23 以 RED 用例收紧同一行多语句逐条反馈  
**验收信号：** UTF-8 文件可执行；跨行 SQL 复用状态机；缺失文件为指定 ERROR；EOF 未闭合为单行 ERROR

- [x] **Step 1：添加 Path 导入和文件执行函数**

```python
from pathlib import Path


def _run_file(db: Database, path_str: str) -> None:
    try:
        text = Path(path_str).read_text(encoding="utf-8")
    except OSError:
        print(f"ERROR: cannot read file: {path_str}", file=sys.stderr)
        return

    buf = ""
    for raw_line in text.splitlines(keepends=True):
        buf += raw_line
        if not _is_unterminated(buf):
            _run_sql(db, buf)
            buf = ""
    if buf.strip():
        print(
            f"ERROR: unterminated statement at EOF in {path_str}",
            file=sys.stderr,
        )
```

- [x] **Step 2：在 dispatcher unknown 分支前添加 `.read`**

```python
    if command == ".read":
        _run_file(db, argument)
        return True
```

- [x] **Step 3：验证成功和缺失文件**

Run:
```bash
tmp_dir=$(mktemp -d)
printf 'CREATE TABLE t(id INT);\nINSERT INTO t(id) VALUES (1);\n' > "$tmp_dir/seed.sql"
printf '.read %s\nSELECT * FROM t;\n.exit\n' "$tmp_dir/seed.sql" | .venv/bin/tinydb-repl
printf '.read %s\n.exit\n' "$tmp_dir/nope.sql" | .venv/bin/tinydb-repl 2>&1
rm -rf "$tmp_dir"
```
Expected: 第一段包含两个 `OK` 和 `Row(id=1)`；第二段包含 `ERROR: cannot read file:`，无 traceback。

- [x] **Step 4：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): execute SQL files from read command"
```

---

# 里程碑 D：历史（tasks.md §5）

### Task 16：5.1 加载 Unix 历史

**模式：** Direct  
**输入文件：** Design Doc §8.2、stdlib `readline` 契约  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22 使用 fake readline 和临时 HOME  
**验收信号：** 可导入时读取展开后的 `~/.tinydb_history`、长度设为 1000；缺失文件静默；不可导入返回 `False`

- [x] **Step 1：添加历史常量与 setup**

```python
import os

HISTORY_PATH = "~/.tinydb_history"
HISTORY_LENGTH = 1000


def _setup_history() -> bool:
    try:
        import readline
    except ImportError:
        return False
    history_file = os.path.expanduser(HISTORY_PATH)
    try:
        readline.read_history_file(history_file)
    except OSError:
        pass
    readline.set_history_length(HISTORY_LENGTH)
    return True
```

- [x] **Step 2：验证真实平台路径不因文件缺失失败**

Run:
```bash
HOME=$(mktemp -d) .venv/bin/python -c "from tinydb.repl import _setup_history; assert _setup_history() in (True, False)"
wc -l src/tinydb/repl.py
```
Expected: 退出码 `0`；行数 `≤ 350`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): load persistent readline history"
```

### Task 17：5.2 保存 Unix 历史

**模式：** Direct  
**输入文件：** `_setup_history` 返回契约、Design Doc §8.2  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22 覆盖成功、`OSError` 和 false 路径  
**验收信号：** 仅 `readline_ok=True` 时写文件；`ImportError`/`OSError` 静默

- [x] **Step 1：添加 `_save_history`**

```python
def _save_history(readline_ok: bool) -> None:
    if not readline_ok:
        return
    try:
        import readline

        readline.write_history_file(os.path.expanduser(HISTORY_PATH))
    except (ImportError, OSError):
        pass
```

- [x] **Step 2：验证禁用路径不触碰磁盘**

Run:
```bash
.venv/bin/python -c "from tinydb.repl import _save_history; assert _save_history(False) is None"
```
Expected: 退出码 `0`，无输出。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): save history without surfacing IO errors"
```

### Task 18：5.3 串接 fallback、命令追加和统一出口

**模式：** Direct  
**输入文件：** 当前 `main`、`_setup_history`、`_save_history`  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22 覆盖 ImportError；Task 23 覆盖正常执行  
**验收信号：** `readline` 缺失时仍走 `input()`；所有退出路径保存历史；SQL buffer 被添加到历史；Ctrl-C 清空 buffer 不退出

- [x] **Step 1：抽取完整交互循环**

```python
def _interactive_loop(db: Database, db_path: str) -> int:
    readline_ok = _setup_history()
    buf = ""
    try:
        while True:
            try:
                prompt = CONTINUATION_PROMPT if buf else _make_prompt(db_path)
                line = _read_one_statement(prompt)
            except KeyboardInterrupt:
                print("\n(Use .exit or Ctrl-D to exit)")
                buf = ""
                continue
            if line is None:
                return 0
            if not line.strip() and not buf:
                continue
            if not buf and line.lstrip().startswith("."):
                try:
                    _handle_meta(line, db)
                except _ExitRepl:
                    return 0
                continue
            buf += line + "\n"
            if _is_unterminated(buf):
                continue
            if readline_ok:
                try:
                    import readline

                    readline.add_history(buf.rstrip("\n"))
                except (ImportError, AttributeError):
                    pass
            _run_sql(db, buf)
            buf = ""
    finally:
        _save_history(readline_ok)
```

- [x] **Step 2：让 `main` 委托循环并保持关闭语义**

```python
def main() -> int:
    db_path = ":memory:"
    db = Database(db_path)
    try:
        return _interactive_loop(db, db_path)
    finally:
        db.close()
```

- [x] **Step 3：验证 fallback 不阻断 SQL**

Run:
```bash
printf 'CREATE TABLE t(id INT);\n.exit\n' | .venv/bin/tinydb-repl
```
Expected: 输出包含 `OK`，退出码 `0`；`wc -l src/tinydb/repl.py` 不超过 `350`。

- [x] **Step 4：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): persist history across interactive sessions"
```

---

# 里程碑 E：输出（tasks.md §6）

### Task 19：6.1 实现对齐表格格式化

**模式：** Direct  
**输入文件：** Design Doc §5、已确认的 immutable `Row(values, columns)`  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22 覆盖宽度、空结果、截断  
**验收信号：** 列宽算法精确为 `min(max(header, values), 30)`；值超过 30 字符时为前 29 字符加 `…`；header/separator/data 均存在

- [x] **Step 1：导入 Row 并添加格式化函数**

```python
from tinydb.database import Database, Row

MAX_COLUMN_WIDTH = 30


def _format_table(rows: list[Row]) -> str:
    if not rows:
        return "(no rows)"
    columns = list(rows[0].columns)
    raw_values = [[str(value) for value in row.values] for row in rows]
    widths = [
        min(
            max(len(column), *(len(values[index]) for values in raw_values)),
            MAX_COLUMN_WIDTH,
        )
        for index, column in enumerate(columns)
    ]

    def truncate(value: str) -> str:
        if len(value) <= MAX_COLUMN_WIDTH:
            return value
        return value[: MAX_COLUMN_WIDTH - 1] + "…"

    def render(values: list[str]) -> str:
        cells = [truncate(value).ljust(width) for value, width in zip(values, widths)]
        return " | ".join(cells).rstrip()

    header = render(columns)
    separator = " | ".join("---" for _ in columns)
    body = [render(values) for values in raw_values]
    return "\n".join([header, separator, *body])
```

- [x] **Step 2：验证两列和截断**

Run:
```bash
.venv/bin/python - <<'PY'
from tinydb.database import Row
from tinydb.repl import _format_table
rows = [Row(values=(1, "alice"), columns=("id", "name"))]
out = _format_table(rows)
assert "id | name" in out
assert "--- | ---" in out
assert "1  | alice" in out
long = _format_table([Row(values=("x" * 31,), columns=("v",))])
assert "x" * 29 + "…" in long
assert _format_table([]) == "(no rows)"
PY
wc -l src/tinydb/repl.py
```
Expected: 退出码 `0`；行数 `≤ 350`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): format rows as aligned tables"
```

### Task 20：6.2 将 SELECT 输出切换为表格

**模式：** Direct  
**输入文件：** `_run_sql`、`_format_table`、Design Doc §6 AST peek  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22/23  
**验收信号：** 非空 SELECT 只打印一个表格块；空 SELECT 仍精确打印 `(no rows)`；DDL/DML 仍为 `OK`

- [x] **Step 1：替换 `_run_sql` 的结果分支**

```python
    if not last_is_select:
        print("OK")
    elif not rows:
        print("(no rows)")
    else:
        print(_format_table(rows))
```

- [x] **Step 2：验证结果类型分派**

Run:
```bash
printf "CREATE TABLE t(id INT, name TEXT);\nINSERT INTO t(id, name) VALUES (1, 'alice');\nSELECT * FROM t;\n.exit\n" | .venv/bin/tinydb-repl
```
Expected: 两个 `OK`；表格包含 `id | name`、`--- | ---` 和数据行；不再打印 `Row(`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): render SELECT results as tables"
```

### Task 21：6.3 统一错误为严格单行

**模式：** Direct  
**输入文件：** `_run_sql`、Design Doc §6、delta spec error requirement  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 22/23 覆盖 ParseError、ExecutionError、错误后继续  
**验收信号：** 任意异常 message 中的换行被折叠为空格；输出精确以 `ERROR: <Class>:` 开头；stderr 无 traceback

- [x] **Step 1：收紧异常分支**

```python
    try:
        rows = db.execute(sql)
    except Exception as exc:
        message = " ".join(str(exc).splitlines())
        print(f"ERROR: {type(exc).__name__}: {message}", file=sys.stderr)
        return
```

- [x] **Step 2：验证错误后继续**

Run:
```bash
printf 'SELECT FROM;\nCREATE TABLE ok(id INT);\n.exit\n' | .venv/bin/tinydb-repl > /tmp/repl-out 2> /tmp/repl-err
test "$(wc -l < /tmp/repl-err)" -eq 1
grep -q '^ERROR: ParseError:' /tmp/repl-err
grep -q 'OK' /tmp/repl-out
rm -f /tmp/repl-out /tmp/repl-err
```
Expected: 所有断言通过；进程退出码 `0`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): keep execution errors on one line"
```

---

# 里程碑 F：TDD 正式测试（tasks.md §7）

### Task 22：7.1 单元测试套件（RED → GREEN → IMPROVE）

**模式：** **TDD，强制 RED → GREEN → IMPROVE**  
**输入文件：** `src/tinydb/repl.py`、Design Doc §10.1、delta spec 26 scenarios  
**输出文件：** 新建 `tests/unit/test_repl.py`；GREEN 阶段允许最小修改 `src/tinydb/repl.py`  
**测试文件：** `tests/unit/test_repl.py`  
**验收信号：** RED 明确暴露 `.schema`/`.read` 缺参格式；GREEN 全部通过；IMPROVE 后测试仍绿且 `repl.py ≤ 350`

- [x] **Step 1（RED）：写单元测试文件**

```python
"""Unit coverage for REPL state, formatting, meta commands, history, and SQL output."""
import builtins
import sys
from types import SimpleNamespace

import pytest

from tinydb.database import Database, Row
from tinydb.repl import (
    HISTORY_LENGTH,
    _ExitRepl,
    _format_table,
    _handle_meta,
    _is_unterminated,
    _make_prompt,
    _read_one_statement,
    _run_sql,
    _save_history,
    _setup_history,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT 1;", False),
        ("INSERT INTO t(id) VALUES (", True),
        ("INSERT INTO t(id) VALUES (1)", False),
        ("INSERT INTO t(name) VALUES ('alice", True),
        ("INSERT INTO t(name) VALUES ('o''brien');", False),
        ("SELECT 1 -- ( ignored\n", False),
        ("SELECT 1 /* unterminated", True),
        ("-- leading comment\nSELECT 1;", False),
    ],
)
def test_is_unterminated_sql_aware(sql, expected):
    assert _is_unterminated(sql) is expected


@pytest.mark.unit
def test_format_table_header_separator_and_rows():
    rows = [
        Row(values=(1, "alice"), columns=("id", "name")),
        Row(values=(2, "bob"), columns=("id", "name")),
    ]
    output = _format_table(rows)
    assert "id | name" in output
    assert "--- | ---" in output
    assert "1  | alice" in output
    assert "2  | bob" in output


@pytest.mark.unit
def test_format_table_truncates_at_thirty_characters():
    output = _format_table([Row(values=("x" * 31,), columns=("value",))])
    assert "x" * 29 + "…" in output
    assert "x" * 30 not in output


@pytest.mark.unit
def test_format_table_empty_rows():
    assert _format_table([]) == "(no rows)"


@pytest.mark.unit
def test_make_prompt_contains_database_path():
    assert _make_prompt(":memory:") == "tinydb> [:memory:] "
    assert _make_prompt("data.db") == "tinydb> [data.db] "


@pytest.mark.unit
def test_read_one_statement_returns_input(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda prompt: "SELECT 1;")
    assert _read_one_statement("tinydb> ") == "SELECT 1;"


@pytest.mark.unit
def test_read_one_statement_maps_eof_to_none(monkeypatch):
    def raise_eof(prompt):
        raise EOFError

    monkeypatch.setattr(builtins, "input", raise_eof)
    assert _read_one_statement("tinydb> ") is None


@pytest.mark.unit
@pytest.mark.parametrize("command", [".exit", ".quit"])
def test_exit_meta_commands_raise_control_flow(command):
    with Database(":memory:") as db, pytest.raises(_ExitRepl):
        _handle_meta(command, db)


@pytest.mark.unit
def test_help_lists_every_meta_command(capsys):
    with Database(":memory:") as db:
        assert _handle_meta(".help", db) is True
    output = capsys.readouterr().out
    for command in (".exit", ".quit", ".help", ".tables", ".schema", ".read"):
        assert command in output


@pytest.mark.unit
def test_tables_are_sorted(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT)")
        db.execute("CREATE TABLE orders(id INT)")
        _handle_meta(".tables", db)
    assert capsys.readouterr().out.splitlines() == ["orders", "users"]


@pytest.mark.unit
def test_schema_renders_create_table(capsys):
    with Database(":memory:") as db:
        db.execute("CREATE TABLE users(id INT, name TEXT)")
        _handle_meta(".schema users", db)
    assert capsys.readouterr().out == "CREATE TABLE users(id INT, name TEXT);\n"


@pytest.mark.unit
def test_schema_unknown_table(capsys):
    with Database(":memory:") as db:
        _handle_meta(".schema ghost", db)
    assert capsys.readouterr().err == "ERROR: no such table: ghost\n"


@pytest.mark.unit
def test_schema_missing_argument(capsys):
    with Database(":memory:") as db:
        _handle_meta(".schema", db)
    assert capsys.readouterr().err == "ERROR: missing argument for .schema\n"


@pytest.mark.unit
def test_read_missing_argument(capsys):
    with Database(":memory:") as db:
        _handle_meta(".read", db)
    assert capsys.readouterr().err == "ERROR: missing argument for .read\n"


@pytest.mark.unit
def test_read_missing_file(capsys, tmp_path):
    missing = tmp_path / "missing.sql"
    with Database(":memory:") as db:
        _handle_meta(f".read {missing}", db)
    assert capsys.readouterr().err == f"ERROR: cannot read file: {missing}\n"


@pytest.mark.unit
def test_unknown_meta_command(capsys):
    with Database(":memory:") as db:
        _handle_meta(".foo", db)
    assert capsys.readouterr().err == "ERROR: unknown command: .foo\n"


@pytest.mark.unit
def test_run_sql_distinguishes_ok_empty_and_rows(capsys):
    with Database(":memory:") as db:
        _run_sql(db, "CREATE TABLE t(id INT)")
        assert capsys.readouterr().out == "OK\n"
        _run_sql(db, "SELECT * FROM t")
        assert capsys.readouterr().out == "(no rows)\n"
        db.execute("INSERT INTO t(id) VALUES (1)")
        _run_sql(db, "SELECT * FROM t")
        assert "id" in capsys.readouterr().out


@pytest.mark.unit
def test_run_sql_prints_single_line_error(capsys):
    with Database(":memory:") as db:
        _run_sql(db, "SELECT FROM")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERROR: ParseError:")
    assert len(captured.err.splitlines()) == 1


@pytest.mark.unit
def test_setup_history_expands_home(monkeypatch, tmp_path):
    calls = []
    fake_readline = SimpleNamespace(
        read_history_file=lambda path: calls.append(("read", path)),
        set_history_length=lambda length: calls.append(("length", length)),
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(sys.modules, "readline", fake_readline)
    assert _setup_history() is True
    assert calls == [
        ("read", str(tmp_path / ".tinydb_history")),
        ("length", HISTORY_LENGTH),
    ]


@pytest.mark.unit
def test_setup_history_ignores_missing_file(monkeypatch):
    def missing(path):
        raise OSError("missing")

    fake_readline = SimpleNamespace(
        read_history_file=missing,
        set_history_length=lambda length: None,
    )
    monkeypatch.setitem(sys.modules, "readline", fake_readline)
    assert _setup_history() is True


@pytest.mark.unit
def test_setup_history_falls_back_without_readline(monkeypatch):
    real_import = builtins.__import__

    def import_without_readline(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "readline":
            raise ImportError("readline unavailable")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_readline)
    assert _setup_history() is False


@pytest.mark.unit
def test_save_history_uses_expanded_home(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setitem(
        sys.modules,
        "readline",
        SimpleNamespace(write_history_file=lambda path: calls.append(path)),
    )
    assert _save_history(True) is None
    assert calls == [str(tmp_path / ".tinydb_history")]


@pytest.mark.unit
def test_save_history_ignores_write_failure(monkeypatch):
    def fail(path):
        raise OSError("disk full")

    monkeypatch.setitem(
        sys.modules,
        "readline",
        SimpleNamespace(write_history_file=fail),
    )
    assert _save_history(True) is None
    assert _save_history(False) is None
```

- [x] **Step 2（RED）：运行并确认真实行为缺口**

Run:
```bash
.venv/bin/python -m pytest -o addopts='' tests/unit/test_repl.py -q
```
Expected: 至少 `test_schema_missing_argument` 与 `test_read_missing_argument` FAIL；其余用例通过。禁止通过弱化断言制造 GREEN。

- [x] **Step 3（GREEN）：仅补齐缺参分派**

在 `_handle_meta` 的 `.schema` 和 `.read` 实际调用之前加入：

```python
    if command in {".schema", ".read"} and not argument:
        print(f"ERROR: missing argument for {command}", file=sys.stderr)
        return True
```

- [x] **Step 4（GREEN）：重跑单元测试**

Run:
```bash
.venv/bin/python -m pytest -o addopts='' tests/unit/test_repl.py -q
```
Expected: 全部 PASS。

- [x] **Step 5（IMPROVE）：格式化、去重并复验**

保持参数提取只出现一次、错误分支只出现一次；不得为测试添加生产代码特例。然后运行：

```bash
.venv/bin/python -m pytest -o addopts='' tests/unit/test_repl.py -q
wc -l src/tinydb/repl.py
```
Expected: 全部 PASS；`repl.py ≤ 350`。

- [x] **Step 6：验收、勾选并提交**

```bash
git add tests/unit/test_repl.py src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "test(repl): cover shell units and missing arguments"
```

### Task 23：7.2 子进程集成套件（RED → GREEN → IMPROVE）

**模式：** **TDD，强制 RED → GREEN → IMPROVE**  
**输入文件：** 已安装的 `tinydb-repl`、Design Doc §10.2、delta spec scenarios  
**输出文件：** 新建 `tests/integration/test_repl_process.py`；GREEN 最小修改 `_run_file`  
**测试文件：** `tests/integration/test_repl_process.py`  
**验收信号：** RED 暴露同一物理行多个语句只反馈一次；GREEN 后所有真实进程场景通过；每个 `communicate` 有 10 秒 timeout

- [x] **Step 1（RED）：写真实 console-script 测试**

```python
"""Process-level tests for the installed tinydb-repl console script."""
import shutil
import subprocess

import pytest


REPL = shutil.which("tinydb-repl")


def run_repl(commands: str, *args: str) -> subprocess.CompletedProcess[str]:
    assert REPL is not None, "run pip install -e '.[dev]' before integration tests"
    process = subprocess.Popen(
        [REPL, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = process.communicate(input=commands, timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(
            f"tinydb-repl timed out\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


@pytest.mark.integration
def test_repl_basic_crud():
    result = run_repl(
        "CREATE TABLE t(id INT);\n"
        "INSERT INTO t(id) VALUES (1);\n"
        "SELECT * FROM t;\n"
        ".exit\n"
    )
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "id" in result.stdout and "1" in result.stdout
    assert result.stderr == ""


@pytest.mark.integration
def test_repl_select_no_rows():
    result = run_repl("CREATE TABLE t(id INT);\nSELECT * FROM t;\n.exit\n")
    assert result.returncode == 0
    assert "(no rows)" in result.stdout


@pytest.mark.integration
def test_repl_tables_meta():
    result = run_repl(
        "CREATE TABLE users(id INT);\n"
        "CREATE TABLE orders(id INT);\n"
        ".tables\n.exit\n"
    )
    assert result.returncode == 0
    assert "users" in result.stdout and "orders" in result.stdout


@pytest.mark.integration
def test_repl_schema_meta():
    result = run_repl("CREATE TABLE users(id INT, name TEXT);\n.schema users\n.exit\n")
    assert result.returncode == 0
    assert "CREATE TABLE users(id INT, name TEXT);" in result.stdout


@pytest.mark.integration
def test_repl_read_executes_each_same_line_statement(tmp_path):
    script = tmp_path / "seed.sql"
    script.write_text(
        "CREATE TABLE t(id INT); INSERT INTO t(id) VALUES (1);",
        encoding="utf-8",
    )
    result = run_repl(f".read {script}\nSELECT * FROM t;\n.exit\n")
    assert result.returncode == 0
    assert result.stdout.count("OK") == 2
    assert "1" in result.stdout


@pytest.mark.integration
@pytest.mark.parametrize("command", [".exit", ".quit"])
def test_repl_meta_exit_returns_zero(command):
    result = run_repl(command + "\n")
    assert result.returncode == 0
    assert "Traceback" not in result.stderr


@pytest.mark.integration
def test_repl_eof_returns_zero():
    result = run_repl("")
    assert result.returncode == 0


@pytest.mark.integration
def test_repl_multiline_insert():
    result = run_repl(
        "CREATE TABLE t(id INT, name TEXT);\n"
        "INSERT INTO t(id, name) VALUES (\n"
        "1, 'alice');\n"
        "SELECT * FROM t;\n.exit\n"
    )
    assert result.returncode == 0
    assert "...> " in result.stdout
    assert "alice" in result.stdout


@pytest.mark.integration
def test_repl_error_is_single_line_and_loop_continues():
    result = run_repl("SELECT FROM;\nCREATE TABLE ok(id INT);\n.exit\n")
    assert result.returncode == 0
    error_lines = [line for line in result.stderr.splitlines() if line]
    assert len(error_lines) == 1
    assert error_lines[0].startswith("ERROR: ParseError:")
    assert "OK" in result.stdout
```

- [x] **Step 2（RED）：运行并确认逐条反馈缺口**

Run:
```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -o addopts='' tests/integration/test_repl_process.py -q
```
Expected: `test_repl_read_executes_each_same_line_statement` FAIL，实际仅一个 `OK`；其余场景通过。

- [x] **Step 3（GREEN）：把 `_run_file` 改为 SQL-aware 分号边界执行**

保留原文件读取和 OSError 分支，只替换 buffer 循环：

```python
    buf = ""
    for char in text:
        buf += char
        if char == ";" and not _is_unterminated(buf):
            _run_sql(db, buf)
            buf = ""
    if buf.strip():
        print(
            f"ERROR: unterminated statement at EOF in {path_str}",
            file=sys.stderr,
        )
```

该实现不使用朴素 `text.split(';')`，因此字符串、注释或括号中的分号仍由 `_is_unterminated` 保护。

- [x] **Step 4（GREEN）：重跑进程测试**

Run:
```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -o addopts='' tests/integration/test_repl_process.py -q
```
Expected: 全部 PASS，无 timeout。

- [x] **Step 5（IMPROVE）：联合回归并审计资源释放**

Run:
```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -o addopts='' tests/unit/test_repl.py tests/integration/test_repl_process.py -q
wc -l src/tinydb/repl.py
```
Expected: 全部 PASS；无残留子进程；`repl.py ≤ 350`。

- [x] **Step 6：验收、勾选并提交**

```bash
git add tests/integration/test_repl_process.py src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "test(repl): cover console process workflows"
```

---

# 里程碑 G：CLI / 文档（tasks.md §8–§9）

### Task 24：8.1 实现 `--database` CLI flag

**模式：** Direct（实现 → 测试增量验证；本任务不得改成 RED-first）  
**输入文件：** Design Doc §8.1/Q7、delta spec CLI requirement  
**输出文件：** 修改 `src/tinydb/repl.py`  
**测试文件：** Task 25（8.2）在实现后补 characterization；当前用真实 CLI 增量验证  
**验收信号：** 默认 `:memory:`；仅接受 `--database <path>`、`--help/-h`；路径经 `expanduser`；未知/缺参返回 `2`

- [x] **Step 1：用带 argv 的入口替换 `main`**

```python
USAGE = "Usage: tinydb-repl [--database PATH]"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["--help"], ["-h"]):
        print(USAGE)
        return 0
    if not args:
        db_path = ":memory:"
    elif len(args) == 2 and args[0] == "--database":
        db_path = os.path.expanduser(args[1])
    else:
        flag = args[0] if args else "--database"
        print(f"ERROR: invalid argument: {flag}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    db = Database(db_path)
    try:
        return _interactive_loop(db, db_path)
    finally:
        db.close()
```

- [x] **Step 2：验证帮助、未知参数和文件创建**

Run:
```bash
.venv/bin/tinydb-repl --help
if .venv/bin/tinydb-repl data.db; then exit 1; else test "$?" -eq 2; fi
tmp_dir=$(mktemp -d)
printf '.exit\n' | .venv/bin/tinydb-repl --database "$tmp_dir/data.db"
test -f "$tmp_dir/data.db"
rm -rf "$tmp_dir"
```
Expected: help 输出精确 usage 并返回 `0`；位置参数被拒绝并返回 `2`；flag 路径被创建。

- [x] **Step 3：行数审计**

Run: `wc -l src/tinydb/repl.py`  
Expected: `≤ 350`。

- [x] **Step 4：验收、勾选并提交**

```bash
git add src/tinydb/repl.py openspec/changes/repl-shell/tasks.md
git commit -m "feat(repl): parse database CLI flag"
```

### Task 25：8.2 增量补齐 CLI 测试

**模式：** Direct（先有实现，再加测试并验证；不是 RED→GREEN）  
**输入文件：** `src/tinydb/repl.py` 的已实现 CLI  
**输出文件：** 修改 `tests/unit/test_repl.py`、`tests/integration/test_repl_process.py`  
**测试文件：** 同输出文件  
**验收信号：** 默认内存不建文件；`~` 展开后创建文件；help/非法参数退出码正确；两次进程启动可读取持久化数据

- [x] **Step 1：在单元测试追加 CLI characterization**

```python
@pytest.mark.unit
def test_main_help_returns_zero(capsys):
    from tinydb.repl import main

    assert main(["--help"]) == 0
    assert capsys.readouterr().out == "Usage: tinydb-repl [--database PATH]\n"


@pytest.mark.unit
def test_main_unknown_argument_returns_two(capsys):
    from tinydb.repl import main

    assert main(["data.db"]) == 2
    assert "ERROR: invalid argument: data.db" in capsys.readouterr().err


@pytest.mark.unit
def test_main_default_memory_creates_no_file(monkeypatch, tmp_path):
    import tinydb.repl as repl

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(repl, "_interactive_loop", lambda db, path: 0)
    assert repl.main([]) == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit
def test_main_database_expands_home_and_creates_file(monkeypatch, tmp_path):
    import tinydb.repl as repl

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(repl, "_interactive_loop", lambda db, path: 0)
    assert repl.main(["--database", "~/persist.db"]) == 0
    assert (tmp_path / "persist.db").exists()
```

- [x] **Step 2：在进程测试追加持久化场景**

```python
@pytest.mark.integration
def test_repl_database_flag_persists(tmp_path):
    database = tmp_path / "persist.db"
    first = run_repl(
        "CREATE TABLE t(id INT);\nINSERT INTO t(id) VALUES (7);\n.exit\n",
        "--database",
        str(database),
    )
    second = run_repl(
        "SELECT * FROM t;\n.exit\n",
        "--database",
        str(database),
    )
    assert first.returncode == 0
    assert second.returncode == 0
    assert database.exists()
    assert "7" in second.stdout
```

- [x] **Step 3：运行增量验证**

Run:
```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest -o addopts='' tests/unit/test_repl.py tests/integration/test_repl_process.py -q
```
Expected: 全部 PASS。

- [x] **Step 4：验收、勾选并提交**

```bash
git add tests/unit/test_repl.py tests/integration/test_repl_process.py openspec/changes/repl-shell/tasks.md
git commit -m "test(repl): verify database CLI behavior"
```

### Task 26：9.1 更新 README REPL 章节

**模式：** Direct  
**输入文件：** `README.md`、Design Doc §8/Q7、最终 CLI 和元命令  
**输出文件：** 修改 `README.md`  
**测试文件：** 无新增；使用文本契约检查  
**验收信号：** 启动示例只使用无参数或 `--database`；六个元命令齐全；历史路径和 Windows fallback 明示；无位置参数示例

- [x] **Step 1：在 Quick start 后添加章节**

````markdown
## REPL

安装开发环境后可启动内存数据库 shell：

```bash
pip install -e ".[dev]"
tinydb-repl
```

使用 `--database` 打开或创建文件数据库：

```bash
tinydb-repl --database data.db
```

REPL 支持多行 SQL；未闭合的单引号或括号会显示 `...>` 续行提示符。SELECT 以对齐表格显示，DDL/DML 输出 `OK`，错误以单行 `ERROR: <Class>: <message>` 显示且不会终止会话。

| 元命令 | 作用 |
|---|---|
| `.exit` / `.quit` | 正常退出 |
| `.help` | 显示帮助 |
| `.tables` | 一行一个列出表名 |
| `.schema <name>` | 显示回向生成的 CREATE TABLE |
| `.read <path>` | 执行 UTF-8 SQL 文件 |

在可导入 `readline` 的 Unix-like 平台，历史保存在 `~/.tinydb_history`。缺失历史文件或写入失败不会阻止启动/退出。没有 `readline` 的平台（例如默认 Windows 环境）自动回退到内置 `input()`，SQL、元命令和输出仍可用，但不加载或保存历史。
````

- [x] **Step 2：验证文档契约**

Run:
```bash
grep -q '^## REPL$' README.md
grep -q 'tinydb-repl --database data.db' README.md
grep -q '~/.tinydb_history' README.md
grep -q 'Windows' README.md
! grep -q '^tinydb-repl data.db$' README.md
```
Expected: 全部退出码 `0`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add README.md openspec/changes/repl-shell/tasks.md
git commit -m "docs(repl): document shell usage and history"
```

### Task 27：9.2 添加真实 CLI smoke 脚本

**模式：** Direct  
**输入文件：** 最终 CLI、README 示例  
**输出文件：** 新建 `examples/repl_smoke.sh`  
**测试文件：** `examples/repl_smoke.sh` 本身为 smoke 验收  
**验收信号：** 脚本使用临时目录、真实 `tinydb-repl --database`、四个输入动作、关键输出 grep、可靠 cleanup；退出 `0` 并打印 `smoke: OK`

- [x] **Step 1：创建脚本**

```bash
#!/usr/bin/env bash
set -euo pipefail

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
DB="$TMP_DIR/repl.db"
OUT="$TMP_DIR/repl.out"

printf '%s\n' \
  'CREATE TABLE t(id INT);' \
  'INSERT INTO t(id) VALUES (1);' \
  'SELECT * FROM t;' \
  '.exit' \
  | tinydb-repl --database "$DB" >"$OUT" 2>&1

test -f "$DB"
test "$(grep -c 'OK' "$OUT")" -eq 2
grep -Eq 'id[[:space:]]*' "$OUT"
grep -Eq '(^|[[:space:]])1([[:space:]]|$)' "$OUT"

echo "smoke: OK"
```

- [x] **Step 2：设置可执行位并运行**

Run:
```bash
chmod +x examples/repl_smoke.sh
PATH="$PWD/.venv/bin:$PATH" examples/repl_smoke.sh
```
Expected: 唯一最终状态行包含 `smoke: OK`，退出码 `0`。

- [x] **Step 3：验收、勾选并提交**

```bash
git add examples/repl_smoke.sh openspec/changes/repl-shell/tasks.md
git commit -m "test(repl): add console smoke script"
```

---

# 里程碑 H：行数审计与最终自检（tasks.md §10）

### Check 10.1：行数预算红线

**模式：** 只读审计  
**输入文件：** `src/tinydb/repl.py`  
**输出文件：** 无；若失败，回到 build 修复，不勾选 10.1  
**测试文件：** 无  
**验收信号：** 数值 `≤ 350`；禁止把注释/逻辑机械挪入 MVP 核心模块规避预算

- [x] Run:

```bash
lines=$(wc -l < src/tinydb/repl.py)
printf 'src/tinydb/repl.py: %s lines\n' "$lines"
test "$lines" -le 350
```
Expected: 退出码 `0`。

### Check 10.2：完整测试与覆盖率

**模式：** 只读审计  
**输入文件：** 全部源码和测试  
**输出文件：** coverage 数据文件（不提交）  
**测试文件：** `tests/unit/test_repl.py`、`tests/integration/test_repl_process.py` 及现有全套测试  
**验收信号：** 全套测试通过；合并 coverage `≥ 85%`；REPL 模块 coverage `≥ 90%`；MVP 现有测试无回归

- [x] Run:

```bash
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest --cov=tinydb --cov-report=term-missing --cov-fail-under=85
.venv/bin/python -m coverage report --include='*/tinydb/repl.py' --fail-under=90
```
Expected: pytest 全绿；第一条总覆盖率不低于 `85%`，第二条 `repl.py` 不低于 `90%`。若第二条不足，补高价值分支测试，不添加 `# pragma: no cover` 逃逸。

### Check 10.3：OpenSpec 严格校验

**模式：** 只读审计  
**输入文件：** `openspec/changes/repl-shell/` 全部产物  
**输出文件：** 无  
**测试文件：** delta spec 8 requirements × 26 scenarios  
**验收信号：** strict validation PASS；仍为“无 Spec Patch”

- [x] Run:

```bash
openspec validate repl-shell --strict
```
Expected: `repl-shell` validation PASS，无 warning/error。

### Check 10.4：管道 smoke 与手工交互一致性

**模式：** 真实 CLI 验收  
**输入文件：** 临时 `smoke.sql`、最终 console script  
**输出文件：** 临时数据库/输出（清理，不提交）  
**测试文件：** `examples/repl_smoke.sh`  
**验收信号：** 管道和 smoke 脚本均退出 `0`；CREATE/INSERT 各一个 `OK`；SELECT 表格含 `id` 和 `1`；文件数据库存在

- [x] Run:

```bash
tmp_dir=$(mktemp -d)
printf '%s\n' \
  'CREATE TABLE t(id INT);' \
  'INSERT INTO t(id) VALUES (1);' \
  'SELECT * FROM t;' \
  '.exit' > "$tmp_dir/smoke.sql"
PATH="$PWD/.venv/bin:$PATH" tinydb-repl --database "$tmp_dir/data.db" \
  < "$tmp_dir/smoke.sql" > "$tmp_dir/out" 2> "$tmp_dir/err"
test ! -s "$tmp_dir/err"
test "$(grep -c 'OK' "$tmp_dir/out")" -eq 2
grep -q 'id' "$tmp_dir/out"
grep -q '1' "$tmp_dir/out"
test -f "$tmp_dir/data.db"
PATH="$PWD/.venv/bin:$PATH" examples/repl_smoke.sh
rm -rf "$tmp_dir"
```
Expected: 全部退出码 `0`；脚本打印 `smoke: OK`。

---

## 3. Spec 覆盖追踪

| Requirement | 主要实施任务 | 正式测试 |
|---|---|---|
| Interactive SQL loop / EOF / `.exit` / `.quit` | 2.3、2.4、4.2、6.2 | 7.1、7.2 |
| Multi-line continuation | 3.1、3.2 | 7.1 参数矩阵、7.2 multiline |
| Meta-commands | 4.1–4.6 | 7.1 dispatcher、7.2 tables/schema/read/exit |
| Unix history | 5.1、5.2、5.3 | 7.1 fake readline |
| Windows readline fallback | 5.1、5.3 | 7.1 ImportError/禁用路径，完整回归 |
| Aligned SELECT table | 6.1、6.2 | 7.1 width/truncate/empty、7.2 CRUD |
| Single-line errors and continuation | 6.3 | 7.1 error、7.2 error-then-continue |
| `--database` flag | 8.1、8.2 | 8.2 unit + process persistence |

## 4. 完成定义

- `src/tinydb/repl.py ≤ 350`，且 `git diff a14dec13620f81639857f9bb9dfbecd93c86c42f...HEAD -- src/tinydb` 只包含 `repl.py`。
- `pyproject.toml` 仍为零运行时依赖，console script 可发现。
- 27 个实现/测试/文档子任务逐一验收并提交；§10 四项全部打勾且不制造空提交。
- 合并覆盖率 `≥ 85%`，REPL 模块 `≥ 90%`，所有现有测试无回归。
- `openspec validate repl-shell --strict` PASS。
- 不修改 delta spec：**无 Spec Patch**。
