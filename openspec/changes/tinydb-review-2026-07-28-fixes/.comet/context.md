# Comet Design Handoff

- Change: tinydb-review-2026-07-28-fixes
- Phase: design
- Mode: compact
- Context hash: 13ea264b85dbe61801843f51298b3274d92abc34feaa5856cb1ef3ce29be849f

Generated-by: comet-handoff.sh

OpenSpec remains the canonical capability spec. This handoff is a deterministic, source-traceable context pack, not an agent-authored summary.

## openspec/changes/tinydb-review-2026-07-28-fixes/proposal.md

- Source: openspec/changes/tinydb-review-2026-07-28-fixes/proposal.md
- Lines: 1-118
- SHA256: ec164c3881909fb9142719392d3bd1132d44d51d94819be696af17e11f60598f

[TRUNCATED]

```md
# Proposal: 修复 2026-07-28 代码评审 9 项跨 agent 共识的 HIGH 问题

## Why

2026-07-28 全项目代码评审（5 个并行 agent：storage / codecs / database-core / REPL / uncommitted 验证）共发现 ~50 个问题，其中 9 个 HIGH 级别且被 ≥1 个 agent 独立验证：

1. **WAL 协议顺序违反原子性**（`transaction.py:45-50`，×2 agents 共识）——`commit()` 先写主库页再写 WAL COMMIT 记录。崩溃可导致主库写入无 COMMIT 记录。
2. **B+tree 叶节点链表断裂**（`btree.py:190`，code-verified，stress-test 显示 378/3000 行丢失）——叶子分裂时右侧新叶子 `next_leaf_id=0` 从不补链。
3. **catalog 溢出链静默截断**（`catalog.py:262`，code-verified）——`_pack_chain` 防御性截断掩盖上游 `_serialize_segments` bug；单表 200+ 列丢失。
4. **`Database` `_is_closed` 检查-然后-执行 race**（`database.py:124-127`，code-verified）——close() 与 execute() 之间的竞争窗口。
5. **`_cmd_read` 在 16 MiB 文件上 O(n²)**（`_repl_meta.py:128-136`，×2 agents，NEW 性能问题）——逐字符 `buf += char` 拼接。
6. **`Database.__init__` 异常不关闭 Pager**（`database.py:79-97`，code-verified）——初始化后半段异常导致 OS 文件锁泄漏。
7. **`_IntCodec` 往返不对称**（`type_system.py:196-200`，code-verified）——`validate` 拒绝 2^31 边界但 `decode_bytes` 接受。
8. **`DatabaseLocked(TinydbError)` 不一致**（`errors.py:145-153`，×3 agents）——其他用户错误均 subclass `ExecutionError`，唯独这个不。
9. **`_VarcharCodec`/`_CharCodec` 解码跳过 `_check`**（`type_system.py:302-308`，×2 agents）——DV7 编码端问题的对称解码漏洞；迁移/手工写入可绕过长度检查。

967/2 现有测试通过但**未覆盖这些崩溃路径**（crash-mid-commit、3+ 叶子 B+tree range scan、单表 overflow、close race、5+ MB SQL 文件、2^31 边界、DATABASELOCKED 触发路径、post-migration VARCHAR 列）。生产规模数据与跨平台行为均无验证。

## What Changes

新增 `tinydb-review-2026-07-28-fixes` capability：

1. **WAL 严格遵守 write-ahead** —— `commit()` 重排为 `wal_append_commit → wal_fsync → write_main_page × N → fsync_main`；`rollback()` 同样保留 WAL-first 顺序；`write_page()` 先 WAL 追加再 mutate state。
2. **B+tree 链表完整性** —— `_insert_into_parent` 在分裂后修补新右叶子的 `next_leaf_id`，保持链 `L1 → L2 → L3 → 0` 不变。
3. **catalog 溢出链健壮性** —— 移除 `_pack_chain` 的防御性截断；在 `_serialize_segments` 中实现真实的贪心分割，确保段 ≤ `CHAIN_BODY_SIZE`。
4. **`Database` 生命周期 hardening** —— `_is_closed` 检查移入 `with self._lock` 内；`__init__` 在 Pager 之后的步骤上加 try/except `self.close(); raise`。
5. **REPL `_cmd_read` 性能修复** —— 字符累积改 list-join 或 `text.find()` 流式扫描；.read 一个 5 MB SQL 文件应在 < 1 秒。
6. **`_IntCodec` 往返对称** —— 将界限检查抽到共享 `_bounds_check()` helper，`encode_py` 和 `decode_bytes` 双调用。
7. **`DatabaseLocked(ExecutionError)`** —— 改基类为 `ExecutionError`；同步更新 REPL `_run_sql` 的 `except` 链；CHANGELOG 标注非破坏性（父类变体）。
8. **`_VarcharCodec`/`_CharCodec` 解码校验** —— `decode_bytes` 调用 `_check(len(text))`；异常类型为 `CodecError`（非 `ValueError`）。

## Capabilities

### 影响到的现有 capabilities
- `execute` / DDL/DML：受影响 — items 1, 4, 6, 8 改变 execute/commit/close 的失败语义
- `storage` / WAL 协议：受影响 — item 1 改变主库写时机
- `index-manager` / BTree：受影响 — item 2 改变叶子分裂行为
- `catalog` / schema：受影响 — item 3 改变溢出链写入
- `type-system` / codecs：受影响 — items 6, 7 改变 encode/decode 行为
- `repl` / REPL：受影响 — items 5, 8 改变 `_cmd_read` 与异常分发

### 新增 capabilities
（无）

## Non-Goals

- **不**修复 MED/LOW 项（如 `pager.read_page` 缺边界检查、`_repl_io._is_unterminated` CC=27、`_filelock` Windows NameError、`VALID_OUTPUT_FORMATS` 三重定义、`global _state` 突变等）。这些留给后续 change。
- **不**重新设计 WAL 协议或迁移格式。修补保持二进制兼容。
- **不**为 `_cmd_read` 引入流式分词器（仍维持简单分号切分）。
- **不**触及测试基础设施（如 `tests/integration/_repl_fakes.py` 5× 复制）。
- **不**为 item 2 重写整个 B+tree；仅修补 leaf split 路径。
- **不**为 item 3 重写 catalog 序列化；仅补全 `_serialize_segments` 的段分割。

## Acceptance Scenarios

### Item 1: WAL ordering
- ✅ `commit()` 调用序列：先 `wal_append_commit` → `wal_fsync` → 多次 `write_main_page` → `fsync_main`
- ✅ `write_page()` 中 `wal_append_page` 失败时 `pending_writes` 不被 mutate
- ✅ `commit()` 中途异常时，状态置 `ROLLED_BACK`（或新增 `FAILED`），不留 `ACTIVE`
- ✅ 现有 967 测试通过；新增测试模拟 WAL 写失败 → commit 失败 → state 正确转换

### Item 2: B+tree leaf chain
- ✅ 随机插入 3000 行触发 3+ 叶子分裂；range scan 返回全部 3000 行
- ✅ 分裂后 right.next_leaf_id 等于原 rightmost page id
- ✅ 现有 engine-v2 测试 + index tests 全过

### Item 3: catalog overflow
- ✅ 单表 200+ 列时 `_pack_chain` 输出页数 ≥ ⌈payload / CHAIN_BODY_SIZE⌉
- ✅ `_serialize_segments` 中 `len(seg) > CHAIN_BODY_SIZE` 真正触发贪心分割
- ✅ 截断码路径（`body[:CHAIN_BODY_SIZE]`）被移除

### Item 4: Database lifecycle
- ✅ `_is_closed` 检查在 `with self._lock:` 块内
- ✅ `__init__` 中 Pager 之后的任何 raise 都触发 `self.pager.close()`
- ✅ `Database.open_corrupt_file()` 测试：构造一个坏文件，确认 close() 被调用且 OS 锁释放

### Item 5: `_cmd_read` perf
- ✅ 5 MB SQL 文件 `.read` 完成 < 1 秒
- ✅ 16 MB SQL 文件 `.read` 完成 < 5 秒（之前基本无法完成）
- ✅ 现有 `.read` 行为不变（小文件）

```

