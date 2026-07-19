---
comet_change: tinydb-acid
role: technical-design
canonical_spec: openspec
status: final
---

# Design: tinydb-acid

> **关联文档**：[proposal.md](../../../../openspec/changes/tinydb-acid/proposal.md) · [design.md](../../../../openspec/changes/tinydb-acid/design.md) · [tasks.md](../../../../openspec/changes/tinydb-acid/tasks.md)
> **Brainstorm checkpoint**：见本文 §6 六轮澄清与五段设计裁决
> **Date**：2026-07-19
> **承接 change 名**：`tinydb-acid`

本文档落实六轮澄清问答、五段设计裁决与一种实现 approach（page-level WAL），提供实现级技术方案供 build 阶段 implementer 直接对照。

---

## 1. Context

MVP + 五个后续 change（engine-v1、constraints、types、aggregation、engine-v2）共享三个存储层瓶颈中的第三个仍未解决：

1. **崩溃一致性缺失** — `execute()` 立即落盘，`kill -9` 中途的 INSERT / UPDATE 留下半写 page 或 catalog 不一致状态
2. **无原子事务** — UPDATE 的"in-place 改 + delete/insert fallback"中途崩溃 → 数据丢失；多条相关 INSERT 无法"全成或全不成"
3. **无 BEGIN / COMMIT / ROLLBACK 语句** — 教学型嵌入式数据库若无 ACID 语义始终只是 demo；用户无法表达"先试不通过就回滚"

engine-v2 已落地 page-aligned 存储（schema 0x02）、B+tree 索引、DROP 回收。WAL / 事务是其上的纯增量层；不依赖 B+tree / 多页 catalog 内部实现细节。

## 2. Goals / Non-Goals

**Goals：**
- `BEGIN` / `COMMIT` / `ROLLBACK` 三语句完整语义；嵌套 BEGIN 报错；COMMIT/ROLLBACK 无 active txn 时报错
- **隐式 auto-commit**：未 BEGIN 时单 DML/DDL 语句 = 一次性事务；失败自动 rollback，成功自动 commit
- **DDL 在显式事务内允许**（Postgres 风格）：`BEGIN; CREATE TABLE t(...); ROLLBACK;` 后 t 不存在
- **Page-level WAL**：每条 page 修改 append 一条完整 page record 到 `<db>.wal`；commit 时 replay 到 main file + fsync main + truncate WAL
- **启动期 crash recovery**：open 时若 WAL 存在，扫描 record，apply commit / 丢弃未 commit，最后 truncate WAL
- **fsync 策略**：仅在 COMMIT 后 `os.fsync(main_file)`；WAL record append 不单独 fsync（OS page cache 即可）
- **Schema version bump**：main file header 升到 `schema_version=0x03`；旧 v2 db + 新 WAL 残留 → 报 `SchemaMismatch`，要求用户先调 `migrate_v2_to_v3(path)`
- **WAL corruption 处理**：recovery 时遇 CRC 错 → truncate WAL 到该 record 之前 + log warning + 启动成功（保留已 commit 部分）
- **Executor 事务状态机**：单线程、单 Executor、单 `current_txn`

**Non-Goals：**
- MVCC / 多版本并发控制（串行单线程等价 SERIALIZABLE）
- Savepoint / nested transaction
- 隔离级别（READ COMMITTED / SERIALIZABLE 等显式声明）
- 异步 fsync / group commit / write coalescing
- 加密 / 校验和认证（CRC32 仅作 record 完整性）
- 远程 / 分布式事务
- WAL 压缩 / archive（`truncate_before` 是唯一清理路径）
- time-travel query / flashback
- 多 Executor / 多连接 / 多线程并发

## 3. Architecture

### 3.1 高层组件

