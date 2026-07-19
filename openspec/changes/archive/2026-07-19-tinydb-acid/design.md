# Design: tinydb-acid

> **关联文档**：[proposal.md](./proposal.md) · [specs/](./specs/)

## Context

教学型嵌入式数据库若不知道 ACID 是什么，永远只是 demo。本 change 把"原子提交 + 崩溃恢复"作为基础能力交付。

## Goals / Non-Goals

**Goals：**
- `BEGIN` / `COMMIT` / `ROLLBACK` 三语句完整语义
- 隐式 auto-commit：未 BEGIN 时单语句 = 一次性事务
- 崩溃恢复：下次 open 时根据 wal 决定是否重放
- 嵌套 BEGIN 报错；COMMIT/ROLLBACK 没在 BEGIN 时报错
- WAL 文件 fsync 在 commit 时
- 启动期 recovery 完成后再 accept user queries

**Non-Goals：**
- 不引入 MVCC / 隔离级别
- 不引入 savepoint
- 不引入异步 fsync
- 不引入加密 / 校验

## Architecture

### WAL 文件格式

```
struct WalHeader {
    magic:    bytes 8   ("TINYWAL\x00")
    schema:   u8        (0x01)
    reserved: bytes 7
}

struct WalRecord {
    txn_id:   u64
    kind:     u8        (0=begin, 1=page_write, 2=commit, 3=rollback)
    page_id:  u32       (page_write 时)
    data:     u32       (page 数据长度)
    payload:  bytes
    crc32:    u32       (record 结尾)
}
```

WAL append-only；每次 write 追加一条；commit 时把 page_write 的 payload 写回主 db 文件。

### Transaction 状态机

```python
class Transaction:
    def __init__(self, txn_id, pager):
        self.id = txn_id
        self.pager = pager
        self.pending_writes: dict[int, bytes] = {}
        self.state = "active"

    def write_page(self, page_id: int, data: bytes):
        self.pending_writes[page_id] = data
        self.pager.wal_append(self.id, page_id, data)

    def commit(self):
        for pid, data in self.pending_writes.items():
            self.pager.write_main_file(pid, data)  # 写主 db
        self.pager.wal_append(self.id, kind=commit)
        self.pager.fsync_main()                     # fsync
        self.pager.wal_truncate_before(self.id)
        self.state = "committed"

    def rollback(self):
        self.pager.wal_append(self.id, kind=rollback)
        self.pager.wal_truncate_before(self.id)
        self.state = "rolled_back"
```

### Executor 改造

```python
def execute(stmt, ...):
    if isinstance(stmt, Begin):
        if current_txn is not None:
            raise ExecutionError("nested BEGIN not allowed")
        current_txn = new_transaction()
    elif isinstance(stmt, Commit):
        if current_txn is None:
            raise ExecutionError("COMMIT without BEGIN")
        current_txn.commit()
        current_txn = None
    elif isinstance(stmt, Rollback):
        if current_txn is None:
            raise ExecutionError("ROLLBACK without BEGIN")
        current_txn.rollback()
        current_txn = None
    else:
        # DML/DDL: 写入 current_txn 或新 auto-txn
        txn = current_txn or new_transaction(autocommit=True)
        apply(stmt, txn)
        if txn.autocommit:
            txn.commit()
            if current_txn is None:
                pass  # auto-txn 结束
```

### Crash Recovery

启动时序：

1. `Pager.open(path)` 读取 file header
2. `Pager.open` 检查 `<path>.wal` 是否存在
3. 若有，扫描 wal records：
   - 累积每 txn 的 page_writes
   - 遇 commit record → 应用其 page_writes 到 main file
   - 遇 rollback record → 丢弃其 page_writes
   - 遇 begin 但无 commit/rollback 的 txn → 当作 rollback 丢弃
4. truncate wal
5. 返回可用 db 状态

## Decisions

### D1: WAL 独立文件 `*.db.wal`

- 选项 A：独立文件 ← 选 A
- 选项 B：主文件追加段
- 理由：A 路由 truncate 容易；B 路由 commit 后还需 merge

### D2: 简单整页 WAL 而非 logical redo

- 选项 A：page-level WAL（物理写入完整 page） ← 选 A
- 选项 B：logical redo（记录 SQL）
- 理由：A 路由实现简单；B 路由需要 redo executor；A 浪费一点空间但 demo 友好

### D3: 隐式 auto-commit

- 选项 A：未 BEGIN 单语句 = 单事务 ← 选 A
- 选项 B：必须显式 BEGIN
- 理由：MVP 行为不变，API 兼容；事务可选

### D4: 嵌套 BEGIN 报错

- 防止用户写错（例如忘了 COMMIT 又 BEGIN）

### D5: COMMIT 后 wal truncate before(this_txn_id)

- 选项 A：truncate before 当前 txn ← 选 A
- 选项 B：truncate 整个 wal
- 理由：保留并发场景下其他 active txn 的 wal（虽然本期单线程，但 truncate 仍按 record 边界）

## Risks

- **R1**：WAL fsync 失败 → 数据库处于未知状态；调用方需自行判断（OS error 透传）
- **R2**：Recovery 中 page_id 重叠 / 冲突 → path 上显式断言：page_write 应用顺序为 commit record 顺序
- **R3**：WAL 文件与 main file 不一致（partial write 残留） → WAL CRC32 校验失败时截断到坏 record 之前
- **R4**：engine-v1 的 UPDATE in-place + delete/insert fallback 与事务语义冲突 → 全部 DML 路径统一经 txn；不再"特殊 UPDATE"
- **R5**：与现有 engine-v2（v2 文件头）共存：本期 schema_version 升 0x03，独立文件保留 v2 read 兼容

## Test Plan

- 单元 `tests/unit/test_wal.py`：append / commit / rollback / crc 校验
- 单元 `tests/unit/test_transaction.py`：状态机转移
- 集成 `tests/integration/test_crash_recovery.py`：模拟 kill -9 中途（KILL process，校验恢复后数据一致）
- 集成 `tests/integration/test_acid.py`：BEGIN → INSERT → COMMIT 跨 process 重启可见；BEGIN → INSERT → ROLLBACK 不可见
- 集成 `tests/integration/test_ddl_in_transaction.py`：CREATE/DROP TABLE 在 txn 内 commit 后持久；rollback 后无副作用
- fuzz `tests/integration/test_recovery_fuzz.py`：随机构造 wal records，验证恢复稳定
- 反向：MVP + engine-v1 + constraints + aggregation + engine-v2 测试全绿
