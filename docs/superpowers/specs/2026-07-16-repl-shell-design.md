---
comet_change: repl-shell
role: technical-design
canonical_spec: openspec
archived-with: 2026-07-16-repl-shell
status: final
---

# repl-shell 深度技术设计

> **Change:** `repl-shell`
> **日期:** 2026-07-16
> **阶段:** design（基于 open 阶段 proposal / design.md / tasks.md / specs/repl-shell/spec.md）
> **上游产物:** `openspec/changes/repl-shell/proposal.md`, `design.md`, `tasks.md`, `specs/repl-shell/spec.md`
> **职责:** 本 Doc 是对 open 阶段 `design.md` 的深度技术细化（状态机、字符级启发式、列宽算法、AST peek、CLI/历史/测试策略）。它**不重写** open 阶段的 8 个高层决策（D1–D8），而是为每一项给出可落地的实现级细节。

## 0. 上下文回顾（来自 open 阶段）

- **目标：** 在 `tinydb-comet` 增加 `tinydb-repl` console script，提供交互式 SQL shell；零运行时依赖；不修改 MVP 核心模块。
- **约束：** 单文件 `src/tinydb/repl.py ≤ 350 行`；仅 stdlib；不修改 `database.py / executor.py / parser.py / pager.py` 等。
- **non-goals：** 语法高亮 / 自动补全 / `.mode` / `.import` / `.dump` / `.export` / 远程协议。
- **交付契约：** spec.md 8 requirements × 26 scenarios。

## 1. 模块形态与函数清单

```
src/tinydb/repl.py
├── 文档与 import           # 6-10 行
├── 常量                     # 6 行
├── _ExitRepl 异常类         # 3 行
├── _setup_history()         # 8 行（Unix readline 接入）
├── _save_history()          # 6 行（OSError 静默）
├── _is_unterminated(buf)    # 25 行（SQL-aware 字符状态机）
├── _format_table(rows)      # 35 行（列宽算法 + 截断）
├── _make_prompt(db_path)    # 5 行
├── _handle_meta(line, db)   # 35 行（dispatcher 表实现）
├── _run_sql(db, sql)        # 25 行（AST peek + execute + 结果分派）
├── _run_file(db, path)      # 15 行（复用 _is_unterminated 循环）
└── main(argv=None)          # 30 行（CLI + 主循环 + readline 出口）
```

预算总计 ~ 195 行核心 + 50 行常量 / import / 注释 = **245 行**，预算 ≤ 350，留 100+ 行容差。

## 2. 主循环状态机（D1 落地）

精确状态转移（既适用于交互输入也复用给 `.read` 文件读入）：

```text
状态机初始:
    buf = ""

loop:
    if interactive → print_prompt() else → print(... >)
    line = read_one_line()                  # readline.input() / builtin input()
    if line is None (EOF):                   # Ctrl-D
        _save_history()
        return 0
    line = line.rstrip("\n")
    if line.strip() == "" and buf == "":     # 空行：完全忽略
        continue
    stripped = line.lstrip()
    if stripped.startswith(".") and buf == "":
        try:
            _handle_meta(line, db)
        except _ExitRepl:
            _save_history()
            return 0
        continue
    buf += line + "\n"
    if _is_unterminated(buf):
        if not interactive: continue          # .read 中无 prompt
        continue                              # 主循环 → 进入 ...> prompt
    _run_sql(db, buf)                        # buf 此时闭合
    buf = ""
    continue
```

**关键不变量：**
- 元命令仅在 `buf == ""` 时处理；任何非空缓冲都被视为 SQL 累积（即使首字符是 `.`）。
- `Line.strip() == ""` 仅当 `buf` 也为空时忽略，否则视作 SQL 中的换行（`\n` 在 SQL 中是 token 分隔符）。
- EOF 在任何状态下都执行 `_save_history` 后退出 — readline 缓冲未 `add_history` 的输入不持久化。

## 3. 元命令 dispatcher（D3 落地）

