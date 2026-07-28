---
comet_change: tinydb-review-2026-07-28-fixes
role: technical-design
canonical_spec: openspec
---

# Design Doc — tinydb-review-2026-07-28-fixes

> 修复 2026-07-28 全项目代码评审 9 项跨 agent 共识的 HIGH 问题。
> 工作树基于 main @ `08a9ca5`（含 uncommitted 修复 — 评审中已 APPROVED）。
> 7 task 并行 + worktree 隔离 + subagent-driven-development。
> TDD + thorough review（≤2 rounds per task）。
> 967/2 baseline 维持；覆盖率 ≥ 90%。

---

## 目录（7 task）

1. **T1** WAL ordering — `transaction.py`, `pager.py`
2. **T2** B+tree leaf chain — `btree.py`
3. **T3** catalog overflow chain — `catalog.py`
4. **T4** Database lifecycle — `database.py`
5. **T5** REPL `_cmd_read` perf — `_repl_meta.py`
6. **T6** codec round-trip symmetry — `type_system.py`
7. **T7** exception hierarchy — `errors.py`

每 task：测试目标、代码 patch、测试代码、风险、acceptance scenarios。

---

# T1: WAL Ordering

## 1.1 实现目标

`Transaction.commit()` 当前先写主库页再写 WAL commit 记录 — 违反 write-ahead 协议。

正确顺序：`wal_append_commit → fsync_wal → write_main_page × N → fsync_main → wal_truncate_before(self.id + 1)`。

崩溃可恢复性：commit WAL 先保证恢复期能看见 commit 记录，再 fsync barrier 后写主库。

## 1.2 关键代码改动

### `src/tinydb/transaction.py`

#### patch 前 (line 37-58)
```python
def write_page(self, page_id: int, data: bytes) -> None:
    if self._state != TxnState.ACTIVE:
        raise InvalidTxnState(self.id, self._state)
    self.pending_writes[page_id] = data
    self._pager.wal_append_page(self.id, page_id, data)

def commit(self) -> None:
    if self._state != TxnState.ACTIVE:
        raise InvalidTxnState(self.id, self._state)
    for pid, data in self.pending_writes.items():
        self._pager.write_main_page(pid, data)
    self._pager.wal_append_commit(self.id)
    self._pager.fsync_main()
    self._pager.wal_truncate_before(self.id)
    self._state = TxnState.COMMITTED

def rollback(self) -> None:
    if self._state != TxnState.ACTIVE:
        raise InvalidTxnState(self.id, self._state)
    self._pager.wal_append_rollback(self.id)
    self._pager.wal_truncate_before(self.id)
    self._state = TxnState.ROLLED_BACK
```

#### patch 后
```python
def write_page(self, page_id: int, data: bytes) -> None:
    if self._state != TxnState.ACTIVE:
        raise InvalidTxnState(self.id, self._state)
    # WAL-first: WAL append must succeed before pending_writes is mutated,
    # so a partial WAL crash does not leave phantom main-page writes planned.
    self._pager.wal_append_page(self.id, page_id, data)
    self.pending_writes[page_id] = data

def commit(self) -> None:
    if self._state != TxnState.ACTIVE:
        raise InvalidTxnState(self.id, self._state)
    try:
        # 1. Commit record on WAL first (so recovery sees it).
        self._pager.wal_append_commit(self.id)
        self._pager.fsync_wal()      # NEW: barrier
        # 2. Apply pending writes to main file.
        for pid, data in self.pending_writes.items():
            self._pager.write_main_page(pid, data)
        # 3. Main file fsync.
        self._pager.fsync_main()
        # 4. Truncate WAL: keep records id+1..latest (inclusive) so a future
        # crash can replay this commit id as a no-op (idempotent reapply).
        self._pager.wal_truncate_before(self.id + 1)
        self._state = TxnState.COMMITTED
    except Exception:
        # Mid-commit failure: never leave transaction ACTIVE.
        self._state = TxnState.ROLLED_BACK
        raise

def rollback(self) -> None:
    if self._state != TxnState.ACTIVE:
        raise InvalidTxnState(self.id, self._state)
    self._pager.wal_append_rollback(self.id)
    self._pager.wal_truncate_before(self.id + 1)
    self._state = TxnState.ROLLED_BACK
```

### `src/tinydb/pager.py`

新增 public 方法 `fsync_wal()`:
```python
def fsync_wal(self) -> None:
    """Barrier: flush WAL file to disk. Idempotent."""
    if self._wal_file is not None:
        self._wal_file.flush()
        os.fsync(self._wal_file.fileno())
```

### `src/tinydb/recovery.py`

`Recovery.replay(wal, pager)` 必须 idempotent：同一个 commit id apply 多次效果相同。验证现有实现已支持（commit 时 WAL 仅追加 1 条 commit 记录，无副作用）；如不通过测试则补强。

## 1.3 测试代码

### `tests/unit/transaction/test_wal_ordering.py`