```
┌────────────────────────────────────────────────────┐
│              REPL / Python API                      │
│   (BEGIN, COMMIT, ROLLBACK, INSERT, ...)           │
└─────────────────┬──────────────────────────────────┘
                  │ tokens
┌─────────────────▼──────────────────────────────────┐
│   Tokenizer + Parser (新增 Begin/Commit/Rollback)    │
└─────────────────┬──────────────────────────────────┘
                  │ AST (Statement)
┌─────────────────▼──────────────────────────────────┐
│          Executor (新增 current_txn 状态)            │
│   • BEGIN → new Transaction, push                   │
│   • DML/DDL → txn.write_page(...)                   │
│   • COMMIT → flush pages + WAL truncate             │
│   • ROLLBACK → discard pending writes                │
└─────────────────┬──────────────────────────────────┘
                  │
┌─────────────────▼──────────────────────────────────┐
│   Transaction (新增) — pending_writes + state       │
└─────────────────┬──────────────────────────────────┘
                  │
┌─────────────────▼──────────────────────────────────┐
│   Pager (改造) — write_through_wal / commit_writes  │
└─────────────────┬──────────────────────────────────┘
                  │
┌─────────────────▼──────────────────────────────────┐
│   WAL (新增) — <db>.wal append-only record stream  │
│   Recovery (新增) — open 时 replay / discard        │
└────────────────────────────────────────────────────┘
```

### 3.2 模块边界（行数预算）

| 模块 | 状态 | 预算行数 | 实际目标 |
|------|------|----------|----------|
| `src/tinydb/wal.py` | 新增 | ≤ 200 | ~180 |
| `src/tinydb/transaction.py` | 新增 | ≤ 300 | ~250 |
| `src/tinydb/recovery.py` | 新增 | ≤ 200 | ~150 |
| `src/tinydb/pager.py` | 改造 | ≤ 520 | ~480 |
| `src/tinydb/executor.py` | 改造 | ≤ 1280 | ~1260 |
| `src/tinydb/parser.py` | 改造 | ≤ 900 | ~880 |
| `src/tinydb/tokenizer.py` | 改造 | ≤ 160 | ~150 |
| `src/tinydb/errors.py` | 改造 | ≤ 100 | ~80 |

### 3.3 关键不变量

1. **主 db file 永远保持 committed 状态**：崩溃恢复后能直接使用
2. **WAL 永远 append-only**：不修改历史 record
3. **COMMIT 必须 fsync main file**：未 fsync 不算 committed
4. **last-committer-wins**：recovery 时多 txn 修改同一 page → 后 commit 的覆盖
5. **幂等 replay**：`write_main_page(pid, data)` 多次调用同一 (pid, data) 结果一致

## 4. WAL 文件格式

### 4.1 文件布局

```
Header (16 bytes, write once at file create):
  bytes 0-7:   "TINYWAL\x00"          # magic
  byte  8:     0x01                   # schema version
  bytes 9-15:  0x00 * 7               # reserved

Record (variable, append-only):
  bytes 0-7:   u64 txn_id             # monotonic per file
  byte  8:     u8 kind                # 0=begin, 1=page_write, 2=commit, 3=rollback, 4=checkpoint
  bytes 9-12:  u32 page_id            # only page_write; else 0
  bytes 13-16: u32 data_len           # 0 for non-page_write
  bytes 17..:  payload (data_len bytes, ≤4096 for page_write)
  last 4 bytes: u32 crc32             # over bytes 0..(17+data_len-1)
```

### 4.2 record kind 编码

| kind 值 | 名称 | payload | 含义 |
|---------|------|---------|------|
| 0 | BEGIN | 空 | 事务开始（仅记录 txn_id） |
| 1 | PAGE_WRITE | 完整 page bytes | 一条 page 修改 |
| 2 | COMMIT | 空 | 事务提交（commit 后的 pending_writes 在 recovery 时 apply 到 main） |
| 3 | ROLLBACK | 空 | 事务回滚（仅标记，recovery 时丢弃其 pending_writes） |
| 4 | CHECKPOINT | 空 | 占位符（本期不写，仅留作未来扩展；recovery 跳过） |

### 4.3 Wal 类 API