Full source: openspec/changes/tinydb-review-2026-07-28-fixes/proposal.md

## openspec/changes/tinydb-review-2026-07-28-fixes/design.md

- Source: openspec/changes/tinydb-review-2026-07-28-fixes/design.md
- Lines: 1-164
- SHA256: bc508be5ce92be537a467761d0f9c6cb814807b522dc55329a820a6e488de58e

[TRUNCATED]

```md
# Design: 修复 9 项 HIGH 问题（高层架构）

> 本文件给出 tinydb-review-2026-07-28-fixes 的高层架构决策。深度技术细化（API 形状、边界条件、测试策略）将在 Design Doc（design 阶段）中给出。

## 改动覆盖

```
                    ┌───────────────────────────────────┐
                    │        9 项 HIGH 修复              │
                    └────────────────┬──────────────────┘
                                     │
       ┌───────────────────┬─────────┴───────────┬───────────────────┐
       ▼                   ▼                     ▼                   ▼
 ┌─────────────┐    ┌─────────────┐      ┌─────────────┐    ┌─────────────┐
 │  Storage    │    │  Database   │      │  Codecs     │    │  REPL       │
 │ Items 1-3   │    │  Items 4, 6 │      │  Items 7, 9 │    │  Item 5     │
 └──────┬──────┘    └──────┬──────┘      └──────┬──────┘    └──────┬──────┘
        │                  │                    │                  │
        ▼                  ▼                    ▼                  ▼
   transaction.py     database.py        type_system.py      _repl_meta.py
   btree.py                                errors.py           errors.py
   catalog.py                                                   (DatabaseLocked)
   pager.py