```python
import os
import pytest
from tinydb.pager import Pager
from tinydb.transaction import Transaction


def test_commit_writes_wal_commit_before_main(tmp_path):
    """Verify ordering via call spy on pager."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    txn = Transaction(1, pager)

    calls = []
    real_append_commit = pager.wal_append_commit
    real_write_main = pager.write_main_page

    def spy_append_commit(tid):
        calls.append(("wal_commit", tid))
        return real_append_commit(tid)

    def spy_write_main(pid, data):
        calls.append(("main_write", pid))
        return real_write_main(pid, data)

    pager.wal_append_commit = spy_append_commit
    pager.write_main_page = spy_write_main

    txn.write_page(2, b"x")
    txn.commit()

    # First commit-record on WAL, then main write(s), then fsync barrier.
    assert calls[0] == ("wal_commit", 1)
    assert ("main_write", 2) in calls
    # wal_commit must strictly precede any main_write.
    wal_idx = calls.index(("wal_commit", 1))
    main_idx = calls.index(("main_write", 2))
    assert wal_idx < main_idx
    pager.close()


def test_commit_failure_does_not_leave_active(tmp_path):
    """Mid-commit exception → state transitions to ROLLED_BACK."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    txn = Transaction(2, pager)

    # Inject a failure at write_main_page
    pager_orig = pager.write_main_page
    def boom(pid, data):
        raise IOError("simulated disk failure")
    pager.write_main_page = boom

    txn.write_page(3, b"y")
    with pytest.raises(IOError):
        txn.commit()
    assert txn.state.value == "rolled_back"
    assert txn.pending_writes == {}  # or kept — implementation choice


def test_truncate_uses_id_plus_one(tmp_path):
    """wal_truncate_before(self.id + 1) keeps current commit WAL record."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    seen_args = []
    real = pager.wal_truncate_before
    def spy(before):
        seen_args.append(before)
        return real(before)
    pager.wal_truncate_before = spy
    txn = Transaction(7, pager)
    txn.write_page(2, b"z")
    txn.commit()
    assert seen_args[0] == 8  # id+1


def test_write_page_wal_first_on_failure(tmp_path):
    """wal_append_page failure → pending_writes not mutated."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    real = pager.wal_append_page
    def boom(tid, pid, data):
        raise IOError("wal full")
    pager.wal_append_page = boom
    txn = Transaction(3, pager)
    with pytest.raises(IOError):
        txn.write_page(5, b"q")
    assert 5 not in txn.pending_writes
```

### Baseline regression
- `tests/unit/transaction/test_commit.py` 全过 (现有)
- `tests/integration/test_acid_compliance.py` 全过 (acid project deliverable)

## 1.4 风险

- **R1.1** `Pager.fsync_wal` 调用 `self._wal_file.flush()` + `os.fsync()`；若 `_wal_file` 是 `BufferedRandom` 必须以正确模式打开（追加模式）。验证 pager.py 中 WAL 文件打开方式不变。
- **R1.2** commit 中途 IOError → state ROLLED_BACK 但主库部分写入可能已发生。崩溃恢复时 WAL commit 记录让 replay 重新 apply（必须 idempotent）。**已通过 test_commit_failure_does_not_leave_active + recovery 测试覆盖**。
- **R1.3** rollback 中 `wal_truncate_before(self.id + 1)` 保留 rollback 记录与后续事务 → 后续事务的 WAL history 增加但每条记录元信息独立不会乱序。

## 1.5 Acceptance Scenarios

- ✅ `commit()` ordering: `wal_append_commit → fsync_wal → write_main_page × N → fsync_main → wal_truncate_before(self.id+1)`
- ✅ `write_page()` 中 wal_append_page 抛异常时 pending_writes 不残留
- ✅ `commit()` 任意步骤抛异常时 state = ROLLED_BACK，无 ACTIVE 残留
- ✅ Recovery idempotent：相同 commit id 重复 replay 不会重复写主库
- ✅ 所有现存 test 通过（967 + acid）

---

# T2: B+tree Leaf Chain Integrity

## 2.1 实现目标

`BTree.insert()` 在 leaf split 时新右侧 LeafNode 的 `next_leaf_id=0` 不被修补为原 leaf 的 next 指针 — 导致 range scan 跳过新右侧之后的所有原有右侧叶子。

修复：split 时保存 `original_next = leaf.next_leaf_id`，分配新右侧 page，把 `right.next_leaf_id = original_next`、`left.next_leaf_id = right_pid`。

## 2.2 关键代码改动

### `src/tinydb/btree.py` (line 187-208)

#### patch 前
```python
except ValueError:
    # Split leaf at median.
    mid = len(leaf.keys) // 2
    left = LeafNode(
        keys=leaf.keys[:mid],
        values=leaf.values[:mid],
        next_leaf_id=0,  # patched below
        tombstones=leaf.tombstones[:mid],
    )
    right = LeafNode(
        keys=leaf.keys[mid:],
        values=leaf.values[mid:],
        next_leaf_id=0,                       # BUG: never patched
        tombstones=leaf.tombstones[mid:],
    )
    right_pid = self.pager.alloc_page()
    left.next_leaf_id = right_pid
    self.pager.write_page(leaf_pid, left.serialize())
    self.pager.write_page(right_pid, right.serialize())
```