| 输入（lstrip 后）              | 行为                                                                 | 失败输出                              |
|-------------------------------|----------------------------------------------------------------------|--------------------------------------|
| `.exit` / `.quit`             | `raise _ExitRepl`                                                   | —                                    |
| `.help`                       | 内置字符串：6 个名字 + 快捷键说明                                    | —                                    |
| `.tables`                     | `for name in sorted(db.catalog.tables): print(name)`（sorted 仅是稳定测试） | —（空库 → 无输出）                |
| `.schema <name>`              | `CREATE TABLE <name>(col1 TYPE1, col2 TYPE2);` 格式                  | `ERROR: no such table: <name>`       |
| `.read <path>`                | `Path(path).read_text()` → 循环调用本状态机的 `_is_unterminated + _run_sql` | `ERROR: cannot read file: <path>`    |
| `.foo`（其他 `.xyz`）         | 输出未知命令错误                                                     | `ERROR: unknown command: .foo`       |

**`.schema` 输出格式（精确）：**
```
CREATE TABLE <name>(<col1> <TYPE1>, <col2> <TYPE2>);
```
- 列顺序：catalog JSON 中顺序（= 创建时顺序，通过 `db.catalog.get_table(name).schema` 读取）。
- trim 无前置空格，无尾随逗号。TYPE 来自 spec 支持的 `INT` / `TEXT` / `FLOAT` / `BOOL`。
- 末尾 `;` 后跟换行。

**参数提取：** 朴素 `_split_arg(line, n=1)` — 在第一个空白处切，余下视为 1 个参数。`.schema` / `.read` 在缺参数时返回 `ERROR: missing argument for .<cmd>`（spec 未要求；属于 UX 补偿，可降级）。

## 4. `_is_unterminated(buf)` — SQL-aware 字符状态机（D2 + Q1 落地）

```python
def _is_unterminated(buf: str) -> bool:
    in_sq = False         # 'string'
    in_dq = False         # "identifier"  -- MVP 不支持双引号标识符 but无害处理
    in_lc = False         # -- line comment
    in_bc = False         # /* block comment */
    parens = 0
    i, n = 0, len(buf)
    while i < n:
        c = buf[i]
        if in_lc:
            if c == "\n": in_lc = False
            i += 1; continue
        if in_bc:
            if c == "*" and i+1 < n and buf[i+1] == "/":
                in_bc = False; i += 2; continue
            i += 1; continue
        if in_sq:
            if c == "'" and i+1 < n and buf[i+1] == "'":
                i += 2; continue            # '' doubled-quote literal
            if c == "'":
                in_sq = False; i += 1; continue
            i += 1; continue
        if in_dq:
            if c == '"' and i+1 < n and buf[i+1] == '"':
                i += 2; continue
            if c == '"':
                in_dq = False; i += 1; continue
            i += 1; continue
        # token context:
        if c == "-" and i+1 < n and buf[i+1] == "-":
            in_lc = True; i += 2; continue
        if c == "/" and i+1 < n and buf[i+1] == "*":
            in_bc = True; i += 2; continue
        if c == "'":
            in_sq = True; i += 1; continue
        if c == '"':
            in_dq = True; i += 1; continue
        if c == "(":
            parens += 1; i += 1; continue
        if c == ")":
            parens -= 1; i += 1; continue
        i += 1
    return in_sq or parens > 0
```

**测试矩阵（核心）：**
| 输入                                    | 期望 |
|----------------------------------------|------|
| `SELECT 1;`                            | False |
| `INSERT INTO t(name) VALUES (`         | True  |
| `INSERT INTO t(name) VALUES (1, 'a')`   | False |
| `INSERT INTO t(name) VALUES ('o''b');`  | False |
| `INSERT INTO t(name) VALUES ('o''`     | True  |
| `SELECT 1 -- comment\n WHERE 1=1;`     | False |
| `SELECT 1 /* unterm comment`           | True  |
| `-- leading comment\nSELECT 1;`        | False |

实现 ~ 30 行；测试覆盖 12 个 fixture 行。

## 5. `_format_table(rows)` — 列宽算法（D6 + Q2 落地）