```

## 单 change 内并行拆分（7 子任务）

为最大化 parallel 收益，将 9 项拆为 7 个子任务，使用 worktree + subagent-driven-development：

| Task | Items | 文件 | 依赖 | 风险 |
|------|-------|------|------|------|
| T1 WAL ordering | 1 | `transaction.py`, `pager.py` | 无 | HIGH |
| T2 B+tree leaf chain | 2 | `btree.py` | 无 | HIGH |
| T3 catalog overflow | 3 | `catalog.py` | 无 | MEDIUM |
| T4 DB lifecycle | 4, 6 | `database.py` | 无 | HIGH |
| T5 REPL _cmd_read perf | 5 | `_repl_meta.py` | 无 | LOW |
| T6 codec round-trip symmetry | 7, 9 | `type_system.py` | 无 | MEDIUM |
| T7 exception hierarchy | 8 | `errors.py` + call sites | 无 | LOW |

**并行配置**（同一 worktree 内 7 tasks 跑 3-4 round review-fix）：
- Round 1: T1, T2, T3, T5, T6, T7（6 并行）— 各自独立，文件不重叠
- Round 2: T4 — 仅它独立于其余，但因 T1 改 commit() 顺序，T4 与 T1 共享 `database.py`，故需 T1 完成后 T4 跑

实际简化：**全部 7 tasks 在不同文件，T4 与 T1 不在同一文件（事务提交 vs Database 生命周期），可全并行**。TDD + thorough review (≤2 rounds)。

## 各 Task 高层方案

### T1: WAL ordering (`transaction.py` + `pager.py`)

**当前（错误）顺序**：
```python
def commit(self):
    for pid, data in self.pending_writes.items():
        self._pager.write_main_page(pid, data)   # 主库先写
    self._pager.wal_append_commit(self.id)        # WAL commit 后追加
    self._pager.fsync_main()                       # 主库 fsync
    self._pager.wal_truncate_before(self.id)       # WAL 截断