```python
class WalCorruption(Exception):
    """Raised when CRC32 mismatch is detected at record boundary."""

class InvalidWalFile(Exception):
    """Raised when header (magic or schema_version) is invalid."""


class Wal:
    HEADER_SIZE = 16
    HEADER_MAGIC = b"TINYWAL\x00"
    HEADER_SCHEMA = 0x01

    def __init__(self, path: str | None):
        """Open existing WAL file or create new one. path=None → in-memory WAL."""
        ...

    def append(self, txn_id: int, kind: int, page_id: int = 0, data: bytes = b"") -> None:
        """Append one record. Auto-compute CRC32."""
        ...

    def iter_records(self) -> Iterator[tuple[int, int, int, bytes]]:
        """Yield (txn_id, kind, page_id, data). Raise WalCorruption at first bad CRC."""
        ...

    def truncate_before(self, txn_id: int) -> None:
        """Remove records with txn_id < arg, preserving record boundary."""
        ...

    def close(self) -> None: ...
```

### 4.4 CRC32 计算

使用 `zlib.crc32(payload)` 计算，big-endian u32 写入 record 末尾。`iter_records` 读到 record 末尾时校验；不匹配抛 `WalCorruption(offset)`，由 recovery 决定截断到 offset 之前。

## 5. Transaction 状态机

### 5.1 状态枚举

```python
class TxnState(Enum):
    ACTIVE = "active"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
```

### 5.2 核心类

```python
class InvalidTxnState(Exception):
    """Raised when write_page / commit / rollback called in non-ACTIVE state."""


class Transaction:
    def __init__(self, txn_id: int, pager: Pager):
        self.id = txn_id
        self.state = TxnState.ACTIVE
        self.pending_writes: dict[int, bytes] = {}   # page_id → new bytes

    def write_page(self, page_id: int, data: bytes) -> None:
        """Buffer a page modification; flush to WAL on each call."""
        if self.state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self.state)
        self.pending_writes[page_id] = data
        self.pager.wal_append_page(self.id, page_id, data)

    def commit(self) -> None:
        if self.state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self.state)
        # 1. Apply pending_writes to main file (no fsync yet)
        for pid, data in self.pending_writes.items():
            self.pager.write_main_page(pid, data)
        # 2. Append commit record to WAL
        self.pager.wal_append_commit(self.id)
        # 3. fsync main file — single atomic durability point
        self.pager.fsync_main()
        # 4. Truncate WAL (records before this txn no longer needed)
        self.pager.wal_truncate_before(self.id)
        self.state = TxnState.COMMITTED

    def rollback(self) -> None:
        if self.state != TxnState.ACTIVE:
            raise InvalidTxnState(self.id, self.state)
        # Just append rollback record; pending_writes never hit main file
        self.pager.wal_append_rollback(self.id)
        self.pager.wal_truncate_before(self.id)
        self.state = TxnState.ROLLED_BACK
```

### 5.3 Auto-commit 语义

Executor 在无 active txn 时收到 DML/DDL 自动创建短事务：

```python
def _exec_in_txn(self, stmt):
    auto = self._current_txn is None
    txn = self._current_txn or Transaction(self._next_txn_id, self.pager)
    if auto:
        self._next_txn_id += 1
    try:
        result = self._apply_to_txn(stmt, txn)
    except Exception:
        txn.rollback()
        raise
    if auto:
        txn.commit()
    return result
```

- autocommit 失败 → 自动 rollback → 抛原始错误（db 状态与语句执行前一致）
- 显式事务失败 → txn 进入 ROLLED_BACK 状态 → 用户必须显式 ROLLBACK 或 BEGIN 新事务；后续语句报错 "no active transaction"

## 6. Pager 集成

### 6.1 Schema 版本升级 0x02 → 0x03

```python
class Pager:
    MAGIC = b'TINYDB\x00\x02'       # magic 不变（仍然指向 DB 格式）
    SCHEMA_VERSION = 0x03            # was 0x02
```

- 旧 v2 文件（无 WAL）→ open 时直接运行，把 header byte 8 改写为 0x03（in-place 升级）
- 旧 v2 文件 + 新 WAL 残留 → open 时检测到 schema=0x02 但 `<path>.wal` 存在 → 抛 `SchemaMismatch`，要求用户先调 `migrate_v2_to_v3(path)`

### 6.2 Pager 新增方法

