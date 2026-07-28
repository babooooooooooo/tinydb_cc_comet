"""JOIN 执行：nested-loop INNER / CROSS / LEFT / RIGHT / FULL（Task 6 + Task 7）。

设计依据：Design Doc §3.2 / §4.1 / §4.2 / §4.4 / §5.3。事务读路由由 executor 负责，
本模块仅消费 plan 树并在必要时调 ``executor._scan_table``。

ON 谓词的位置语义：resolver 折叠后的 ``on_expr`` 是 ``(op, lpos, rpos, l_src_id,
r_src_id)``，其中 ``lpos`` / ``rpos`` 是 source-local 位置（在该 source 的 schema
中）。chained JOIN 时 left 子树是复合的，因此执行层需把 source-local 位置 remap
到 subtree-local 位置 —— 实现见 :meth:`_remap_position`。

Task 7 增量：
- LEFT/RIGHT/FULL nested-loop；RIGHT 直接实现（保留 left-first 列序）；
- USING/NATURAL Coalesce（left 非 NULL 优先）；
- 复合 ON 谓词由 ``_eval_fold_expr`` 递归处理；
- chained JOIN keys remap（resolver 在 I-1 修复中保证 ``keys.left_col``
  是 subtree-local 位置）。
"""
from typing import Any

from tinydb.catalog import TableInfo
from tinydb.executor import _COMPARE_OPS as _OPERATIONS
from tinydb.plan import (
    LogicalPlan, Scan, Join, Filter, Aggregate, Sort, Project, Limit,
)


# 共享的 6 运算符比较表，被 _compare（HAVING）和 _eval_fold_expr（ON 5-tuple）复用。
# ON 单字符 tokenizer 当前只产出 '='/'<'/'>'，但表里多键允许 _parse_join_predicate
# 以后支持 '!='/'<='/'>=' 而无须再次触碰 _eval_fold_expr。
# ``_OPERATIONS`` is imported from ``tinydb.executor`` so the join executor
# and the single-table executor share one source of truth for op dispatch.


# 行类型：list[Any]；schema 类型：list[str]
Row = list
Schema = list