```

**目标顺序**：
```python
def commit(self):
    # 1. WAL commit 先（保证恢复时能看到 COMMIT）
    self._pager.wal_append_commit(self.id)
    self._pager.fsync_wal()                       # WAL fsync 屏障
    # 2. 主库写
    for pid, data in self.pending_writes.items():
        self._pager.write_main_page(pid, data)
    # 3. 主库 fsync
    self._pager.fsync_main()
    # 4. WAL 截断（保留恢复历史）
    self._pager.wal_truncate_before(self.id + 1)
```

**边界**：
- `pager.py` 需要新增 `fsync_wal()` 方法
- `wal_truncate_before(self.id)` 旧语义保留；新代码用 `wal_truncate_before(self.id + 1)`
- `write_page()` 重排：`wal_append_page` 先，mutate `pending_writes` 后
- 中途异常 → state 转为 `ROLLED_BACK` 并 re-raise（不留下 ACTIVE）


```

Full source: openspec/changes/tinydb-review-2026-07-28-fixes/design.md

## openspec/changes/tinydb-review-2026-07-28-fixes/tasks.md

- Source: openspec/changes/tinydb-review-2026-07-28-fixes/tasks.md
- Lines: 1-71
- SHA256: 99abaecde43651cc242a156520ce5ba714d0744d9325ca04e5afba16c38628ea