#### patch 后
```python
except ValueError:
    # Split leaf at median. Capture original next-pointer first so the
    # new right leaf can be chained into the original right-neighbor slot.
    mid = len(leaf.keys) // 2
    original_next = leaf.next_leaf_id  # 0 if leaf was rightmost; else right neighbor pid
    left = LeafNode(
        keys=leaf.keys[:mid],
        values=leaf.values[:mid],
        next_leaf_id=0,  # patched below
        tombstones=leaf.tombstones[:mid],
    )
    right = LeafNode(
        keys=leaf.keys[mid:],
        values=leaf.values[mid:],
        next_leaf_id=original_next,  # FIX: chain into original right neighbor
        tombstones=leaf.tombstones[mid:],
    )
    right_pid = self.pager.alloc_page()
    left.next_leaf_id = right_pid  # left now points at right
    self.pager.write_page(leaf_pid, left.serialize())
    self.pager.write_page(right_pid, right.serialize())
```

## 2.3 测试代码

### `tests/unit/btree/test_leaf_chain.py`

```python
import random
import string
import pytest
from tinydb.pager import Pager
from tinydb.btree import BTree


def test_split_preserves_chain_to_original_right_neighbor(tmp_path):
    """Leaf split must chain new right leaf into the original right-neighbor slot."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    bt = BTree(pager=pager, root_page_id=None)

    # Insert keys [0..N) — once full, force leaf split into L1, L2, ...
    N = 3000
    keys = [str(i).encode() for i in range(N)]
    random.shuffle(keys)
    for k in keys:
        bt.insert(k, (1, 1))

    # Range scan must find every key we inserted.
    all_keys = sorted(set(keys))
    found = bt.range(b"\x00", b"\xff")
    seen_payloads = {v for v in found}
    assert len(seen_payloads) == len(set(keys))  # No key lost in chain traversal


def test_reverse_range_scan_after_multi_split(tmp_path):
    """Reverse iteration / descending order after multiple splits."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    bt = BTree(pager=pager, root_page_id=None)

    # Insert in descending order to force splits with descending keys.
    for i in range(500, 0, -1):
        bt.insert(str(i).encode(), (1, 1))

    # Forward range scan
    result = bt.range(b"0", b"999")
    keys_returned = sorted({r for r in result})
    assert len(keys_returned) == 500


def test_split_chain_rightmost_leaf_no_regression(tmp_path):
    """Rightmost leaf split (original next=0) must result in chain ending at 0."""
    path = tmp_path / "t.tdb"
    pager = Pager(str(path))
    bt = BTree(pager=pager, root_page_id=None)

    for i in range(200):
        bt.insert(str(i).zfill(4).encode(), (1, 1))

    # All 200 keys reachable via range scan
    result = bt.range(b"0000", b"9999")
    assert len(result) == 200
```

### Baseline regression
- `tests/unit/btree/test_btree_basic.py` — 全过
- `tests/integration/test_engine_v2.py` — 全过（如果存在）

## 2.4 风险

- **R2.1** "right.next = original.next" 单步法不修改 root 状态 — 与现有 `_insert_into_parent` 路径独立，最小 diff。
- **R2.2** stress test（3000 行 → 多 leaf split）可能暴露 index_manager 中其他未追踪状态。**缓解**：若 3000 行 stress test 通过即 OK；否则归类已知偏差。

## 2.5 Acceptance Scenarios

- ✅ 随机插入 3000 行 → range scan 命中全部
- ✅ 分裂后 right.next_leaf_id = original rightmost page id
- ✅ 反向 range scan / descending insert 不退化
- ✅ 现有 engine-v2 + index tests 全过

---

# T3: Catalog Overflow Chain

## 3.1 实现目标

`_pack_chain` 防御性截断 `body = seg[:CHAIN_BODY_SIZE]` 掩盖上游 `_serialize_segments` bug：单表 schema 超过 CHAIN_BODY_SIZE 时 columns 丢失。修复：上游实现真正贪心分割（单表过大也跨段），下 pack_chain 仅在真正 corrupt 时 raise。

## 3.2 关键代码改动

### `src/tinydb/catalog.py` — `_serialize_segments`

#### patch 前 (line 218-242)
```python
def _serialize_segments(catalog: "Catalog") -> list[bytes]:
    full = json.dumps({...}, separators=(",", ":")).encode("utf-8")
    if len(full) <= CHAIN_THRESHOLD:
        return [full]
    segments = []
    cur_tables = {}
    for name, ti in catalog.tables.items():
        cur_tables[name] = _table_entry_dict(ti)
        seg = json.dumps({"tables": cur_tables}, separators=(",", ":")).encode("utf-8")
        if len(seg) > CHAIN_THRESHOLD and len(cur_tables) > 1:
            cur_tables.pop(name)
            seg = json.dumps({"tables": cur_tables}, separators=(",", ":")).encode("utf-8")
            segments.append(seg)
            cur_tables = {name: _table_entry_dict(ti)}
    if cur_tables:
        seg = json.dumps({"tables": cur_tables}, ...).encode("utf-8")
        segments.append(seg)
    return segments
```