```python
def _format_table(rows: list[Row]) -> str:
    if not rows:                            # 防御性：调用方负责，但兜底
        return "(no rows)"
    cols = list(rows[0].columns)            # Row.columns: tuple[str, ...]
    raw_widths = []
    for k in cols:
        w = len(k)
        for r in rows:
            v = getattr(r, k)
            w = max(w, len(str(v)))
        raw_widths.append(w)
    widths = [min(w, 30) for w in raw_widths]
    def fmt_cell(v) -> str:
        s = str(v)
        return (s[:29] + "…") if len(s) > 30 else s
    def fmt_row(vals: list[str]) -> str:
        cells = []
        for w, v in zip(widths, vals):
            cells.append((" " + v + " "*(w - len(v)) + " ") if w > len(v) else " " + v + " ")
        return "|".join(cells)
    header = fmt_row(cols)
    sep    = fmt_row(["-"*min(w,3) for w in widths])   # '---' 视觉分隔
    body   = [fmt_row([fmt_cell(getattr(r, k)) for k in cols]) for r in rows]
    return "\n".join([header, sep, *body])
```

**算法注释：**
- **列宽计算**：`min(max(len(header), max(value-width)), 30)`（D6 + Q2）。
- **截断**：仅在 `len(s) > 30` 时截断为 29 字符 + `…`；29 字符保留标识可读性。
- **空行防御**：调用方 `_run_sql` 已经在结果为 `[]` 时调用 `(no rows)`；此处兜底。
- **对齐**：左对齐；MVP 数据多为 TEXT 时更友好。
- **行内换行（`\n`）**：单元格内 `\n` 会被保留，但下游终端会按字面渲染（不破坏对齐）。MVP 数据 TEXT 列不内嵌换行（row_codec 写入单行），属已知行为。

## 6. `_run_sql(db, sql)` — AST peek + 结果分派（D7 落地 + Q5 落地）

**关键洞察：** MVP `Database.execute(sql)` 对 **空 SELECT** 与 **DDL/DML** 都返回 `[]`（见 §11 探查）。spec 要求二者分别输出 `(no rows)` 和 `OK`。**必须 AST peek。**

```python
def _run_sql(db, sql: str) -> None:
    """Run a complete SQL buffer through Database.execute, dispatching output
    based on whether the last AST statement is a SELECT.
    """
    from tinydb.parser import parse, Select       # noqa: 局部导入避免 import 周期
    from tinydb.tokenizer import tokenize
    from tinydb.errors import TinydbError
    try:
        stmts = parse(tokenize(sql))
        last_is_select = bool(stmts.statements) and isinstance(stmts.statements[-1], Select)
    except TinydbError:
        last_is_select = False                     # 错误时让 execute 抛出实际异常
    try:
        result = db.execute(sql)
    except TinydbError as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return
    except Exception as e:                         # 防御性：捕获非 tinydb 异常
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return
    if last_is_select:
        if not result:
            print("(no rows)")
        else:
            print(_format_table(result))
    else:
        print("OK")
```

**设计决策：**
- AST peek 在错误情况下 `last_is_select = False`，让 execute 在第二次重新抛出（避免不一致）。
- catch 顺序：`TinydbError` 子类优先；其余 `Exception` 兜底防 traceback 泄漏。
- 输出到 stderr（错误属诊断信息），结果/OK 输出到 stdout（让 `tinydb-repl db.sql > out.txt` 可工作）。

## 7. `.read` 文件处理（Q4 落地）

```python
def _run_file(db, path_str: str) -> None:
    p = Path(path_str)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        print(f"ERROR: cannot read file: {path_str}", file=sys.stderr)
        return
    buf = ""
    for raw_line in text.splitlines(keepends=True):
        buf += raw_line
        if not _is_unterminated(buf):                  # 已闭合 → 执行并清空
            _run_sql(db, buf)
            buf = ""
    if buf.strip():                                     # 文件末尾遗留未闭合 → 报错
        print(f"ERROR: unterminated statement at EOF in {path_str}", file=sys.stderr)
```