```python
class Pager:
    # 既有接口保留：read_page, write_page, alloc_page, free_page, flush, close
    # write_page 改造为：若 current_txn 存在则走 wal_append_page，不写 main file

    def wal_append_page(self, txn_id: int, page_id: int, data: bytes) -> None: ...
    def wal_append_commit(self, txn_id: int) -> None: ...
    def wal_append_rollback(self, txn_id: int) -> None: ...
    def wal_truncate_before(self, txn_id: int) -> None: ...
    def write_main_page(self, page_id: int, data: bytes) -> None:
        """Write directly to main db file (no WAL). Used by Transaction.commit()."""
    def fsync_main(self) -> None:
        os.fsync(self._main_fd.fileno())
    def _open_wal(self, path: str) -> Wal: ...
```

### 6.3 write_page 路由逻辑

```python
def write_page(self, page_id: int, data: bytes) -> None:
    if self._current_txn is not None:
        # 事务内：仅 WAL append，不写 main
        self.wal_append_page(self._current_txn.id, page_id, data)
        # cache 仍更新（让 SELECT 在事务内看到自己的修改）
        self._page_cache[page_id] = data
    else:
        # 非事务：直接写 main file（保持 MVP 兼容行为）
        self.write_main_page(page_id, data)
```

注意：Executor 在 autocommit 路径下，`_current_txn` 仍指向刚 new 出来的 transaction；commit 后立即清空。SELECT 在事务内需读取 page 时，cache 已更新（见 `write_page` 实现）。

## 7. Crash Recovery

### 7.1 启动时序

`Pager.open(path)` 启动期：

```
1. 读 main file header → magic/schema 验证
2. 检查 <path>.wal 是否存在：
     - 不存在 → 跳过 recovery，正常初始化
     - 存在 → 进入 step 3
3. 扫 WAL records（从 header 后开始）：
     pending[txn_id] = {page_id: data}
     status[txn_id]  = None
   对每条 record：
     - BEGIN:       status[txn_id] = "active"
     - PAGE_WRITE:  pending[txn_id][pid] = data (覆盖)
     - COMMIT:      status[txn_id] = "committed" → 应用 pending 到 main file
     - ROLLBACK:    status[txn_id] = "rolled_back" → 丢弃 pending
     - CHECKPOINT:  跳过（本期不写）
   对 status==None 或 status=="active" 的 txn_id：视作 rolled_back → 丢弃 pending
4. fsync main file
5. truncate WAL（清空，已全部应用）
6. 返回可用 db 状态
```

### 7.2 CRC 错误处理

- recovery 中遇 CRC 错 → log warning → truncate WAL 到该 record 之前 → 启动（已 commit 部分已应用）
- 主 WAL header 损坏（magic/schema 错）→ 抛 `InvalidWalFile`；用户需手动 `rm wal file` 后重试
- 末尾 partial record（CRC 区不完整）→ 视为 corrupt → truncate 到该 record 之前 → 启动成功

### 7.3 幂等性保证

`write_main_page(pid, data)` 多次调用同一 (pid, data) 结果一致 → recovery 多次 replay 同样 OK。Transaction.commit 失败重试（已写 main 但 fsync 失败）下次 open replay commit record 达到同一最终状态。

### 7.4 Recovery API

```python
class Recovery:
    @staticmethod
    def replay(main_path: str, wal: Wal) -> None:
        """Scan WAL, apply committed page_writes to main file, truncate WAL.

        Raises:
            InvalidWalFile: WAL header (magic/schema) is invalid.
            WalCorruption: CRC mismatch at record boundary; partial recovery
                           applies all valid records before corrupt one.
        """
        ...
```

## 8. Executor 改造

### 8.1 AST 节点

在 `parser.py` 新增：

```python
@dataclass
class Begin: pass

@dataclass
class Commit: pass

@dataclass
class Rollback: pass
```

`parse_statement` 新增分支：

```python
def parse_statement(self):
    tok = self.peek()
    if tok.type == "KEYWORD":
        if tok.value == "BEGIN":    self.advance(); return Begin()
        if tok.value == "COMMIT":   self.advance(); return Commit()
        if tok.value == "ROLLBACK": self.advance(); return Rollback()
    return self._parse_dml_or_ddl()
```