```md
# Tasks: 修复 9 项 HIGH 问题

## 1. WAL 协议顺序（Item 1）

- [ ] 1.1 在 `pager.py` 新增 `Wal.fsync_wal()` 方法（封装 `os.fsync(self._file.fileno())`）
- [ ] 1.2 重排 `transaction.py:Transaction.commit()` 顺序：WAL append_commit → fsync_wal → write_main_page × N → fsync_main → wal_truncate_before(self.id+1)
- [ ] 1.3 重排 `transaction.py:Transaction.write_page()`：先 `wal_append_page`，成功后 mutate `pending_writes`
- [ ] 1.4 `transaction.py:Transaction.commit()` 中途异常时显式置 `_state = TxnState.ROLLED_BACK`（或新增 FAILED）后再 re-raise
- [ ] 1.5 新增 `tests/unit/transaction/test_wal_ordering.py`：模拟 WAL write 失败 → state 转换 + pending_writes 不残留
- [ ] 1.6 现有 `tests/unit/transaction/test_commit.py` 全过；967 测试 baseline 维持

## 2. B+tree 叶子链表完整性（Item 2）

- [ ] 2.1 在 `BTree.insert()` 中追踪 rightmost leaf pid（实例状态）
- [ ] 2.2 `BTree._split_leaf()` 时修补 right.next_leaf_id = original_leaf.next_leaf_id，original_leaf.next_leaf_id = right_pid
- [ ] 2.3 新增 `tests/unit/btree/test_leaf_chain.py`：随机插入 3000 行触发 3+ 叶子分裂，range scan 命中全部 3000 行（stress test）
- [ ] 2.4 新增 `tests/unit/btree/test_chain_after_split.py`：手动构造多叶子分裂 + 反向 range scan
- [ ] 2.5 现有 engine-v2 测试 + index tests 全过

## 3. catalog 溢出链健壮性（Item 3）

- [ ] 3.1 移除 `catalog.py:_pack_chain` 的 `body = seg[:CHAIN_BODY_SIZE]` 防御性截断
- [ ] 3.2 在 `catalog.py:_serialize_segments` 实现真实贪心分割：单段超 CHAIN_BODY_SIZE 时也分裂
- [ ] 3.3 `_pack_chain` 中加 `if len(seg) > CHAIN_BODY_SIZE: raise CatalogCorrupt(...)`（不再静默）
- [ ] 3.4 新增 `tests/unit/catalog/test_overflow_chain_robustness.py`：构造单表 200+ 列，验证 `_pack_chain` 输出页数 ≥ ⌈payload / CHAIN_BODY_SIZE⌉，且 round-trip 列完整
- [ ] 3.5 现有 catalog 测试全过

## 4. Database 生命周期 hardening（Items 4+6）

- [ ] 4.1 `database.py:Database.execute()` 的 `if self._is_closed` 检查移到 `with self._lock:` 块内
- [ ] 4.2 `database.py:Database.explain_plan()` 同样修复
- [ ] 4.3 `database.py:Database.__init__` 中 Pager 构造之后步骤包 `try/except: self.pager.close(); raise`
- [ ] 4.4 `database.py:Database.close()` 中：若 `self.pager.close()` 抛异常，log 错误但不置 `_is_closed`，允许重试
- [ ] 4.5 新增 `tests/unit/database/test_close_race.py`：线程 A/B 模拟 close-then-execute 竞争
- [ ] 4.6 新增 `tests/unit/database/test_init_cleanup_on_exception.py`：mock `Catalog.from_bytes` raise，确认 pager.close() 被调用，OS 锁释放
- [ ] 4.7 现有 database.py 测试全过；cc51c46 修复保留

## 5. REPL `_cmd_read` 性能修复（Item 5）

- [ ] 5.1 `_repl_meta.py:_cmd_read` 字符累积改 list-append + `''.join()`，或切换到 `text.find(';', start)` 流式扫描
- [ ] 5.2 新增 `tests/unit/repl/test_cmd_read_perf.py`：5 MB SQL 文件 `.read` 完成时间 < 1s；16 MB 完成 < 5s
- [ ] 5.3 现有 `.read` 测试全过（小文件功能保持）

## 6. codec 往返对称（Items 7+9）

- [ ] 6.1 `type_system.py:_IntCodec._check_bounds(value: int)` 抽为公共方法（统一 2^15/2^31/2^63 界限）
- [ ] 6.2 `_IntCodec.encode_py` 和 `_IntCodec.decode_bytes` 都调 `_check_bounds`
- [ ] 6.3 `_VarcharCodec.decode_bytes` / `_CharCodec.decode_bytes` 调用 `_check(len(text))`；异常类型 `CodecError`
- [ ] 6.4 新增 `tests/unit/test_type_system.py::test_int_codec_symmetric_bounds`：encode 和 decode 越界行为一致
- [ ] 6.5 新增 `tests/unit/test_type_system.py::test_varchar_decode_check`：长度为 1..max 的正常值通过；max+1 抛 `CodecError`
- [ ] 6.6 DV7 编码端问题不动（docstring 标注 do-not-fix）；仅补齐解码端

## 7. 异常层级清理（Item 8）

- [ ] 7.1 `errors.py:145-153` ——`class DatabaseLocked(ExecutionError):`（替代 `TinydbError`）
- [ ] 7.2 CHANGELOG.md 标注：non-breaking for `except TinydbError` callers
- [ ] 7.3 新增 `tests/unit/test_error_hierarchy.py`：所有用户错误都 subclass `TinydbError`；`DatabaseLocked` 是 `ExecutionError` 子类
- [ ] 7.4 REPL `_run_sql` 调用点验证 `except (TinydbError, ...)` 仍捕获 `DatabaseLocked`

## 8. 验证与归档

- [ ] 8.1 整树 `pytest --no-cov` 通过（967 baseline + 新增 tests）
- [ ] 8.2 覆盖率 ≥ 90%（不允许显著下降）
- [ ] 8.3 生成 `docs/superpowers/reports/2026-07-28-review-fixes-verify.md`
- [ ] 8.4 git merge `feature/20260728/review-fixes` → main (`--no-ff`)
- [ ] 8.5 archive change：`openspec archive tinydb-review-2026-07-28-fixes --yes`
- [ ] 8.6 清理 worktree：`git tag -a archive/review-fixes-feature-pre-archive <branch>` + `git worktree remove --force`

## 9. 已知偏差预留

> 实现阶段如有偏差，按 `comet-verify` 协议记录在 verify 报告中并在 `proposal.md` 的 Risk 表 / 后续 change 跟踪。

```
