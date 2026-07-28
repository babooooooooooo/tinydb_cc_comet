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

### T2: B+tree leaf chain (`btree.py`)

**当前（损坏）逻辑** —— 右叶子 `next_leaf_id=0` 不修补：
```python
right = LeafNode(..., next_leaf_id=0, ...)
right_pid = self.pager.alloc_page()
left.next_leaf_id = right_pid            # left 修补
self.pager.write_page(leaf_pid, left.serialize())
self.pager.write_page(right_pid, right.serialize())  # right 仍为 0
```

**目标**：在 `_insert_into_parent` 中追踪 "rightmost leaf" 状态；分裂前保存 `previous_rightmost_pid = self._rightmost_leaf_pid`，分裂后 `right.next_leaf_id = previous_rightmost_pid`，更新 `self._rightmost_leaf_pid = right_pid`。

**Or 简化**：分裂时若 `original_leaf.next_leaf_id != 0`，则 `right.next_leaf_id = original_leaf.next_leaf_id`；改 `original_leaf.next_leaf_id = right_pid`。

### T3: catalog overflow (`catalog.py`)

**当前**：`_pack_chain` 截断 + `_serialize_segments` 的 `> CHAIN_BODY_SIZE` 防御性截断（`> 1` 守卫）

**目标**：
1. 移除 `_pack_chain` 的 `body = seg[:CHAIN_BODY_SIZE]`（上游真的分割后再走）
2. 在 `_serialize_segments` 中实现真实贪心分割：单表超过阈值时也分割
3. `_pack_chain` 中 `if len(seg) > CHAIN_BODY_SIZE: raise CatalogCorrupt(...)`（不再静默）

### T4: Database lifecycle (`database.py`)

1. `_is_closed` 检查移到 `with self._lock:` 内
2. `__init__` 中 Pager 之后步骤包 `try/except: self.close(); raise`
3. `close()` 中若 `Pager.close()` 抛异常，记录到 log 但不置 `_is_closed`，允许重试

### T5: `_cmd_read` perf (`_repl_meta.py`)

**当前**：
```python
buf = ""
for char in text:
    buf += char                         # O(n²)
    if char == ";" and not _is_unterminated(buf):
        _run_sql(db, buf.strip(), state)
        buf = ""
```

**目标**：
```python
buf_parts: list[str] = []
for char in text:
    buf_parts.append(char)
    if char == ";" and not _is_unterminated(buf := "".join(buf_parts)):
        _run_sql(db, buf.strip(), state)
        buf_parts = []
```

或更简洁：基于 `text.find(';', start)` 的流式扫描，跳过 `_is_unterminated` 内的分号。新增性能 test：`5 MB < 1s`。

### T6: codec round-trip symmetry (`type_system.py`)

1. 抽 `_IntCodec._check_bounds(value: int) -> None` 公共方法
2. `encode_py` 和 `decode_bytes` 都调它
3. `_VarcharCodec.decode_bytes` 和 `_CharCodec.decode_bytes` 调用 `_check(len(text))`；异常类型 `CodecError`
4. 新增 codec 单元测试 `test_int_varchar_symmetry`

### T7: exception hierarchy (`errors.py` + 调用点)

1. `errors.py: DatabaseLocked(ExecutionError)` （替代 `TinydbError`）
2. REPL `_run_sql` 的 except 链不变（TinydbError 是 ExecutionError 的父类）
3. CHANGELOG 标注
4. 新增 `test_error_hierarchy.py` 验证每个用户错误的基类

## 验证策略

每个 task 完成后：
1. 子任务自己的单元测试通过
2. 整树 `pytest --no-cov` 通过（967/2 baseline + 新增测试）
3. 覆盖率 ≥ 90%（项目历史最高 92.59%，不允许显著下降）
4. 验证报告包含：
   - 已修复 vs. 重现的 failing scenario 截图
   - diff stat
   - 已知 deviations（如有）

## Reference

- 完整 review 报告：`docs/superpowers/reports/2026-07-28-code-review.md`
- 已 committed 修复（工作树）：`executor.py / _join_executor.py / _repl_meta.py / resolver.py`
- Subagent 调度规则：参考 `~/.claude/comet/reference/subagent-dispatch.md`