### 8.2 Tokenizer

新增 `BEGIN` / `COMMIT` / `ROLLBACK` 三个 keyword 识别（已有 BEGIN，验证并补全）。

### 8.3 Executor 状态机

```python
class Executor:
    def __init__(self, pager, catalog, index_manager=None):
        ...
        self._current_txn: Transaction | None = None
        self._next_txn_id: int = 1

    def execute(self, stmt):
        if isinstance(stmt, Begin):    return self._exec_begin(stmt)
        if isinstance(stmt, Commit):   return self._exec_commit(stmt)
        if isinstance(stmt, Rollback): return self._exec_rollback(stmt)
        return self._exec_in_txn(stmt)

    def _exec_begin(self, stmt):
        if self._current_txn is not None:
            raise ExecutionError("nested BEGIN not allowed")
        self._current_txn = Transaction(self._next_txn_id, self.pager)
        self._next_txn_id += 1
        return []

    def _exec_commit(self, stmt):
        if self._current_txn is None:
            raise ExecutionError("COMMIT without BEGIN")
        self._current_txn.commit()
        self._current_txn = None
        return []

    def _exec_rollback(self, stmt):
        if self._current_txn is None:
            raise ExecutionError("ROLLBACK without BEGIN")
        self._current_txn.rollback()
        self._current_txn = None
        return []
```

### 8.4 DDL/DML 路由改造

所有 `_exec_insert / _exec_update / _exec_delete / _exec_create_table / _exec_drop_table` 改为：

- **page 修改**：调 `txn.write_page(page_id, bytes)` 而非直接 `self.pager.write_page(...)`
- **B+tree 内部 alloc_page**：在 `txn.write_page` 包装下进行；IndexManager 维护的 B+tree split 等多 page 操作全部进同一 txn
- **DML 内的 SELECT**：通过 page cache 看到自己未 commit 的修改（同事务内 read-your-writes）

### 8.5 executor.py 行数增量估算

| 改造点 | 增量 |
|--------|------|
| `__init__` 加 `_current_txn` + `_next_txn_id` | +5 |
| `execute` dispatch 加 3 分支 | +4 |
| `_exec_begin / _exec_commit / _exec_rollback` | +25 |
| `_exec_in_txn` wrapper | +15 |
| 所有 `_exec_*` 把 `self.pager.write_page` 改为 `txn.write_page` | +35（散布在 5 处） |
| 合计 | ~84 |

1196 + 84 = 1280（仍在 ≤1280 预算内）。

## 9. 错误处理矩阵

| 场景 | 行为 |
|------|------|
| `BEGIN; BEGIN;` | ExecutionError "nested BEGIN not allowed" |
| `COMMIT;`（无 active txn） | ExecutionError "COMMIT without BEGIN" |
| `ROLLBACK;`（无 active txn） | ExecutionError "ROLLBACK without BEGIN" |
| autocommit 约束违反（PK duplicate） | ConstraintViolation 抛出 + 自动 rollback |
| 显式 txn 内约束违反 | ConstraintViolation 抛出 + txn 进入 ROLLED_BACK 状态 |
| 显式 txn 内其他错误 | 同上 |
| kill -9 中途 INSERT（autocommit） | recovery 看到 begin 但无 commit → 丢弃；db 一致 |
| kill -9 中途 COMMIT（已 fsync） | recovery replay → 状态一致 |
| kill -9 COMMIT 中 fsync 中 | fsync 失败抛 OSError；recovery 看到 commit record → replay（幂等） |
| WAL CRC 错 | truncate WAL 末尾 corrupt record；启动成功 + log warning |
| 主 WAL header 损坏 | InvalidWalFile 异常 |
| v2 db + 新 WAL 残留 | SchemaMismatch 异常 |
| SchemaMismatch 时用户调 `migrate_v2_to_v3` | 自动 replay WAL → 升级 header → 重试 open |

## 10. 边界情况

**B1**: 同一 txn 内修改同一 page 多次 → WAL 多条 PAGE_WRITE record；commit 时最后一次生效（覆盖）