- 与主路径共用 `_is_unterminated` + `_run_sql`，避免重复。
- 文件末尾若仍有未闭合语句（缺 `;`），报 ERROR 而不是静默执行 — 对教学友好。

## 8. CLI 与历史（D5 / D7 / Q6 / Q7 落地）

### 8.1 CLI 解析 — 手写，不用 argparse

```python
import sys
def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    db_path = ":memory:"
    if argv:
        if argv[0] == "--database":
            db_path = argv[1] if len(argv) > 1 else ":memory:"
        elif argv[0] in ("--help", "-h"):
            print("Usage: tinydb-repl [--database PATH]")
            return 0
        else:
            print(f"ERROR: unknown flag: {argv[0]}", file=sys.stderr)
            print("Usage: tinydb-repl [--database PATH]", file=sys.stderr)
            return 2
    db = Database(db_path) if db_path != ":memory:" else Database(":memory:")
    try:
        return _interactive_loop(db, db_path)
    finally:
        try:
            db.close()
        except Exception: pass
```

**说明：** 仅按 spec 暴露 `--database <path>` + `--help`；不引入位置参数以避免 spec 范围外的扩展。未知 flag 返回退出码 2 + stderr。

### 8.2 历史持久化

```python
def _setup_history() -> bool:
    """Try to set up readline; return True on success."""
    try:
        import readline
    except ImportError:
        return False
    hist = os.path.expanduser("~/.tinydb_history")
    try:
        readline.read_history_file(hist)
    except OSError:
        pass                            # 文件不存在或权限不足 → 静默
    readline.set_history_length(1000)
    return True

def _save_history(readline_ok: bool) -> None:
    if not readline_ok:
        return
    try:
        import readline
        readline.write_history_file(os.path.expanduser("~/.tinydb_history"))
    except (ImportError, OSError):
        pass                            # 磁盘满、权限不足 → 静默
```

- 历史条目通过 `readline.add_history(line)` 在主循环中加（在 execute 闭合且非元命令后调用）。
- 主循环结构：
```python
def _interactive_loop(db, db_path):
    readline_ok = _setup_history()
    prompt = _make_prompt(db_path)
    buf = ""
    while True:
        try:
            line = input("" if buf else prompt)
        except EOFError:
            _save_history(readline_ok); return 0
        except KeyboardInterrupt:
            print("\n(Use .exit or Ctrl-D to exit)")
            buf = ""; continue
        if line.strip() == "" and buf == "": continue
        stripped = line.lstrip()
        if stripped.startswith(".") and buf == "":
            try:
                _handle_meta(line, db)
            except _ExitRepl:
                _save_history(readline_ok); return 0
            continue
        buf += line + "\n"
        if _is_unterminated(buf):
            print("...>", end="", flush=True)       # 续行无 builtin input() 行为 → 用 print 模拟
            continue
        _run_sql(db, buf)
        if readline_ok and len(line) > 1:
            try: readline.add_history(line)
            except Exception: pass
        buf = ""
```

- KeyboardInterrupt 处理：清空 buf、提示继续，不退出。
- `readline_ok = False` 时（即 Windows fallback），历史 add 跳过；其他流程不变。

## 9. 实现风险矩阵（依 open 阶段 design.md §Risks 加深）

| 风险 | 严重度 | Mitigation |
|------|-------|-----------|
| `_is_unterminated` 与 `''` 转义冲突 | M | 字符状态机中显式 skip `''`；单元测试覆盖 `'o''brien'` 与未闭合 3 引号 |
| CJK / emoji 对齐错位 | L | naive `len()` codepoint 计数，MVP 数据多为 ASCII。README 标注；不引入 wcwidth |
| 空 SELECT vs DML 返回同为 `[]` | H | `_run_sql` AST peek（§6），单测覆盖两类语句的输出分派 |
| spec `Row(id=1)` 与 `Row.__repr__` 实际输出对齐 | L | MVP `Row.__repr__ = "Row(c=v, ...)"` 直接匹配；`print(row)` 命中 spec |
| subprocess 集成测试 race | M | Popen + `communicate(timeout=10)`；失败时降级到 `examples/repl_smoke.sh` |
| 历史文件权限不足 | L | `OSError` 静默；不影响主功能 |
| Windows readline 缺失 | L | `ImportError` 捕获，回退内置 `input()`；README 明示 |
| `--database PATH` 的 PATH 含空格 | L | 内置 `subprocess` 测试用临时路径；不做 shell quote |
| 续行 prompt `...>` 与 readline 编辑冲突 | M | 在续行时**跳过 readline** 直接 `sys.stdin.readline()`；行较短可接受 |