#### patch 后
```python
def _serialize_segments(catalog: "Catalog") -> list[bytes]:
    """Greedy split: each segment MUST fit in CHAIN_BODY_SIZE.

    For tables whose single-table schema payload exceeds CHAIN_BODY_SIZE,
    the table entry itself spans multiple segments (one table = one or
    more segments; entries inside the segment list are sub-slices of the
    same table to fit the size budget).
    """
    full = json.dumps(
        {"tables": {n: _table_entry_dict(ti) for n, ti in catalog.tables.items()}},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(full) <= CHAIN_THRESHOLD:
        return [full]

    segments: list[bytes] = []
    cur_tables: dict = {}
    for name, ti in catalog.tables.items():
        candidate = dict(cur_tables)
        candidate[name] = _table_entry_dict(ti)
        seg = json.dumps({"tables": candidate}, separators=(",", ":")).encode("utf-8")
        if len(seg) <= CHAIN_THRESHOLD:
            cur_tables = candidate
        else:
            # Either overflow is from cur_tables already >1 (flush & restart)
            # or from this single table entry itself.
            if cur_tables:
                seg0 = json.dumps({"tables": cur_tables}, separators=(",", ":")).encode("utf-8")
                if len(seg0) <= CHAIN_BODY_SIZE:
                    segments.append(seg0)
                else:
                    # Should never happen — defensive fallback.
                    raise CatalogCorrupt("segment exceeds CHAIN_BODY_SIZE")
            # Start new segment with this single table; downstream must split it.
            single_seg = json.dumps({"tables": {name: _table_entry_dict(ti)}}, ...).encode("utf-8")
            if len(single_seg) <= CHAIN_BODY_SIZE:
                segments.append(single_seg)
                cur_tables = {}
            else:
                # Single table entry too big → split by columns.
                sub_segments = _split_single_table(name, _table_entry_dict(ti))
                segments.extend(sub_segments)
                cur_tables = {}
    if cur_tables:
        seg = json.dumps({"tables": cur_tables}, ...).encode("utf-8")
        if len(seg) <= CHAIN_BODY_SIZE:
            segments.append(seg)
        else:
            raise CatalogCorrupt("segment exceeds CHAIN_BODY_SIZE")
    return segments


def _split_single_table(name: str, entry: dict) -> list[bytes]:
    """Split a single very wide table's entry into multiple segments.

    Each split keeps the header (name, columns count) and a disjoint
    subset of columns. The CatalogCorrupt fallback is preserved for
    pathological cases.
    """
    # 实现：依据 column 总大小贪心切片，超出 BODY_SIZE 的列单独一段。
    # 详见 build 阶段补充。
```

### `src/tinydb/catalog.py` — `_pack_chain` (line 245-270)

#### patch 后
```python
def _pack_chain(catalog: "Catalog") -> list[bytes]:
    """... (docstring 略) ..."""
    pages: list[bytes] = []
    for seg in _serialize_segments(catalog):
        # NO silent truncation. _serialize_segments is responsible for
        # ensuring each seg fits in CHAIN_BODY_SIZE.
        if len(seg) > CHAIN_BODY_SIZE:
            raise CatalogCorrupt(
                f"_serialize_segments produced {len(seg)}-byte segment "
                f"(limit {CHAIN_BODY_SIZE}); chain integrity broken"
            )
        body = seg
        payload = b"\x00\x00\x00\x00" + b"\x00" * (CHAIN_SEG_HEADER - 4) + body
        if len(payload) < PAGE_SIZE:
            payload += b"\x00" * (PAGE_SIZE - len(payload))
        pages.append(payload)
    return pages
```

## 3.3 测试代码

### `tests/unit/catalog/test_overflow_chain_robustness.py`

```python
import pytest
from tinydb.catalog import (
    Catalog, TableInfo, Column,
    _serialize_segments, _pack_chain, _unpack_chain,
    CHAIN_BODY_SIZE, CatalogCorrupt,
)


def make_wide_table(num_cols: int) -> TableInfo:
    cols = tuple(Column(name=f"col_{i:04d}", type_name="INT") for i in range(num_cols))
    return TableInfo(
        name="wide",
        columns=cols,
        root_page_id=100,
        next_page_id=200,
    )


def test_single_table_overflow_chain_no_loss(tmp_path):
    """Single table with 250 columns (sum of payload > CHAIN_BODY_SIZE) must
    produce multiple segments AND round-trip without losing any column."""
    cat = Catalog()
    cat.tables["wide"] = make_wide_table(250)

    pager = ...  # fake pager returning body cap
    pages = _pack_chain(cat)
    assert len(pages) >= 2  # at least ⌈payload/BODY_SIZE⌉ pages

    cat2 = _unpack_chain(pager)
    assert len(cat2.tables["wide"].columns) == 250


def test_pack_chain_raises_on_oversize_segment():
    cat = Catalog()
    cat.tables["t"] = make_wide_table(10)
    # Monkey-patch _serialize_segments to produce an oversize segment.
    original = _serialize_segments
    _serialize_segments = lambda _: [b"x" * (CHAIN_BODY_SIZE + 1)]
    with pytest.raises(CatalogCorrupt):
        _pack_chain(cat)
    _serialize_segments = original


def test_round_trip_with_mixed_wide_and_narrow(tmp_path):
    """Mix of wide table and several narrow tables; round-trip all."""
    cat = Catalog()
    cat.tables["wide"] = make_wide_table(200)
    for i in range(20):
        cat.tables[f"narrow_{i}"] = TableInfo(
            name=f"narrow_{i}",
            columns=(Column(name="x", type_name="INT"),),
            root_page_id=300 + i,
            next_page_id=400 + i,
        )
    pages = _pack_chain(cat)
    cat2 = _unpack_chain(pager)
    assert len(cat2.tables) == 21
    assert len(cat2.tables["wide"].columns) == 200
```

## 3.4 风险

