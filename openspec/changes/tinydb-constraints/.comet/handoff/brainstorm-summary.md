# Brainstorm Summary

- Change: tinydb-constraints
- Date: 2026-07-17
- 状态：brainstorming 进行中，以下未明确标注“已确认”的内容均为候选。

## 已确认的目标与边界

- 支持列约束 `NOT NULL`、`UNIQUE`、`PRIMARY KEY`。
- 约束在 `INSERT` 执行路径、写入每一行之前校验。
- 不实现 `CHECK`、`FOREIGN KEY`、`DEFAULT`。
- 不实现索引化 UNIQUE；当前接受全表线性扫描，索引留给 `tinydb-engine-v2`。
- 不承诺事务性；事务与原子性增强留给 `tinydb-acid`。
- 不覆盖 UPDATE 约束校验。
- D1（已确认）：parser 保留 `PRIMARY KEY` 独立标记，不自动改写为 `NOT NULL + UNIQUE`。
- D2（已确认）：`ConstraintViolation` 继承 `ExecutionError`。
- D3（已确认）：旧 catalog 的二元列 schema 反序列化为 `nullable=True`、`unique=False`、`primary_key=False`。
- D4（已确认）：UNIQUE 校验使用 tuple key 与内建 `set`，不实现自定义 hash 或索引。
- D5（已确认）：PRIMARY KEY 的等价语义在 executor 合并；NULL 走 `kind="null"`，重复键走 PK 重复错误。

## 当前代码事实

- `parser.CreateTable.columns` 当前是 `list[tuple[str, str]]`，没有独立列定义 AST。
- tokenizer 当前没有 `NOT`、`NULL`、`UNIQUE`、`PRIMARY`、`KEY` 关键字，也没有 NULL token 类型。
- `parser.Insert.values` 直接保存 Python 标量；当前 `_LITERAL_TYPES` 不包含 NULL。
- `Catalog.TableInfo.schema` 当前为 `list[tuple[str, str]]`；catalog JSON 中列是 `[name, type]` 二元数组。
- row codec 已有 null bitmap，能够机械编码/解码 `None`，不需改变行页格式。
- executor 当前忽略 `Insert.columns`，按 schema 顺序 zip；本 change 必须先完成列名解析、重复/未知列检查与 schema 顺序归一化，才能正确约束和落盘。
- executor 已有 `_scan_table()`，可复用于 O(n) UNIQUE 校验。
- `py_to_db(None, type)` 会报类型错误，因此校验顺序必须在类型校验前分流 NULL。
- REPL 当前统一输出 `ERROR: <异常类名>: <str>`；若要求精确输出 `ERROR: ConstraintViolation(...)`，需要显式格式化该子异常。
- 当前 change 没有 `openspec/changes/tinydb-constraints/specs/*/spec.md`；canonical OpenSpec 只有 proposal/design/tasks，缺少正式 delta 验收场景。

## 第二轮协调裁决

- nullable `UNIQUE` 采用方案 A：按 SQL 主流语义，唯一键 tuple 中任一成员为 `NULL` 时不加入 UNIQUE seen-set，也不产生冲突；因此多个含 NULL 的 UNIQUE tuple 可以并存。
- PRIMARY KEY 与上述规则正交：PK tuple 任一成员为 `NULL` 时先抛 `ConstraintViolation(kind="null")`，永远不会进入重复键阶段。
- 该裁决已由协调者下发；最终整体技术方案仍须经过 Comet design 用户确认门。

## 候选技术方案

### 方案 A：分层列模型 + executor 统一归一化（推荐）

- parser 新增 frozen `ColumnDefinition` AST，字段为 `name/type/nullable/unique/primary_key`。
- catalog 新增 frozen `Column` 持久模型；executor 在 CREATE 时显式映射 AST → catalog model，避免 parser 依赖 storage。
- `TableInfo.columns` 保存 `tuple[Column, ...]`，并提供只读 `schema` 投影视图供 row codec、SELECT、DELETE、Database.Row 与 REPL 兼容。
- INSERT 先把显式列清单映射为完整 schema 顺序的 row；省略列填 `None`，然后执行列/数量、NULL、类型、UNIQUE/PK、编码、落盘。
- 每个唯一组只扫描表一次并构造 `set[tuple]`，同时将当前多行 INSERT 已通过的 key 加入 set，从而发现同语句内重复。

优点：模块边界清楚、兼容现有 row codec、后续 engine-v2 可替换唯一键查询实现。缺点：AST 与 catalog 有两个结构，需要一个小型映射函数。

### 方案 B：parser 直接产出 catalog.Column

- `CreateTable.columns` 直接使用 catalog `Column`。
- executor CREATE 直接传入 catalog。

优点：代码最少。缺点：parser 与 catalog/storage 强耦合，AST 单测必须引入持久层类型，后续 catalog 演进会污染语法层。

### 方案 C：保持 tuple AST，追加 flags tuple/dict

- 继续使用 tuple，扩展为五元 tuple 或嵌套 dict。