class JoinExecutor:
    """对 plan 中 Join 节点执行 nested-loop；返回 ``(rows, output_schema)``。

    本类在调用方 ``executor._scan_table(ti)`` 处消费行，因此 ACID 读路径仍走
    ``executor._txn_read_page``。
    """

    def __init__(self, executor) -> None:
        self.executor = executor

    # --- public ------------------------------------------------------------

    def execute_plan(self, plan: LogicalPlan) -> tuple[list, list]:
        """走 plan 树直到顶层 Project；返回 ``(rows, output_schema)``。"""
        rows, schema = self._eval(plan)
        return rows, schema

    # --- dispatch ----------------------------------------------------------

    def _eval(self, node: LogicalPlan) -> tuple[list, list]:
        if isinstance(node, Scan):
            return self._eval_scan(node)
        if isinstance(node, Join):
            return self._eval_join(node)
        if isinstance(node, Filter):
            return self._eval_filter(node)
        if isinstance(node, Project):
            return self._eval_project(node)
        if isinstance(node, Limit):
            return self._eval_limit(node)
        if isinstance(node, Sort):
            return self._eval_sort(node)
        if isinstance(node, Aggregate):
            return self._eval_aggregate(node)
        raise ValueError(f"unsupported plan node: {type(node).__name__}")

    # --- Scan --------------------------------------------------------------

    def _eval_scan(self, node: Scan) -> tuple[list, list]:
        ti: TableInfo = self.executor.catalog.get_table(node.table)
        rows: list = []
        for _sid, vals, _pid in self.executor._scan_table(ti):
            rows.append(list(vals))
        # 把列名加 source_id 前缀，使 Join 输出 schema 不会有同名列歧义。
        schema = [f"{node.source_id}.{c}" for c in node.schema]
        return rows, schema

    # --- Join --------------------------------------------------------------

    def _eval_join(self, node: Join) -> tuple[list, list]:
        left_rows, left_schema = self._eval(node.left)
        right_rows, right_schema = self._eval(node.right)

        if node.kind == "CROSS":
            return self._nested_loop_cross(
                left_rows, right_rows, left_schema, right_schema,
            )

        if node.kind == "INNER":
            return self._nested_loop_inner(
                left_rows, left_schema, right_rows, right_schema, node,
            )

        if node.kind == "LEFT":
            return self._nested_loop_left(
                left_rows, left_schema, right_rows, right_schema, node,
            )

        if node.kind == "RIGHT":
            # RIGHT JOIN 直接实现：保留 left-first 列序（与 LEFT JOIN 对称）。
            # 对每个 right_row 扫描 left_rows 匹配；若无匹配追加 NULL-padded
            # 行（与 _nested_loop_left 顺序对称：外层=left/右部未匹配 vs
            # 外层=right/左部未匹配）。
            return self._nested_loop_right(
                left_rows, left_schema, right_rows, right_schema, node,
            )

        if node.kind == "FULL":
            return self._nested_loop_full(
                left_rows, left_schema, right_rows, right_schema, node,
            )

        raise ValueError(f"unsupported join kind: {node.kind!r}")

    def _collect_sources(self, node: LogicalPlan) -> list[tuple[str, tuple]]:
        """递归收集子树下所有 Scan 节点的 ``(source_id, schema)``，按构造顺序。

        Scan 顺序与 left-deep Join 构造顺序一致，因此 ``source_id`` 在子树
        schema 中的位置 = 前面所有 source 的 schema 长度累加。
        """
        if isinstance(node, Scan):
            return [(node.source_id, node.schema)]
        if isinstance(node, Join):
            return self._collect_sources(node.left) + self._collect_sources(node.right)
        # Filter / Project / Sort / Limit / Aggregate 不改变列布局。
        if hasattr(node, "source") and isinstance(node.source, LogicalPlan):
            return self._collect_sources(node.source)
        raise ValueError(f"cannot collect sources from {type(node).__name__}")

    def _build_source_position_map(
        self,
        subtree_node: LogicalPlan,
        subtree_schema: Schema,
    ) -> dict[str, tuple[int, int]]:
        """返回 ``source_id -> (offset_in_subtree_schema, source_schema_length)``。

        ``offset`` 是该 source 的第一列在 ``subtree_schema`` 中的位置；source
        的 source-local 位置 ``p`` → subtree-local 位置 ``offset + p``。

        USING/NATURAL 合并键的去重已经反映在 ``_coalesce_row`` 的输出布局里：
        合并键只在 left_schema 出现一次，right 的合并键位置被跳过。因此当一个
        source 整体是 USING 合并键右侧源时，它的全部列可能不出现在
        ``subtree_schema`` —— 但 ON 谓词不能引用 USING 合并键（应走 keys 路径），
        实际不会发生，所以简化处理为 offset = 子树前累加。
        """
        sources = self._collect_sources(subtree_node)
        mapping: dict[str, tuple[int, int]] = {}
        offset = 0
        for sid, sch in sources:
            mapping[sid] = (offset, len(sch))
            offset += len(sch)
        return mapping

    def _qualify_schema(
        self,
        schema: Schema,
        node: LogicalPlan,
    ) -> Schema:
        """对 Join 子树的 schema 加 source_id 前缀，使同名列可以区分。

        例如 ``users u JOIN orders o ON u.id = o.user_id`` 的合并 schema 由
        ``('id', 'name', 'id', 'user_id', 'total')`` 变为
        ``('u.id', 'u.name', 'o.id', 'o.user_id', 'o.total')``。USING/NATURAL
        合并键仍走 ``_merged_schema`` 去重，不进入此路径。
        """
        sources = self._collect_sources(node)
        out: list = []
        for sid, sch in sources:
            for col in sch:
                out.append(f"{sid}.{col}")
        return out

    def _merged_schema(
        self,
        left_schema: Schema,
        right_schema: Schema,
        node: Join,
    ) -> Schema:
        """输出 schema：USING/NATURAL 合并键只出现一次（用 merge label）；其余列保持原名。

        子树 schema 已经按 source_id 加前缀（如 ``'u.id'`` / ``'o.id'``），因此
        同名列可以区分。USING merge 把两侧的合并键列统一为单个 ``k.label``（如
        ``'id'``）；其余列按左在前、右在后的顺序输出。
        """
        out: list = []
        # 处理 left 中的列：合并键保留为 label，非合并键保留 qualified 名
        merge_left_idx = {k.left_col: k.label for k in node.keys}
        for i, col in enumerate(left_schema):
            if i in merge_left_idx:
                out.append(merge_left_idx[i])
            else:
                out.append(col)
        # 处理 right 中的列：合并键位置跳过
        merge_right_idx = {k.right_col for k in node.keys}
        for i, col in enumerate(right_schema):
            if i in merge_right_idx:
                continue
            out.append(col)
        return out

    def _coalesce_row(
        self,
        left_row: Row,
        right_row: Row,
        node: Join,
        left_schema: Schema,
        right_schema: Schema,
    ) -> Row:
        """构造输出行：USING 合并键 Coalesce（left 非 NULL 优先；left 为 NULL 时用 right）。

        输出布局：left_row 在前，right_row 中非合并键位置追加在尾；合并键（按
        ``k.right_col``）跳过。
        """
        out = list(left_row)
        right_to_left = {k.right_col: k.left_col for k in node.keys}
        for ri, _col in enumerate(right_schema):
            if ri in right_to_left:
                # 合并键：Coalesce — left 为 None 时取 right。
                li = right_to_left[ri]
                if out[li] is None and right_row[ri] is not None:
                    out[li] = right_row[ri]
                continue
            out.append(right_row[ri])
        return out

    def _nested_loop_cross(
        self,
        left_rows: list,
        right_rows: list,
        left_schema: Schema,
        right_schema: Schema,
    ) -> tuple[list, list]:
        out_rows: list = []
        for lr in left_rows:
            for rr in right_rows:
                out_rows.append(list(lr) + list(rr))
        return out_rows, list(left_schema) + list(right_schema)

    def _nested_loop_inner(
        self,
        left_rows: list,
        left_schema: Schema,
        right_rows: list,
        right_schema: Schema,
        node: Join,
    ) -> tuple[list, list]:
        out_rows: list = []
        out_schema = self._merged_schema(left_schema, right_schema, node)
        # 构造 source_id → subtree-local offset 映射，用于 ON 谓词 remap。
        left_pos_map = self._build_source_position_map(node.left, left_schema)
        right_pos_map = self._build_source_position_map(node.right, right_schema)
        for lr in left_rows:
            for rr in right_rows:
                if self._matches(lr, rr, left_schema, right_schema, node,
                                  left_pos_map, right_pos_map):
                    out_rows.append(
                        self._coalesce_row(lr, rr, node, left_schema, right_schema),
                    )
        return out_rows, out_schema

    def _nested_loop_left(
        self,
        left_rows: list,
        left_schema: Schema,
        right_rows: list,
        right_schema: Schema,
        node: Join,
    ) -> tuple[list, list]:
        """LEFT JOIN nested-loop。

        顺序：strict-left-deep-insertion —— 对每个 left_row，按右表扫描顺序遍历
        匹配；若无匹配，立即追加一行（左部原值 + 右部 NULL）。
        """
        out_rows: list = []
        out_schema = self._merged_schema(left_schema, right_schema, node)
        left_pos_map = self._build_source_position_map(node.left, left_schema)
        right_pos_map = self._build_source_position_map(node.right, right_schema)
        for lr in left_rows:
            matched = False
            for rr in right_rows:
                if self._matches(lr, rr, left_schema, right_schema, node,
                                  left_pos_map, right_pos_map):
                    out_rows.append(
                        self._coalesce_row(lr, rr, node, left_schema, right_schema),
                    )
                    matched = True
            if not matched:
                out_rows.append(
                    self._null_pad_right(lr, right_schema, node),
                )
        return out_rows, out_schema

    def _nested_loop_right(
        self,
        left_rows: list,
        left_schema: Schema,
        right_rows: list,
        right_schema: Schema,
        node: Join,
    ) -> tuple[list, list]:
        """RIGHT JOIN nested-loop。

        顺序：strict-right-deep-insertion —— 对每个 right_row，按左表扫描顺序遍
        历匹配；若无匹配，立即追加一行（左部 NULL + 右部原值）。
        列布局保持与 LEFT 一致：``_merged_schema(left_schema, right_schema)``。
        """
        out_rows: list = []
        out_schema = self._merged_schema(left_schema, right_schema, node)
        left_pos_map = self._build_source_position_map(node.left, left_schema)
        right_pos_map = self._build_source_position_map(node.right, right_schema)
        for rr in right_rows:
            matched = False
            for lr in left_rows:
                if self._matches(lr, rr, left_schema, right_schema, node,
                                  left_pos_map, right_pos_map):
                    out_rows.append(
                        self._coalesce_row(lr, rr, node, left_schema, right_schema),
                    )
                    matched = True
            if not matched:
                out_rows.append(
                    self._null_pad_left(rr, left_schema, right_schema, node),
                )
        return out_rows, out_schema

    def _nested_loop_full(
        self,
        left_rows: list,
        left_schema: Schema,
        right_rows: list,
        right_schema: Schema,
        node: Join,
    ) -> tuple[list, list]:
        """FULL JOIN = LEFT 规则（匹配 + 左未匹配）+ 追加右未匹配（按右扫描顺序）。"""
        out_rows: list = []
        out_schema = self._merged_schema(left_schema, right_schema, node)
        left_pos_map = self._build_source_position_map(node.left, left_schema)
        right_pos_map = self._build_source_position_map(node.right, right_schema)
        # 第一阶段：复用 LEFT 规则，但额外记录所有匹配过的右行索引。
        right_matched_idx: set = set()
        for lr in left_rows:
            matched = False
            for ri, rr in enumerate(right_rows):
                if self._matches(lr, rr, left_schema, right_schema, node,
                                  left_pos_map, right_pos_map):
                    out_rows.append(
                        self._coalesce_row(lr, rr, node, left_schema, right_schema),
                    )
                    right_matched_idx.add(ri)
                    matched = True
            if not matched:
                out_rows.append(
                    self._null_pad_right(lr, right_schema, node),
                )
        # 第二阶段：追加右未匹配行（按右扫描顺序）。
        for ri, rr in enumerate(right_rows):
            if ri not in right_matched_idx:
                out_rows.append(
                    self._null_pad_left(rr, left_schema, right_schema, node),
                )
        return out_rows, out_schema

    def _null_pad_right(
        self,
        left_row: Row,
        right_schema: Schema,
        node: Join,
    ) -> Row:
        """构造一行：左部原值 + 右部 NULL。

        USING/NATURAL 合并键：左部位置保留 left 值（通常为非 NULL —— 否则不
        会是"未匹配"），右部合并键位置跳过。
        ON-based join：右部全部位置填 None。
        """
        if node.keys:
            out = list(left_row)
            merge_right_idx = {k.right_col for k in node.keys}
            for ri, _col in enumerate(right_schema):
                if ri in merge_right_idx:
                    continue
                out.append(None)
            return out
        # ON-based
        return list(left_row) + [None] * len(right_schema)

    def _null_pad_left(
        self,
        right_row: Row,
        left_schema: Schema,
        right_schema: Schema,
        node: Join,
    ) -> Row:
        """构造一行：左部 NULL + 右部原值（USING 合并键回填 right 值）。

        与 ``_coalesce_row`` 对称：USING/NATURAL 合并键位置把 right_row[ri]
        写到 left_schema[li]（因为 left 是 None），其余右部列追加。
        ON-based join：左部全部填 None，右部原值追加。
        """
        if node.keys:
            out = [None] * len(left_schema)
            right_to_left = {k.right_col: k.left_col for k in node.keys}
            for ri, _col in enumerate(right_schema):
                if ri in right_to_left:
                    li = right_to_left[ri]
                    out[li] = right_row[ri]
                    continue
                out.append(right_row[ri])
            return out
        # ON-based
        return [None] * len(left_schema) + list(right_row)

    def _matches(
        self,
        lr: Row,
        rr: Row,
        ls: Schema,
        rs: Schema,
        node: Join,
        left_pos_map: dict,
        right_pos_map: dict,
    ) -> bool:
        """判断左右行是否匹配。优先 USING/NATURAL 合并键；否则用 ON 表达式。"""
        if node.keys:
            for k in node.keys:
                lv = lr[k.left_col]
                rv = rr[k.right_col]
                if lv is None or rv is None:
                    return False
                if lv != rv:
                    return False
            return True
        if node.on_expr:
            for pred in node.on_expr:
                # ON 谓词 = 5-tuple (op, lpos, rpos, l_src_id, r_src_id)；
                # 复合 (AND/OR/NOT) 由 _eval_fold_expr 递归处理（Task 7）。
                if not self._eval_fold_expr(
                    pred, lr, rr, ls, rs, left_pos_map, right_pos_map,
                ):
                    return False
            return True
        return True

    def _eval_fold_expr(
        self,
        pred: Any,
        lr: Row,
        rr: Row,
        ls: Schema,
        rs: Schema,
        left_pos_map: dict = None,
        right_pos_map: dict = None,
    ) -> bool:
        """统一 ON / WHERE 折叠谓词求值（Task 7 §7.5 合并）。

        pred 形态：
        - ``("AND", a, b)`` / ``("OR", a, b)`` / ``("NOT", a)`` 复合
        - 5-tuple ``(op, lpos, rpos, l_src_id, r_src_id)``：ON 双行
        - 3-tuple ``("=", pos, value)``：列对字面量（WHERE）

        ``rr is None`` 时表示纯 row 谓词（WHERE）；否则 ON 双行谓词。
        """
        if pred is None:
            return True
        if not isinstance(pred, tuple):
            return True
        # 复合 AND/OR/NOT 递归
        if pred[0] == "AND" and len(pred) == 3:
            return self._eval_fold_expr(
                pred[1], lr, rr, ls, rs, left_pos_map, right_pos_map,
            ) and self._eval_fold_expr(
                pred[2], lr, rr, ls, rs, left_pos_map, right_pos_map,
            )
        if pred[0] == "OR" and len(pred) == 3:
            return self._eval_fold_expr(
                pred[1], lr, rr, ls, rs, left_pos_map, right_pos_map,
            ) or self._eval_fold_expr(
                pred[2], lr, rr, ls, rs, left_pos_map, right_pos_map,
            )
        if pred[0] == "NOT" and len(pred) == 2:
            return not self._eval_fold_expr(
                pred[1], lr, rr, ls, rs, left_pos_map, right_pos_map,
            )
        # ON 双行谓词（5-tuple）
        if rr is not None and len(pred) == 5 and pred[0] in _OPERATIONS:
            op, lpos, rpos, l_src_id, r_src_id = pred
            li = self._remap_position(
                l_src_id, lpos, left_pos_map or {}, ls,
            )
            ri = self._remap_position(
                r_src_id, rpos, right_pos_map or {}, rs,
            )
            return _OPERATIONS[op](lr[li], rr[ri])
        # WHERE 列对字面量（3-tuple, Task 7 C6 修正后）
        if len(pred) == 3 and pred[0] == "=":
            return lr[pred[1]] == pred[2]
        return True

    def _remap_position(
        self,
        source_id: str,
        source_pos: int,
        pos_map: dict,
        subtree_schema: Schema,
    ) -> int:
        """source-local 位置 → subtree-local 位置。

        ``pos_map`` 是当前子树（left 或 right）的 source_id → (offset, len)
        映射。subtree-local = offset + source_pos。
        """
        if source_id not in pos_map:
            raise KeyError(
                f"source_id {source_id!r} not found in subtree sources "
                f"(known: {sorted(pos_map)})",
            )
        offset, _length = pos_map[source_id]
        return offset + source_pos

    # --- Filter / Project / Limit / Sort / Aggregate -----------------------

    def _eval_filter(self, node: Filter) -> tuple[list, list]:
        rows, schema = self._eval(node.source)
        pred = node.predicate
        if pred is None:
            return rows, schema
        out: list = []
        for r in rows:
            # WHERE 谓词：rr=None 表示纯 row 谓词（Task 7 §7.5 合并）。
            if self._eval_fold_expr(
                pred, r, None, schema, schema, None, None,
            ):
                out.append(r)
        return out, schema

    def _eval_predicate(self, row: Row, pred: Any, schema: Schema) -> bool:
        """折叠后谓词求值（WHERE 形式，由 _eval_fold_expr 转发，保留兼容）。"""
        return self._eval_fold_expr(
            pred, row, None, schema, schema, None, None,
        )

    def _eval_project(self, node: Project) -> tuple[list, list]:
        rows, schema = self._eval(node.source)
        if node.star:
            return rows, schema
        out_rows: list = []
        new_labels = [label for label, _expr in node.items]
        for r in rows:
            projected = []
            for _label, expr in node.items:
                value = self._project_expr(expr, r, schema)
                projected.append(value)
            out_rows.append(projected)
        return out_rows, new_labels

    def _project_expr(self, expr: Any, row: Row, schema: Schema) -> Any:
        """Evaluate a column projection against the current schema."""
        if expr == "star":
            raise ValueError("star projection must use Project.star passthrough")
        if isinstance(expr, tuple) and len(expr) == 3 and expr[0] == "col":
            _tag, qualifier, name = expr
            pos = self._resolve_col(name, qualifier, schema)
            return row[pos]
        if isinstance(expr, tuple) and len(expr) == 2 and expr[0] == "agg":
            agg = expr[1]
            label = agg.alias or self._default_alias(agg)
            if label not in schema:
                raise ValueError(f"aggregate {label!r} not in schema {schema!r}")
            return row[schema.index(label)]
        raise ValueError(f"unsupported project expression: {expr!r}")

    def _resolve_col(self, name: str, qualifier: Any, schema: Schema) -> int:
        """Resolve qualified columns exactly and bare columns by unique suffix."""
        if qualifier is not None:
            qualified = f"{qualifier}.{name}"
            if qualified in schema:
                return schema.index(qualified)
        if name in schema:
            return schema.index(name)
        suffix_hits = [i for i, label in enumerate(schema) if label.endswith(f".{name}")]
        if len(suffix_hits) == 1:
            return suffix_hits[0]
        raise ValueError(f"column {name!r} not in schema {schema!r}")

    def _eval_limit(self, node: Limit) -> tuple[list, list]:
        rows, schema = self._eval(node.source)
        if node.offset:
            rows = rows[node.offset:]
        if node.limit is not None:
            rows = rows[: node.limit]
        return rows, schema

    def _eval_sort(self, node: Sort) -> tuple[list, list]:
        rows, schema = self._eval(node.source)
        keys = node.keys
        for idx, desc in reversed(keys):
            rows.sort(
                key=lambda r: (r[idx] is None, r[idx]),
                reverse=bool(desc),
            )
        return rows, schema

    def _eval_aggregate(self, node: Aggregate) -> tuple[list, list]:
        """Group merged JOIN rows, compute aggregates, then apply HAVING."""
        rows, schema = self._eval(node.source)
        if node.group_keys:
            out_rows, out_schema = self._aggregate_grouped(
                rows, schema, node.group_keys, node.aggregates,
            )
        else:
            out_rows, out_schema = self._aggregate_single(
                rows, schema, node.aggregates,
            )
        if node.having is not None:
            pos, op, literal = node.having
            out_rows = [r for r in out_rows if self._compare(r[pos], op, literal)]
        return out_rows, out_schema

    def _aggregate_single(
        self, rows: list, schema: Schema, aggregates: tuple,
    ) -> tuple[list, list]:
        values = [self._compute_aggregate(a, rows, schema) for a in aggregates]
        labels = [a.alias or self._default_alias(a) for a in aggregates]
        return [values], labels

    def _aggregate_grouped(
        self, rows: list, schema: Schema, group_keys: tuple, aggregates: tuple,
    ) -> tuple[list, list]:
        groups: dict = {}
        for row in rows:
            key = tuple(row[pos] for pos in group_keys)
            groups.setdefault(key, []).append(row)
        out_rows = [
            list(key) + [self._compute_aggregate(a, group, schema) for a in aggregates]
            for key, group in groups.items()
        ]
        labels = [schema[pos] for pos in group_keys]
        labels.extend(a.alias or self._default_alias(a) for a in aggregates)
        return out_rows, labels

    def _compute_aggregate(self, agg: Any, rows: list, schema: Schema) -> Any:
        if agg.arg == "*":
            values = rows
        elif isinstance(agg.arg, tuple) and agg.arg[0] == "position":
            values = [row[agg.arg[1]] for row in rows]
        else:
            raise NotImplementedError(
                f"aggregate argument {agg.arg!r} lacks merged-schema position",
            )
        non_null = [value for value in values if value is not None]
        if agg.func == "COUNT":
            return len(values) if agg.arg == "*" else len(non_null)
        if agg.func == "SUM":
            return sum(non_null) if non_null else None
        if agg.func == "AVG":
            return sum(non_null) / len(non_null) if non_null else None
        if agg.func == "MIN":
            return min(non_null) if non_null else None
        if agg.func == "MAX":
            return max(non_null) if non_null else None
        raise ValueError(f"unsupported aggregate: {agg.func!r}")

    @staticmethod
    def _default_alias(agg: Any) -> str:
        if agg.arg == "*":
            return "count"
        return agg.func.lower()

    @staticmethod
    def _compare(value: Any, op: str, literal: Any) -> bool:
        if op not in _OPERATIONS:
            raise ValueError(f"unsupported HAVING operator: {op!r}")
        return _OPERATIONS[op](value, literal)