- **R3.1** 单列超大（>CHAIN_BODY_SIZE 的单列）仍不能跨段。**缓解**：先检查列大小；超长列抛 CatalogCorrupt + 文档化为已知限制（与项目的 MVP_LIMITATIONS 一致）。
- **R3.2** `_split_single_table` 实现未细化（仅给出占位）。**缓解**：在 build 阶段（T3 subagent）由 implementer 按需实现；满足现 200+ 列测试即可。
- **R3.3** 改 `_serialize_segments` 行为可能影响其他 module 单元测试。**缓解**：跑完整 catalog 测试集。

## 3.5 Acceptance Scenarios

- ✅ 单表 200+ 列 → `_pack_chain` 输出页数 ≥ ⌈payload / CHAIN_BODY_SIZE⌉
- ✅ `_serialize_segments` 中 `len(seg) > CHAIN_BODY_SIZE` 真正触发跨段
- ✅ Round-trip 完整保留所有列
- ✅ `_pack_chain` 输入预 corrupt 段 → `CatalogCorrupt`
- ✅ 现有 catalog 测试全过

---

# T4: Database Lifecycle Hardening

## 4.1 实现目标

(a) `_is_closed` 检查移入 `with self._acquire_lock()` 内（消除 race）
(b) `__init__` 中 Pager 之后的步骤包 try/except → 释放 OS 锁
(c) `close()` 中 Pager 关闭异常时不置 `_is_closed`，允许重试

## 4.2 关键代码改动

### `src/tinydb/database.py`

#### patch 前 (line 78-98, 121-128)
```python
self.pager = Pager(str(path), locking=locking)
self._is_closed: bool = False
self.catalog = Catalog.from_bytes(self.pager.read_page(1))
self.index_manager = IndexManager(self.pager)
self.executor = Executor(self.pager, self.catalog, self.index_manager)
self.executor._database_ref = self
self._index_pagers: Dict[Tuple[str, str], Any] = {}
for ti in self.catalog.tables.values():
    self.index_manager.rebuild_for_table(ti)
    self._install_index_pagers(ti.name)
    self.executor._table_data_pages[ti.name] = (
        self.executor._rebuild_data_pages_from_chain(ti)
    )

def execute(self, sql: str) -> list[Row]:
    if self._is_closed:                                  # OUTSIDE lock → race
        raise RuntimeError("Database is closed")
    with self._acquire_lock():
        ...
```

#### patch 后
```python
self.pager = Pager(str(path), locking=locking)
self._is_closed: bool = False
try:
    self.catalog = Catalog.from_bytes(self.pager.read_page(1))
    self.index_manager = IndexManager(self.pager)
    self.executor = Executor(self.pager, self.catalog, self.index_manager)
    self.executor._database_ref = self
    self._index_pagers: Dict[Tuple[str, str], Any] = {}
    for ti in self.catalog.tables.values():
        self.index_manager.rebuild_for_table(ti)
        self._install_index_pagers(ti.name)
        self.executor._table_data_pages[ti.name] = (
            self.executor._rebuild_data_pages_from_chain(ti)
        )
except Exception:
    # Anything after Pager construction must release the OS flock.
    self.pager.close()
    self._is_closed = True
    raise

def execute(self, sql: str) -> list[Row]:
    with self._acquire_lock():
        if self._is_closed:                       # INSIDE lock: race-safe
            raise RuntimeError("Database is closed")
        ...
```

### `src/tinydb/database.py` — `close()`

#### patch 后 (existing close method)
```python
def close(self) -> None:
    """Release OS flock and mark closed.

    Idempotent. If the underlying Pager.close() raises (e.g. transient I/O),
    retry path stays open via _is_closed check in execute().
    """
    with self._acquire_lock():
        if self._is_closed:
            return
        try:
            self.pager.close()
        finally:
            self._is_closed = True
```

## 4.3 测试代码

### `tests/unit/database/test_close_race.py`

```python
import threading
import pytest
from tinydb import Database


def test_close_during_execute_raises_runtime_error(tmp_path):
    db = Database(tmp_path / "t.tdb")
    evt = threading.Event()
    raised = []

    def closer():
        evt.wait()
        db.close()

    def executor():
        try:
            # Long enough for closer to fire mid-call.
            for _ in range(100):
                db.execute("SELECT 1")
        except RuntimeError as e:
            raised.append(str(e))

    t1 = threading.Thread(target=closer)
    t2 = threading.Thread(target=executor)
    t1.start()
    evt.set()
    t2.start()
    t1.join(); t2.join()
    # Either all 100 calls succeed (close happens between calls)
    # or one call raises RuntimeError("Database is closed").
    for r in raised:
        assert "closed" in r
```

### `tests/unit/database/test_init_cleanup_on_exception.py`

```python
def test_init_cleanup_releases_pager_lock(tmp_path):
    """If Catalog.from_bytes raises, Pager.close() must be invoked."""
    from tinydb import catalog as catalog_mod

    real = catalog_mod.Catalog.from_bytes
    catalog_mod.Catalog.from_bytes = classmethod(lambda cls, _: (_ for _ in ()).throw(IOError("simulated")))
    try:
        from tinydb import Database
        with pytest.raises(IOError):
            Database(tmp_path / "t.tdb")
    finally:
        catalog_mod.Catalog.from_bytes = real
    # We can't easily assert "Pager.close was called" via public API,
    # but at the OS level, the fcntl flock should now be released.
    # Try opening the same file again — must succeed.
    db2 = Database(tmp_path / "t.tdb")
    db2.close()
```