**B2**: txn 内 ROLLBACK → pending_writes 全部丢弃，不写 main file；下一个语句需先 BEGIN

**B3**: WAL 文件不存在（首次 open）→ Pager 跳过 recovery，正常初始化

**B4**: WAL 文件 header 损坏（magic 错）→ InvalidWalFile 异常；用户需手动 rm wal file

**B5**: txn 内 BEGIN → COMMIT → BEGIN → ROLLBACK → COMMIT → 三次 txn 各自 state 独立

**B6**: COMMIT fsync 抛 OSError → Transaction 仍标 COMMITTED（数据已 write_main_page）；下次 open 看到 commit record → replay（幂等）

**B7**: autocommit DDL (CREATE TABLE) 失败 → 自动 rollback（catalog 不变）→ 抛原错误

**B8**: B+tree split 在 txn 内可能产生多个 alloc_page → 全部进同一 txn 的 pending_writes → commit 时 atomic 应用

## 11. 风险

**R1** — fsync 抛 OSError：Transaction 已标 COMMITTED（数据 write_main_page），但 fsync 失败。下次 open 时 recovery replay 同样结果（幂等）。调用方需 catch OSError 自决。

**R2** — Recovery 中 page_id 冲突（多 txn 修改同一 page）：后 commit 的覆盖先 commit 的（按 commit record 顺序）。文档化为"last-committer-wins"。

**R3** — WAL 末尾 partial record（process killed 在 record 写入中）：CRC 区不完整 → recovery 视为 corrupt → truncate 到该 record 之前 → 启动成功。

**R4** — engine-v1 UPDATE in-place + delete/insert fallback：所有 DML 统一经 `txn.write_page`，不再有"特殊 UPDATE"路径。UPDATE 在事务内不再直接调 `pager.write_page`。

**R5** — engine-v2 page-id collision workaround（`_IndexPager` + `_table_data_pages`）：acid 不变；WAL 记录 page_id，与 engine-v2 数据一致。

**R6** — engine-v2 `_IndexPager.alloc_page()` 内部仍调 `Pager.alloc_page()`：WAL 路径需在 `_alloc_data_page` 等处插入 txn.write_page 包装；IndexManager 内部的 split 等多 page 操作全部进同一 txn。

**R7** — executor.py 已 1196 行（+276 over budget from engine-v2）：本 change 再加 ~84 → 1280。仍在预算内但余量小。

## 12. 测试矩阵

### 单元

- `tests/unit/test_wal.py` — append/iter/truncate/CRC/header 验证（~12 tests）
- `tests/unit/test_transaction.py` — 状态机转移 + write_page/commit/rollback（~8 tests）
- `tests/unit/test_acid_parser.py` — BEGIN/COMMIT/ROLLBACK parse（~5 tests）

### 集成

- `tests/integration/test_acid.py` — BEGIN...COMMIT 跨进程可见；ROLLBACK 不可见（~6 tests）
- `tests/integration/test_ddl_in_transaction.py` — CREATE TABLE 在 ROLLBACK 后无副作用（~4 tests）
- `tests/integration/test_crash_recovery.py` — 模拟 kill -9 后 reopen 数据一致（~6 tests）
- `tests/integration/test_pager_v3_header.py` — schema=0x03 验证 + v2 file SchemaMismatch（~3 tests）
- `tests/integration/test_autocommit.py` — 单语句 auto-commit 行为（~4 tests）

### fuzz

- `tests/integration/test_recovery_fuzz.py` — 随机生成 WAL records → recovery → 一致性（~3 tests）

### 回归

597 tests（main 当前）必须全绿。MVP / engine-v1 / constraints / types / aggregation / engine-v2 全部 baseline 行为保持。

## 13. 偏差预警

实施中可能发现的偏差（提前文档化，便于归档时汇报）：