## 10. 测试策略（落地 tasks.md §7）

### 10.1 Unit — `tests/unit/test_repl.py`

```python
# 伪大纲
def test_is_unterminated_balanced_returns_false(): ...
def test_is_unterminated_open_paren_returns_true(): ...
def test_is_unterminated_unmatched_quote_returns_true(): ...
def test_is_unterminated_doubled_quote_in_string_no_false_positive(): ...
def test_is_unterminated_unbalanced_paren_returns_true(): ...
def test_is_unterminated_line_comment_eats_until_newline(): ...
def test_is_unterminated_block_comment_unterminated(): ...
def test_format_table_header_and_separator(): ...
def test_format_table_column_width_fit(): ...
def test_format_table_long_value_truncated_with_ellipsis(): ...
def test_format_table_empty_rows_returns_no_rows(): ...
def test_handle_meta_exit_quit_exits(): ...
def test_handle_meta_help_lists_commands(): ...
def test_handle_meta_tables_lists_table_names(): ...
def test_handle_meta_schema_unknown_table(): ...
def test_handle_meta_schema_known_emits_create_table(): ...
def test_handle_meta_read_missing_file(): ...
def test_handle_meta_read_runs_sql(): ...
def test_handle_meta_unknown_command(): ...
def test_run_sql_ok_for_ddl(): ...
def test_run_sql_ok_for_insert(): ...
def test_run_sql_no_rows_for_select_empty(): ...
def test_run_sql_table_for_select_nonempty(): ...
def test_run_sql_parse_error_single_line_stderr(): ...
def test_run_sql_execution_error_single_line_stderr(): ...
def test_make_prompt_memory_default(): ...
def test_make_prompt_path_db(): ...
def test_main_parses_database_flag(): ...
def test_main_parses_positional_path(): ...
```

总计 ~ 28 个 unit 测试。

### 10.2 Integration — `tests/integration/test_repl_process.py`

```python
def test_repl_basic_crud(tmp_path):           # CREATE / INSERT / SELECT 退出码 0
def test_repl_select_no_rows(tmp_path):       # 输出 "(no rows)"
def test_repl_tables_meta(tmp_path):          # .tables 含已建表
def test_repl_schema_meta(tmp_path):          # .schema 含 CREATE TABLE 串
def test_repl_read_file(tmp_path):            # .read a.sql → OK × 2
def test_repl_exit_returns_zero(tmp_path):    # .exit → exit code 0
def test_repl_eof_returns_zero(tmp_path):     # EOF → exit code 0
def test_repl_multiline_insert(tmp_path):     # 未闭合 ( 后继续 → 执行 OK
def test_repl_error_single_line(tmp_path):    # SELECT FROM → "ERROR: ParseError: ..."
def test_repl_database_flag_persists(tmp_path):  # 两次启动写入同一 db 看到数据
```

总计 ~ 10 个 integration 测试。subprocess 用 `subprocess.Popen(['tinydb-repl', '--database', str(p)]), stdin=PIPE, stdout=PIPE, stderr=PIPE` + `communicate(input=b"...", timeout=10)`。

### 10.3 Smoke — `examples/repl_smoke.sh`

```bash
#!/usr/bin/env bash
set -e
DB=$(mktemp)
TINYDB_REPL=$(which tinydb-repl)
printf 'CREATE TABLE t(id INT);\nINSERT INTO t(id) VALUES (1);\nSELECT * FROM t;\n.exit\n' \
    | "$TINYDB_REPL" --database "$DB" > /tmp/repl_out 2>&1
grep -q "OK" /tmp/repl_out
grep -q "Row(id=1)" /tmp/repl_out
rm -f "$DB"
echo "smoke: OK"
```