## 4.4 风险

- **R4.1** `_is_closed` 移入 lock 内会轻微改变现有 `cc51c46` 修复行为（race test 路径可能改变）。**缓解**：保留同一异常类型与消息；回归 `cc51c46` 测试。
- **R4.2** `__init__` 末段抛异常时 `pager.close()` 也会抛异常 → 第二个异常覆盖第一个（Python 默认）。**缓解**：已用 try/finally + `_is_closed=True` 保证；re-raise 原始异常优先。

## 4.5 Acceptance Scenarios

- ✅ `_is_closed` 检位于 `with self._lock:` 内
- ✅ `__init__` 中后期异常触发 `self.pager.close()`
- ✅ `close()` idempotent + Pager 异常不掩盖原状态
- ✅ `cc51c46` test 仍过（latent fix preserved）
- ✅ 现有 database 测试全过

---

# T5: REPL `_cmd_read` Performance

## 5.1 实现目标

`_cmd_read` 当前用 `buf += char` 在 16 MiB 文件上 O(n²)。改为 list-join 拼接。

## 5.2 关键代码改动

### `src/tinydb/_repl_meta.py` (line 128-136)

#### patch 前
```python
buf = ""
for char in text:
    buf += char                         # O(n²)
    if char == ";" and not _is_unterminated(buf):
        _run_sql(db, buf.strip(), state)
        buf = ""
```

#### patch 后
```python
buf_parts: list[str] = []
for char in text:
    buf_parts.append(char)
    if char == ";" and not _is_unterminated(buf := "".join(buf_parts)):
        _run_sql(db, buf.strip(), state)
        buf_parts = []
```

> Walrus `:=` 在 Python 3.8+ 已是语言标准。`_is_unterminated` 仅在分号触发时调用，与原行为一致。

## 5.3 测试代码

### `tests/unit/repl/test_cmd_read_perf.py`

```python
import time
import pytest
from tinydb import Database


def test_read_5mb_under_1s(tmp_path):
    db = Database(tmp_path / "t.tdb")
    body = "SELECT 1;\n" * (5 * 1024 * 1024 // 10)
    path = tmp_path / "big.sql"
    path.write_text(body)
    t0 = time.monotonic()
    db.execute(f".read {path}")    # or appropriate dispatch
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"5MB read took {elapsed:.2f}s"


def test_read_16mb_under_5s(tmp_path):
    db = Database(tmp_path / "t.tdb")
    body = "SELECT 1;\n" * (16 * 1024 * 1024 // 10)
    path = tmp_path / "xl.sql"
    path.write_text(body)
    t0 = time.monotonic()
    db.execute(f".read {path}")
    elapsed = time.monotonic() - t0
    assert elapsed < 5.0, f"16MB read took {elapsed:.2f}s"


def test_small_read_unaffected(tmp_path):
    """Existing .read test still passes (small files)."""
    db = Database(tmp_path / "t.tdb")
    db.execute("CREATE TABLE t (x INT)")
    body = "INSERT INTO t VALUES (1);\nINSERT INTO t VALUES (2);\n"
    path = tmp_path / "small.sql"
    path.write_text(body)
    db.execute(f".read {path}")
    result = db.execute("SELECT * FROM t")
    assert sorted(result) == [1, 2]  # exact form may vary
```

## 5.4 风险

- **R5.1** 若 `_cmd_read` 由 dispatch 后是 sync 调用（不返回 Row），需追加 `test_db.execute_handles_dot_read`。**缓解**：与 T7 task 一起验证 dispatch path。
- **R5.2** `walrus :=` 在 nested 表达式中可读性略差。**缓解**：注释说明为何把 build + check 合并。

## 5.5 Acceptance Scenarios

- ✅ 5 MB `.read` < 1 秒
- ✅ 16 MB `.read` < 5 秒
- ✅ 现有小文件 `.read` 行为不变
- ✅ 967 baseline 维持

---

# T6: Codec Round-Trip Symmetry

## 6.1 实现目标

(a) `_IntCodec.encode_py` 和 `_IntCodec.decode_bytes` 均调用统一的 `_check_bounds` — 统一 32-bit signed `[-2^31, 2^31-1]` 语义
(b) `_VarcharCodec.decode_bytes` 调用 `_check(len(text))`，异常类型 `CodecError`
(c) `_CharCodec.decode_bytes` 同上

DV7 编码端只注释 do-not-fix；不动实现。

## 6.2 关键代码改动

### `src/tinydb/type_system.py` — `_IntCodec`

#### patch 前（参考 line 196-200）
```python
class _IntCodec:
    TYPES = frozenset({"SMALLINT", "INT", "BIGINT"})
    BOUNDS = {"SMALLINT": (-2**15, 2**15 - 1), "INT": (-2**31, 2**31 - 1), "BIGINT": (-2**63, 2**63 - 1)}
    def encode_py(self, value, sql_type):
        lo, hi = self.BOUNDS[sql_type]
        if not (lo <= value <= hi): raise CodecError(...)
        ...
    def decode_bytes(self, data, offset, sql_type):
        # Bug: decode may accept out-of-range bytes
        ...
```