1. **executor.py 1196 → ~1280 行**：虽在预算内但余量小；若发现更多事务钩子（如 savepoint 钩子），可能突破 → 拆 `_exec_drop_table` 到独立模块作后续重构
2. **WAL fsync 性能**：fsync on commit 是 10-100x slower than no-fsync；对 demo 友好但若后续需要批量导入需添加 async fsync 选项
3. **recovery replay 与 free list 协调**：D6 DROP reclamation 释放的 page 可能被某 txn 回收后再分配；recovery replay 时若 COMMIT 记录中的 page_id 已被释放 + 重分配 → 覆盖目标 page 数据 → last-committer-wins 文档化为可接受

## 14. 决策记录

### D1: 独立 WAL 文件 `<db>.wal`

- 选项 A：独立文件 ← **选 A**
- 选项 B：主文件追加段
- 理由：A 路由 truncate 简单；B 路由 commit 后还需 merge

### D2: 简单整页 WAL（page-level）

- 选项 A：page-level WAL（物理写入完整 page） ← **选 A**
- 选项 B：logical redo（记录 SQL 操作）
- 理由：A 实现简单；B 需 redo executor；A 浪费空间但 demo 友好。B+tree index 维护无法 logical。

### D3: 隐式 auto-commit

- 选项 A：未 BEGIN 单语句 = 单事务 ← **选 A**
- 选项 B：必须显式 BEGIN
- 理由：MVP 行为不变，API 兼容；事务可选

### D4: 嵌套 BEGIN 报错

- 防止用户写错（例如忘了 COMMIT 又 BEGIN）

### D5: COMMIT 后 wal truncate_before(this_txn_id)

- 选项 A：truncate before 当前 txn ← **选 A**
- 选项 B：truncate 整个 wal
- 理由：保留 record 边界；未来并发扩展时其他 active txn 的 wal 不丢（本期单线程但 truncate 仍按 record 边界）

### D6: DDL 在显式事务内允许

- 选项 A：允许 + WAL 保护（Postgres 风格） ← **选 A**
- 选项 B：DDL auto-commit
- 理由：所有 DDL/DML 统一走 txn.write_page；用户在测试/迁移时更安全

### D7: 仅在 COMMIT 后 fsync main

- 选项 A：仅 fsync main on COMMIT ← **选 A**
- 选项 B：WAL append + COMMIT 两处都 fsync
- 理由：A 是惯用 WAL 模式；commit fsync 抛错时 recovery replay 幂等达到同一最终状态

### D8: Schema version bump 到 0x03

- 选项 A：bump 到 0x03（手动 migrate） ← **选 A**
- 选项 B：保留 0x02 + WAL overlay
- 理由：A 清晰、不双轨；用户调 `migrate_v2_to_v3(path)` 处理边界

### D9: COMMIT/ROLLBACK 无 active txn 时报错

- 选项 A：报错 ← **选 A**
- 理由：避免掩盖用户错误

### D10: WAL CRC 错 → truncate 到最后 valid + 启动

- 选项 A：truncate + log warning + 启动 ← **选 A**
- 选项 B：全量拒绝 + 报错退出
- 理由：保留最后已 commit 的数据；不丢；corruption 可见（log）

## 15. 实施路线

按 ~6 个 task 推进（与 `openspec/changes/tinydb-acid/tasks.md` 大致对应）：

1. **WAL 基础** — `wal.py` + 单元测试
2. **Pager schema 升级 + write_main_page / fsync_main** — `pager.py` 改造
3. **Transaction 状态机** — `transaction.py` + 单元测试
4. **Parser 新增 Begin/Commit/Rollback** — `parser.py` + `tokenizer.py`
5. **Recovery 实现 + 启动期集成** — `recovery.py` + `pager.py` open 钩子
6. **Executor 改造 + 集成测试** — `executor.py` + DDL/DML 路由 + crash recovery + fuzz

每个 task 内部遵循 TDD：RED → GREEN → COMMIT。详见 writing-plans 阶段产出的实施 plan。

---

**附录 A — 与 openspec/changes/tinydb-acid/design.md 的关系**

`openspec/changes/tinydb-acid/design.md` 是 OpenSpec 三件套之一，结构较精简。本文档是 Comet 工作流要求的"实现级 Design Doc"，包含完整行数预算、模块边界、风险矩阵、决策记录（D1-D10）、测试矩阵。两者内容一致；本文档是 build 阶段 implementer 的直接对照表。