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

### Item 6: `_IntCodec` symmetry
- ✅ `INSERT INT 2^31` → `encode_py` 抛 `CodecError`
- ✅ `decode_bytes(b"\x80\x00\x00\x00", 0)` 也抛 `CodecError`（或返回 + raise on next encode）
- ✅ codec 单元测试新增 `test_int_codec_symmetric_bounds`

### Item 7: `DatabaseLocked` hierarchy
- ✅ `class DatabaseLocked(ExecutionError)` 而非 `TinydbError`
- ✅ `from tinydb.errors import *` 仍包含 `DatabaseLocked`
- ✅ REPL `_run_sql` `except (TinydbError, ...)` 捕获路径保持现有行为
- ✅ CHANGELOG 标注：non-breaking for callers catching `TinydbError`

### Item 8: VARCHAR/CHAR decode check
- ✅ `decode_bytes` 调用 `_check(len(text))`
- ✅ 长度溢出抛 `CodecError`（非 `ValueError`）
- ✅ DV7 编码端不动；只补齐解码端

## Risk

| Risk | Severity | Mitigation |
|------|----------|------------|
| Item 1 (WAL reorder) 影响所有 commit 路径 | HIGH | 充分单元测试 + 保留原有 `pending_writes` 字段语义；新 WAL 顺序与现有 recovery path 匹配 |
| Item 2 (B+tree chain) 影响索引写入 | HIGH | 复用现有 leaf 节点结构；新单元测试 + 随机 stress test（3000 行） |
| Item 3 (catalog truncation) 影响所有超宽表 | MEDIUM | 同时修改 `_serialize_segments`，确保截断路径不再触发 |
| Item 6 (`_IntCodec` boundary) 可能拒绝历史 row | MEDIUM | 历史数据本来就不应越界；若越界则升级为明确错误更安全 |
| Item 8 (`DatabaseLocked` parent) 改变基类 | LOW | `TinydbError` 仍为父链上的祖先，`except TinydbError` 不变 |

## Dependencies

- 工作树基于 `main` @ `08a9ca5`（含 uncommitted 修复 — 评审中已 APPROVED）
- 不阻塞其他 change；与未提交修复相互独立
- 不引入新外部依赖

## Reference

- 完整 review 报告：`docs/superpowers/reports/2026-07-28-code-review.md`
- 分项 report：`docs/superpowers/reports/2026-07-28-code-review-{storage,codecs,database,repl}.md`
- 验证报告：`docs/superpowers/reports/2026-07-28-code-review-uncommitted-verification.md`
