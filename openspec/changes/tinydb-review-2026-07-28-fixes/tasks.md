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