优点：表面改动小。缺点：位置语义脆弱、错误易发生、类型与可读性最差，不适合实现级设计，不推荐。

## 候选校验顺序

1. 表存在。
2. INSERT 列清单非空、无重复、全部存在。
3. 每行值数与显式列数一致（parser 已检查，executor 防御性复查）。
4. 将每行归一化为完整 schema 顺序；省略列为 `None`。
5. NOT NULL / PK 非空校验。
6. 对非 NULL 值运行既有类型校验。
7. 构造 UNIQUE 与 PK tuple key；用已有行集合和本批次已通过集合检测重复。
8. 编码并落盘。

多行 INSERT 不承诺事务原子性；设计必须精确说明是逐行校验/写入，还是先约束预检整批再写入。当前候选为逐行校验并在每行写入前完成全部约束检查，符合“失败行不写入”，但先前行可能保留。

## 第二轮待确认问题（批量）

1. **分层模型**：采用方案 A（推荐），parser 使用 frozen `ColumnDefinition`，catalog 使用 frozen `Column`，executor 显式映射；还是方案 B 让 parser 直接依赖 `catalog.Column`？
2. **裸 `NULL` 列子句**：仅支持范围内的 `NOT NULL`，拒绝冗余 `CREATE TABLE t(x INT NULL)`（推荐，YAGNI）；还是兼容高层 design 示例接受裸 `NULL`？无论选择哪项，未标注列默认 `nullable=True`。
3. **多行 INSERT 约束失败语义**：逐行校验并落盘，后续行失败时保留先前成功行（推荐，与“无事务”边界及现有 executor 一致）；还是先对整批做约束预检后再逐行落盘？两者都不承诺存储故障原子性。
4. **错误 kind 与重叠优先级**：固定为 proposal 的 `null | unique | duplicate_pk`（推荐），PK 与显式 UNIQUE 生成完全相同列组时只保留 PK 组并报 `duplicate_pk`；还是统一重复错误为 `duplicate_key`？
5. **INSERT 列归一化**：保持显式列清单必填；executor 拒绝未知/重复列，省略列填 `None` 后走约束与 row codec（推荐）；是否需要改成必须提供所有表列？
6. **catalog 兼容形状**：新列编码为带命名字段的 JSON object，旧 `[name, type]` 数组按 D3 升级；`TableInfo.columns: tuple[Column, ...]` 并保留只读 `schema` 投影供 row codec 和现有 API（推荐）；还是继续持久化位置数组？
7. **REPL 精确输出**：为 `ConstraintViolation` 增加专门渲染，输出 `ERROR: ConstraintViolation(kind=..., column[s]=..., value=...)`（推荐，符合 proposal），而不是沿用当前通用的 `ERROR: ConstraintViolation: ...`；是否确认？
8. **Spec Gap 处理**：遵守本轮写入边界，不创建 delta spec，只在 Design Doc 的 `## Spec Gap` 记录缺少正式验收场景（推荐）；还是允许补最小 delta spec？

## 关键取舍与风险

- catalog JSON 逻辑 schema 会升级，但物理 page/row 格式不变；必须用元素形状识别旧 `[name, type]` 与新对象格式。
- `nullable=True` 的旧 catalog 兼容决策会改变过去“NULL 无法解析”形成的隐式行为；这是用户明确指定的 D3，应以回归 fixture 固化。
- 所有标记为 UNIQUE 的列组成一个复合 UNIQUE 组、所有 PK 列组成一个复合 PK 组，这是上游非标准但已声明的能力语义；不得误实现为每列单独唯一。
- O(n) 扫描若按每行/每组重复执行会退化为不必要的 O(batch × groups × rows) I/O；应按组建立一次 set。
- 当前 catalog 和 `TableInfo` 是可变对象；只冻结 `Column`，不在本 change 做无关的全量不可变重构。

## 测试策略（候选）

- tokenizer/parser 单元：关键字大小写、约束任意顺序、重复子句、截断 `NOT`/`PRIMARY`、NULL literal 的合法/非法上下文。
- AST 单元：D1 独立标志、组合约束、复合组标记。
- catalog 单元/集成：新格式 round-trip、旧二元 schema fixture、混乱 JSON 拒绝、page overflow。
- executor 单元/集成：校验顺序、未知/重复列、省略列、单列/复合 UNIQUE、复合 PK、已有表冲突、同批冲突、tombstone 忽略、溢出行扫描。
- 错误契约：`ConstraintViolation` 属性、继承关系、确定性 `str/repr`、REPL 单行输出。
- 回归：现有测试全过；行格式字节不变；无约束表行为保持。
- 性能：1000 行数据上的单次 UNIQUE 插入或批量构建 set 计时，避免把环境敏感的整批 O(n²) 写入硬编码为 100ms。

## Spec Patch

候选：当前 change 没有 delta spec，按 Comet 规则应补充最小验收场景；但用户明确限制本协调阶段只能写 Design Doc 与 Comet 状态文件，暂不回写，等待确认。