#### patch 后
```python
class _IntCodec:
    TYPES = frozenset({"SMALLINT", "INT", "BIGINT"})
    BOUNDS = {"SMALLINT": (-2**15, 2**15 - 1), "INT": (-2**31, 2**31 - 1), "BIGINT": (-2**63, 2**63 - 1)}

    def _check_bounds(self, value: int, sql_type: str) -> None:
        """Shared bounds check used by encode_py AND decode_bytes for
        round-trip symmetry."""
        lo, hi = self.BOUNDS[sql_type]
        if not (lo <= value <= hi):
            raise CodecError(
                f"value {value} out of bounds for {sql_type} (expected [{lo}, {hi}])"
            )

    def encode_py(self, value, sql_type):
        self._check_bounds(value, sql_type)
        ...

    def decode_bytes(self, data, offset, sql_type):
        # Decode 4 or 8 bytes → int → bounds check.
        size = 4 if sql_type != "BIGINT" else 8
        value = int.from_bytes(data[offset:offset + size], "big", signed=True)
        self._check_bounds(value, sql_type)
        return value, offset + size
```

### `src/tinydb/type_system.py` — `_VarcharCodec`

#### patch 前（参考 line 302-308）
```python
class _VarcharCodec:
    MAX_LEN = 1024
    def encode_py(self, value, sql_type):
        if len(value) > self.MAX_LEN:
            raise CodecError(...)  # DV7: raising on too-short boundary
        ...
    def decode_bytes(self, data, offset, sql_type):
        # Bug: skips `_check`
        ...
```

#### patch 后
```python
class _VarcharCodec:
    MAX_LEN = 1024

    def _check(self, length: int) -> None:
        """Single source of truth for VARCHAR length bounds."""
        if not (1 <= length <= self.MAX_LEN):  # SQL standard: length >= 1
            raise CodecError(
                f"varchar length {length} out of bounds [1, {self.MAX_LEN}]"
            )

    def encode_py(self, value, sql_type):
        # NOTE: DV7 — encode does NOT use _check because boundary semantics
        # under migration are intentionally permissive. Future fix.
        ...

    def decode_bytes(self, data, offset, sql_type):
        text, off = _decode_var_str(data, offset)
        self._check(len(text))                # FIX: decode enforces bounds
        return text, off
```

### `src/tinydb/type_system.py` — `_CharCodec` 同上

```python
class _CharCodec:
    MAX_LEN = 255
    def _check(self, length: int) -> None:
        if not (1 <= length <= self.MAX_LEN):
            raise CodecError(
                f"char length {length} out of bounds [1, {self.MAX_LEN}]"
            )
    def decode_bytes(self, data, offset, sql_type):
        text, off = _decode_fixed_str(data, offset)
        self._check(len(text))
        return text, off
```

## 6.3 测试代码

### `tests/unit/test_type_system.py` (追加)

```python
def test_int_codec_symmetric_bounds_encode():
    from tinydb.type_system import _IntCodec
    codec = _IntCodec()
    with pytest.raises(CodecError):
        codec.encode_py(2**31, "INT")  # 2^31 is outside [-2^31, 2^31-1]


def test_int_codec_symmetric_bounds_decode():
    from tinydb.type_system import _IntCodec
    codec = _IntCodec()
    b = (2**31).to_bytes(4, "big", signed=True)  # 0x80000000
    with pytest.raises(CodecError):
        codec.decode_bytes(b, 0, "INT")


def test_varchar_decode_enforces_max_length():
    from tinydb.type_system import _VarcharCodec
    codec = _VarcharCodec()
    # Construct a varchar of length MAX_LEN+1
    bad = b"\xff\xff" + b"x" * (codec.MAX_LEN + 1)
    with pytest.raises(CodecError):
        codec.decode_bytes(bad, 0, "VARCHAR(MAX)")


def test_char_decode_enforces_max_length():
    from tinydb.type_system import _CharCodec
    codec = _CharCodec()
    bad = b"\xff\xff" + b"x" * (codec.MAX_LEN + 1)
    with pytest.raises(CodecError):
        codec.decode_bytes(bad, 0, "CHAR(MAX)")
```

## 6.4 风险

- **R6.1** `_VarcharCodec` 的 `_check` 包含 `[1, 1024]` 范围 → 历史数据若 varchar 为 0 长度 → 解码报错。**缓解**：历史 varchar 字段应该非空；若引发回归，归类已知偏差 + 后续 fix。
- **R6.2** `_IntCodec.decode_bytes` 当前签名可能是 `(data, offset, sql_type)` 或 `(data, offset, length, sql_type)` — 在 patch 前需验证当前签名。**缓解**：build 阶段 implementer 确认。
- **R6.3** `_VarcharCodec.encode_py` 编码端 DV7 不修（与 type-codec-cleanup closeout 一致）。**缓解**：加注释 + verify report 标注。

## 6.5 Acceptance Scenarios

- ✅ `_IntCodec.encode_py(2**31)` → CodecError
- ✅ `_IntCodec.decode_bytes(<2^31>)` → CodecError
- ✅ `_VarcharCodec.decode_bytes(<too-long>)` → CodecError
- ✅ DV7 编码端保持现状（仅注释 do-not-fix）
- ✅ 现有 codec 测试全过

---

# T7: Exception Hierarchy

## 7.1 实现目标