CI 与人眼双用。

### 10.4 合并覆盖率门槛

`pytest --cov=tinydb --cov-report=term-missing --cov-fail-under=85` —— MVP 已 93.33%，REPL 引入新代码预计覆盖率仍 ≥ 90%（纯逻辑 + 状态机分支较多）。

## 11. 实现前探查结论（已落定）

| 探查项 | 结果 | 影响 |
|--------|------|------|
| `Row` 是否 dataclass | ✓ `database.py:14 @dataclass(frozen=True)` | `Row.columns` 直接可读（`tuple[str, ...]`） |
| `Row.__repr__` 是否输出 `Row(id=1)` | ✓ `database.py:34` 自定义 `__repr__ = f"Row({', '.join(f'{c}={v!r}'...)"` | `print(row)` 命中 spec |
| `Database(path)` 接受 `:memory:` 与路径 | ✓ `database.py:45-53` 通过 Pager 打开 | CLI 默认 path 正确 |
| `ParseError` 实际 str | ✓ `errors.py:9-10` → `"line 2, col 5: <msg>"` | 单行格式 `ERROR: ParseError: line 2, col 5: <msg>` 命中 spec |
| `ExecutionError` 实际 str | ✓ `errors.py:24` → `table 'ghost' does not exist` | 单行格式命中 spec |
| `db.execute()` 对空 SELECT vs DML 的返回 | **都返回 `[]`** | §6 AST peek 必要 |
| AST 节点导入 `Select` from parser | ✓ `database.py:10` 已有 `from tinydb.parser import parse, Select` | `_run_sql` 可直接使用 |
| `pager.open(...)` 对不存在路径的行为 | 假设创建新文件（MVP 行为，未在本探查中验证） | 测试覆盖 `test_repl_database_flag_persists` 验证副作用 |

## 12. Spec Patch 总结

本阶段探查未发现 spec 描述与 MVP 实际行为的差异：
- 8 requirements / 26 scenarios 均与 MVP `Database` + `Row` API 兼容。
- spec scenario "Basic CRUD round-trip" 中 `Row(id=1)` 字面命中 MVP `Row.__repr__` 输出。
- spec scenario "Empty result message" 要求 `(no rows)` 输出，与 §6 AST peek 路径一致。

**无需 Spec Patch 回写**。

## 13. 开放问题（已收敛 → 见 brainstorm-summary.md）

| Q# | 主题 | 决定 |
|----|------|------|
| Q1 | escape 语义 | SQL-aware skip `''` pairs |
| Q2 | 列宽计算 | naive `len(str(value))` |
| Q3 | 多语句反馈 | buffer + db.execute(buf) |
| Q4 | .read 解析 | 复用主路径 + `_is_unterminated` 循环 |
| Q5 | 空 SELECT vs DML | AST peek in `_run_sql` |
| Q6 | CLI 风格 | 手写（节省行数） |
| Q7 | `--database` vs 位置参数 | **仅 `--database <path>`**，不引入位置参数（spec 之外） |

## 14. 交付检查清单（移交 build 阶段）

- [ ] `src/tinydb/repl.py` ≤ 350 行（§1 预算 ~ 245 行）
- [ ] `pyproject.toml` 增加 `[project.scripts] tinydb-repl = "tinydb.repl:main"`
- [ ] `tests/unit/test_repl.py` 28 个用例覆盖
- [ ] `tests/integration/test_repl_process.py` 10 个 end-to-end 用例
- [ ] `examples/repl_smoke.sh` bash smoke
- [ ] `README.md` `## REPL` 章节
- [ ] 不修改 `database.py / executor.py / parser.py / pager.py / slotted_page.py / catalog.py / type_system.py / row_codec.py / tokenizer.py / errors.py / __init__.py`
- [ ] `wc -l src/tinydb/repl.py ≤ 350` 验证
- [ ] `pytest --cov=tinydb --cov-fail-under=85` 全绿
- [ ] `openspec validate repl-shell --strict` PASS