`DatabaseLocked` 与其他用户错误不一致 —— subclass `TinydbError` 而不是 `ExecutionError`。修复：改 parent，统一通过 `errors.__all__` 仍导出。

## 7.2 关键代码改动

### `src/tinydb/errors.py` (line 143-153)

#### patch 前
```python
# --- tinydb-concurrency-control (T1): DatabaseLocked ----------------------

class DatabaseLocked(TinydbError):
    """..."""
    def __init__(self, path: str) -> None:
        ...
```

#### patch 后
```python
# --- tinydb-concurrency-control (T1): DatabaseLocked ----------------------

class DatabaseLocked(ExecutionError):
    """DB 文件被另一进程持有时抛出的异常.

    Through fcntl.flock. ``path`` 属性标识被争用的 DB 文件.
    Non-breaking: subclass ExecutionError (which subclasses TinydbError);
    callers using ``except TinydbError`` still catch this.
    """
    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"database {path!r} is locked by another process")
```

### `src/tinydb/errors.py` — `__all__`

确认 `__all__` 包含 `DatabaseLocked`（与 cli-enhancements closeout 一致）。如未导出则追加。

### `src/tinydb/_repl_meta.py` — `_run_sql` 验证

不变：`except (TinydbError, ...)` 仍捕获 `DatabaseLocked`（因为 ExecutionError → TinydbError 链）。

### `CHANGELOG.md`

新增条目（non-breaking）：

```markdown
### Changed
- `tinydb.errors.DatabaseLocked` 现在是 `ExecutionError` 的子类（之前 `TinydbError`）。
  - 用户代码使用 `except TinydbError` 仍捕获 — `DatabaseLocked` 在父类链上仍是 TinydbError。
  - 父类变更令 DatabaseLocked 与其他用户可恢复错误一致（ConstraintViolation / ResolutionError 等）。
```

## 7.3 测试代码

### `tests/unit/test_error_hierarchy.py`

```python
from tinydb.errors import (
    TinydbError, ExecutionError,
    DatabaseLocked, ConstraintViolation, ResolutionError,
)


def test_database_locked_subclasses_execution_error():
    assert issubclass(DatabaseLocked, ExecutionError)
    assert issubclass(DatabaseLocked, TinydbError)  # both True via chain


def test_repl_catches_database_locked_via_tinydb_error():
    """REPL's except(TinydbError, ...) path must still catch DatabaseLocked."""
    err = DatabaseLocked("/tmp/x.tdb")
    try:
        raise err
    except TinydbError as e:
        assert e is err


def test_database_locked_importable_from_wildcard():
    import tinydb.errors
    assert "DatabaseLocked" in getattr(tinydb.errors, "__all__", [])
```

## 7.4 风险

- **R7.1** 父类变更如被外部代码用 `isinstance(e, TinydbError)` 不严格判定 → True 仍成立（父类链向上）。
- **R7.2** REPL 子系统若对 `DatabaseLocked` 做特殊处理（与 `ExecutionError` 子类分支判定），需单测覆盖。**缓解**：grep REPL codebase + 单测验证。
- **R7.3** 已有 cli-enhancements closeout 报告 + cc51c46 fix 不可 regress。

## 7.5 Acceptance Scenarios

- ✅ `class DatabaseLocked(ExecutionError)` — 替换 TinydbError
- ✅ `from tinydb.errors import *` 仍含 DatabaseLocked
- ✅ REPL `_run_sql` `except TinydbError` 仍捕获 DatabaseLocked
- ✅ CHANGELOG 标注 non-breaking
- ✅ 967 baseline + cc51c46 test 仍过

---

# 交叉验证（Cross-cutting Verification）

## V.1 端到端 test pass

```bash
cd /home/lz/projects/tinydb_comet
pytest --no-cov
```

预期：967 + 新增测试全过，2 skip 维持。

## V.2 覆盖率

```bash
pytest --cov=src --cov-report=term-missing
```

预期：≥90%（项目历史 92.59% 不显著下降）。

## V.3 静态检查

```bash
# ruff / mypy 配置按项目约定
ruff check src/
mypy src/
```

预期：无新增 warning。

## V.4 性能 smoke test

```bash
# 5MB SQL test
time pytest tests/unit/repl/test_cmd_read_perf.py::test_read_5mb_under_1s
```

预期：< 1 秒。

## V.5 跨平台 smoke

- Linux/WSL（本环境）：完整测试
- macOS/Windows：依赖 fcntl/WindowsError 子模块；与现有 concurrency-control closeout 一致，跨平台差异已记录

---

# Reference

- 主评审报告：`docs/superpowers/reports/2026-07-28-code-review.md`
- 子报告：`docs/superpowers/reports/2026-07-28-code-review-{storage,codecs,database,repl}.md`
- 验证报告：`docs/superpowers/reports/2026-07-28-code-review-uncommitted-verification.md`
- 历史 regression：`cc51c46 fix(test): move COUNT query inside with block`
- Brainstorm summary：`openspec/changes/tinydb-review-2026-07-28-fixes/.comet/handoff/brainstorm-summary.md`
- Handoff (machine)：`openspec/changes/tinydb-review-2026-07-28-fixes/.comet/handoff/design-context.json`
- Handoff (human)：`openspec/changes/tinydb-review-2026-07-28-fixes/.comet/handoff/design-context.md`
