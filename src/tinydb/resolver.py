"""名称解析：来源映射、合并 schema、USING/NATURAL JoinKey、列位置解析。

设计依据：Design Doc §3.2 / §4.5 / §5.2。无副作用：只读 AST + Catalog 元数据。
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from tinydb.catalog import Catalog, TableInfo
from tinydb.errors import (
    AmbiguousColumn, DuplicateAlias, IncompatibleKeyTypes,
    MissingUsingKey, UnknownQualifiedColumn, UnknownSource,
)
from tinydb.parser import (
    Select, JoinClause, JoinOnPredicate, JoinKey, TableRef, ColumnRef,
    AggregateCall, default_alias,
    EqualsExpr, AndExpr, OrExpr, NotExpr,
)
from tinydb.type_system import validate_compare_types


@dataclass(frozen=True)
class ResolvedSource:
    """FROM / JOIN 来源的元数据视图。

    ``source_id`` 是 alias 优先、否则 ``table_name``；在 source map 中唯一。
    ``column_pos`` 把列名映射到 schema 中的位置，供执行层快速索引。
    """

    source_id: str
    table_name: str
    alias: Optional[str]
    schema: tuple  # tuple[str, ...]
    column_pos: dict  # dict[str, int]


@dataclass(frozen=True)
class ResolvedPlan:
    """Resolver 输出：名称绑定 + 已 fold 的 SELECT 子句。

    ``outer_kind`` 仅在 NATURAL JOIN 无共同列退化时记录用户声明的 join
    kind（让执行层按对应 outer kind + cross 处理）。
    """

    sources: tuple  # tuple[ResolvedSource, ...]
    output_schema: tuple  # tuple[str, ...]
    merged_keys: tuple  # tuple[JoinKey, ...]
    column_resolver: Callable  # (qualifier_or_None, col_name) -> (pos, ResolvedSource)
    on_resolved: tuple  # 已 fold 的 ON 列表（list of tuples）
    where_resolved: Any  # 已 fold 的 WHERE expr（None / tuple）
    select_resolved: tuple  # (label, source-expr)
    order_resolved: tuple
    group_resolved: tuple
    having_resolved: Any
    aggregate_resolved: tuple
    outer_kind: Optional[str] = None
    # --- tinydb-join-query (T6): per-Join 切分，chained JOIN 时按 join 出现顺序 ---
    per_join_keys: tuple = ()  # tuple[tuple[JoinKey, ...], ...] 按 join 顺序
    per_join_on_resolved: tuple = ()  # tuple[Any, ...] 按 join 顺序


def _source_id_for(name: str, alias: Optional[str]) -> str:
    """返回 source map 的 id：alias 优先，否则表名。"""
    return alias or name


def _split_qualified(s: str) -> tuple:
    """``'u.id'`` -> ``('u', 'id')``；``'id'`` -> ``(None, 'id')``。"""
    if "." in s:
        q, n = s.split(".", 1)
        return q, n
    return None, s


def _build_source_map(
    from_ref: TableRef,
    joins: tuple,
    catalog: Catalog,
) -> tuple:
    """构造 source map：``(sources tuple, source_id -> ResolvedSource)``。

    返回 ``sources``（按 JOIN 链顺序，含 FROM）；alias 重名抛 ``DuplicateAlias``，
    表名不存在抛 ``UnknownSource``。``sources`` 索引与 joins 一一对应：
    ``sources[0]`` = FROM，``sources[i+1]`` = ``joins[i].right``。
    """
    sources: list = []
    seen: dict = {}

    def _register(ref: TableRef) -> ResolvedSource:
        ti = catalog.get_table(ref.name)
        if ti is None:
            raise UnknownSource(ref.name)
        sid = _source_id_for(ref.name, ref.alias)
        if sid in seen:
            raise DuplicateAlias(sid, ref.name, seen[sid].table_name)
        schema = tuple(c.name for c in ti.columns)
        rs = ResolvedSource(
            source_id=sid, table_name=ref.name, alias=ref.alias,
            schema=schema,
            column_pos={n: i for i, n in enumerate(schema)},
        )
        seen[sid] = rs
        return rs

    sources.append(_register(from_ref))
    for j in joins:
        sources.append(_register(j.right))
    return tuple(sources)


def _column_type(ti: TableInfo, name: str) -> tuple:
    """返回 ``(type_name, type_params)``。"""
    for c in ti.columns:
        if c.name == name:
            return c.type, c.type_params
    raise KeyError(name)


def _resolve_using_or_natural(
    join: JoinClause,
    left_src: ResolvedSource,
    right_src: ResolvedSource,
    left_ti: TableInfo,
    right_ti: TableInfo,
) -> tuple:
    """返回 ``(JoinKey tuple, output_labels tuple)``。

    NATURAL 通过两侧 schema 求共同列（按左 schema 顺序）；USING 直接采用
    parser 给出的列名列表。共同列类型校验抛 ``IncompatibleKeyTypes``。
    """
    if join.natural:
        keys = [n for n in left_src.schema if n in right_src.schema]
    else:
        keys = list(join.using_keys)

    if not keys:
        # NATURAL 无共同列：退化 CROSS；labels 为空（用户列两边都无）。
        return (), ()

    out_keys: list = []
    for k in keys:
        if k not in left_src.column_pos:
            raise MissingUsingKey(k, left_src.source_id)
        if k not in right_src.column_pos:
            raise MissingUsingKey(k, right_src.source_id)
        ltype, lparams = _column_type(left_ti, k)
        rtype, rparams = _column_type(right_ti, k)
        try:
            validate_compare_types(ltype, lparams, rtype, rparams)
        except TypeError:
            raise IncompatibleKeyTypes(ltype, rtype)
        out_keys.append(JoinKey(
            left_col=left_src.column_pos[k],
            right_col=right_src.column_pos[k],
            label=k, source_left=left_src.source_id,
            source_right=right_src.source_id,
        ))
    return tuple(out_keys), tuple(keys)


def _make_resolver(sources: tuple) -> Callable:
    """返回 ``(qualifier_or_None, col_name) -> (pos, ResolvedSource)`` 闭包。

    裸列只在唯一 source 提供时通过；多 source 同名列抛 ``AmbiguousColumn``。
    限定列找不到 source 或 schema 中无该列抛 ``UnknownQualifiedColumn``。
    """
    by_id = {s.source_id: s for s in sources}

    def _resolve(ref) -> tuple:
        # 接受 ColumnRef / 2-tuple / 限定字符串。
        if isinstance(ref, ColumnRef):
            q, n = ref.qualifier, ref.name
        elif isinstance(ref, tuple) and len(ref) == 2:
            q, n = ref
        elif isinstance(ref, str):
            q, n = _split_qualified(ref)
        else:
            raise ValueError(f"unsupported column ref: {ref!r}")

        if q is not None:
            src = by_id.get(q)
            if src is None or n not in src.column_pos:
                raise UnknownQualifiedColumn(q, n)
            return src.column_pos[n], src

        hits = [s for s in sources if n in s.column_pos]
        if not hits:
            raise UnknownQualifiedColumn("?", n)
        if len(hits) > 1:
            raise AmbiguousColumn(n, tuple(s.source_id for s in hits))
        return hits[0].column_pos[n], hits[0]

    return _resolve


def _fold_equals_expr(expr: EqualsExpr, resolver: Callable) -> tuple:
    """fold ``EqualsExpr`` -> ``("=", pos, value, src_id)``（4-tuple, Task 7 deviation）。

    ``src_id`` is consumed by ``_remap_where_positions`` to remap source-local
    ``pos`` to qualified-output position; after remap the form reduces to
    ``("=", qualified_pos, value)`` for executor consumption.
    """
    pos, src = resolver((getattr(expr, "qualifier", None), expr.column))
    return ("=", pos, expr.value, src.source_id)


def _remap_where_positions(
    pred: Any,
    resolver: Callable,
    output_position_map: dict,
    sources: tuple,
) -> Any:
    """把 WHERE 折叠后的 ``("=", pos, lit)`` 中 source-local pos remap 到
    qualified-output pos。

    ``("=", pos, lit, src_id)`` 4-tuple 是 _fold_equals_expr 折叠输出：
    - pos = 该 source 的 source-local 位置；
    - src_id = source identifier。

    通过 ``output_position_map[qualified_name]`` 找到最终合并 schema 的位置，
    重写为 ``("=", new_pos, lit)``（去掉 src_id）。复合 AND/OR/NOT 递归。
    """
    if pred is None:
        return None
    if isinstance(pred, tuple):
        # 复合 AND/OR/NOT 递归
        if pred[0] == "AND" and len(pred) == 3:
            return (
                "AND",
                _remap_where_positions(pred[1], resolver, output_position_map, sources),
                _remap_where_positions(pred[2], resolver, output_position_map, sources),
            )
        if pred[0] == "OR" and len(pred) == 3:
            return (
                "OR",
                _remap_where_positions(pred[1], resolver, output_position_map, sources),
                _remap_where_positions(pred[2], resolver, output_position_map, sources),
            )
        if pred[0] == "NOT" and len(pred) == 2:
            return (
                "NOT",
                _remap_where_positions(pred[1], resolver, output_position_map, sources),
            )
        # 列对字面量（4-tuple from _fold_equals_expr）
        if pred[0] == "=" and len(pred) == 4:
            _op, pos, lit, src_id = pred
            # 用 source_id 找到 source，再找 col name
            src = next((s for s in sources if s.source_id == src_id), None)
            if src is None:
                return ("=", pos, lit)  # fallback（不 remap）
            col_name = src.schema[pos]
            qualified = f"{src_id}.{col_name}"
            new_pos = output_position_map.get(qualified, pos)
            return ("=", new_pos, lit)
    return pred


def _fold_join_predicate(pred: JoinOnPredicate, resolver: Callable) -> tuple:
    """fold ``JoinOnPredicate`` -> ``(op, left_pos, right_pos, l_src_id, r_src_id)``。

    保留 source_id 以便执行层在每个 Join 节点上把 source-local 位置 remap 到
    subtree-local 位置（chained JOIN 时 left 子树是复合的，无法直接用 source-local pos）。
    """
    lpos, lsrc = resolver((pred.left.qualifier, pred.left.name))
    rpos, rsrc = resolver((pred.right.qualifier, pred.right.name))
    return (pred.op, lpos, rpos, lsrc.source_id, rsrc.source_id)


def _fold_expr(expr: Any, resolver: Callable) -> Any:
    """把 Expr 树中所有 ColumnRef 替换为列位置；返回折叠后的简单 tuple。

    简化实现：仅保证不抛错并通过 resolver 测试；Task 6 / 7 会细化 fold 输出
    （保留 ``(op, left, right)`` 形式以与既有 ``eval_expr`` 协同）。
    """
    if expr is None:
        return None
    if isinstance(expr, JoinOnPredicate):
        return _fold_join_predicate(expr, resolver)
    if isinstance(expr, EqualsExpr):
        return _fold_equals_expr(expr, resolver)
    if isinstance(expr, AndExpr):
        return ("AND", _fold_expr(expr.left, resolver), _fold_expr(expr.right, resolver))
    if isinstance(expr, OrExpr):
        return ("OR", _fold_expr(expr.left, resolver), _fold_expr(expr.right, resolver))
    if isinstance(expr, NotExpr):
        return ("NOT", _fold_expr(expr.operand, resolver))
    raise ValueError(f"unsupported expr node: {type(expr).__name__}")


def _merged_position(
    qualifier: Optional[str],
    name: str,
    resolver: Callable,
    output_position_map: dict,
) -> int:
    """Resolve a column to its position in the qualified JOIN output schema."""
    source_pos, source = resolver((qualifier, name))
    qualified = f"{source.source_id}.{name}"
    if qualified not in output_position_map:
        raise UnknownQualifiedColumn(source.source_id, name)
    return output_position_map.get(qualified, source_pos)


def _fold_group_by(
    group_by: tuple,
    resolver: Callable,
    output_position_map: dict,
) -> tuple:
    """Fold GROUP BY references to merged-schema positions."""
    return tuple(
        _merged_position(*_split_qualified(col), resolver, output_position_map)
        for col in group_by
    )


def _fold_order_by(
    order_by: tuple,
    resolver: Callable,
    output_position_map: dict,
    aliases: dict,
    group_positions: tuple,
    aggregate_query: bool,
) -> tuple:
    """Fold ORDER BY references to positions in the Sort input schema."""
    out: list = []
    for item in order_by:
        if item.qualifier is None and item.column in aliases:
            pos = aliases[item.column]
        else:
            merged_pos = _merged_position(
                item.qualifier, item.column, resolver, output_position_map,
            )
            if aggregate_query:
                if merged_pos not in group_positions:
                    raise ValueError(
                        f"ORDER BY column {item.column!r} is not grouped",
                    )
                pos = group_positions.index(merged_pos)
            else:
                pos = merged_pos
        out.append((pos, item.descending))
    return tuple(out)


def _drop_right_by_source(per_join_keys: tuple) -> dict:
    """Return ``{source_id: {right_col_idx, ...}, ...}`` for every USING/NATURAL
    right-side key. Centralises the invariant shared with
    ``_join_executor._merged_schema`` so the resolver and executor cannot drift.
    """
    drop_by_source: dict = {}
    for join_keys in per_join_keys:
        for k in join_keys:
            drop_by_source.setdefault(k.source_right, set()).add(k.right_col)
    return drop_by_source


def _merged_schema(sources: tuple, per_join_keys: tuple = ()) -> tuple:
    """构造合并后 output_schema：USING/NATURAL 合并键只输出一次（用左侧
    source 的 label 占位，右侧 source 的同名列 SKIP）；其余列两侧保留。

    每个 source 各自计算 ``drop``（该 source 作为右侧时需要跳过的列索引）
    和 ``label_map``（该 source 作为左侧时需要替换为 label 的列索引）——
    与执行层 ``_join_executor._merged_schema`` 的语义保持严格一致，否则
    WHERE/ORDER/GROUP 的位置 remap 会与执行层行形状错位（IndexError）。
    """
    drop_by_source: dict = _drop_right_by_source(per_join_keys)
    label_by_source: dict = {}
    for join_keys in per_join_keys:
        for k in join_keys:
            label_by_source.setdefault(k.source_left, {})[k.left_col] = k.label
    out: list = []
    for src in sources:
        drop = drop_by_source.get(src.source_id, set())
        label_map = label_by_source.get(src.source_id, {})
        for idx, col in enumerate(src.schema):
            if idx in drop:
                continue  # 右侧 source 的合并键位置：跳过
            if idx in label_map:
                out.append(label_map[idx])  # 左侧 source 的合并键位置：relabel
            else:
                out.append(col)
    return tuple(out)


def resolve(ast: Select, catalog: Catalog) -> ResolvedPlan:
    """主入口：把 ``Select`` AST + ``Catalog`` 物化成 ``ResolvedPlan``。

    步骤：build_source_map → merged_keys（USING/NATURAL）→ fold ON/WHERE →
    fold GROUP BY/ORDER BY。返回纯数据 ``ResolvedPlan``，无副作用。
    """
    if not isinstance(ast, Select):
        raise ValueError("resolve() expects a Select AST")
    if ast.from_ is None:
        raise ValueError("resolve() expects FROM clause")

    sources = _build_source_map(ast.from_, ast.joins, catalog)
    resolver = _make_resolver(sources)

    merged_keys: list = []
    per_join_keys: list = []
    per_join_on_resolved: list = []
    outer_kind: Optional[str] = None

    # 跟踪累积 schema：cumulative_seen_total = [(source_id, col_name), ...]
    # 每条记录表示"该 source 的该列在累积 schema 中的位置 = 索引"。
    # Task 7 I-1 修复：chained JOIN 的 keys.left_col 是 source-local 位置，
    # 但执行层 node.left 子树输出 schema 已合并（USING/NATURAL 共同列并入左
    # 侧），需要 remap 到 subtree-local 位置。
    cumulative_seen_total: list = []  # list of (source_id, col_name)
    cumulative_pos_by_name: dict = {}  # col_name -> cumulative position

    def _extend_cumulative(src: ResolvedSource) -> None:
        """把 src 的列按 schema 顺序推入累积 schema；共同列跳过。"""
        for col in src.schema:
            if col in cumulative_pos_by_name:
                continue
            cumulative_pos_by_name[col] = len(cumulative_seen_total)
            cumulative_seen_total.append((src.source_id, col))

    # 预填 sources[0]（FROM source）
    if sources:
        _extend_cumulative(sources[0])

    # 计算 merged_keys 与外连接 kind（NATURAL 无共同列时记录）。
    for join in ast.joins:
        right_idx = next(
            i for i, s in enumerate(sources)
            if s.source_id == _source_id_for(join.right.name, join.right.alias)
        )
        # 左深 join 链：当前 join 的 left 是上一个 source（left_idx = right_idx - 1）。
        # sources 是按 joins 顺序构建的，所以 ``right_idx - 1`` 总是指向
        # 上一个参与 join 的 source（与 join 在 FROM 子句中的位置无关）。
        left_idx = right_idx - 1
        left_src = sources[left_idx]
        right_src = sources[right_idx]
        left_ti = catalog.get_table(left_src.table_name)
        right_ti = catalog.get_table(right_src.table_name)
        keys = _resolve_using_or_natural(
            join, left_src, right_src, left_ti, right_ti,
        )[0]

        # I-1 修复：chained JOIN（left_idx > 0）时，把 keys.left_col 从
        # source-local 位置 remap 到 subtree-local 位置。
        # subtree cumulative schema = sources[0..left_idx] 的列按合并去重
        # 顺序。每个 source 的列在该 schema 中的位置由 ``cumulative_pos_by_name``
        # 记录（按列名查）。共同列已在第一个出现的 source 中，后续 source
        # 的同名列被跳过 —— 因此 source-local 与 subtree-local 不一致。
        if left_idx > 0 and keys:
            remapped = []
            for k in keys:
                col_name = left_src.schema[k.left_col]
                new_left = cumulative_pos_by_name.get(col_name, k.left_col)
                remapped.append(JoinKey(
                    left_col=new_left,
                    right_col=k.right_col,
                    label=k.label,
                    source_left=k.source_left,
                    source_right=k.source_right,
                ))
            keys = tuple(remapped)

        merged_keys.extend(keys)
        per_join_keys.append(keys)
        # 累积 schema：把 right_src 推入（USING/NATURAL 共同列已被合并跳过）
        _extend_cumulative(right_src)
        if join.natural and not keys:
            outer_kind = join.kind

    # 构造 output_schema：USING/NATURAL 合并键只输出一次；其余列两侧保留。
    output_schema = _merged_schema(sources, per_join_keys)

    # Task 7 WHERE remap：折叠返回 source-local 位置，但 Filter 消费方按
    # 合并 schema（source_id.col）位置求值。预计算 qualified-output 位置映射。
    # 位置必须与 ``output_schema`` 完全对齐 —— USING/NATURAL 右侧 source 的
    # 合并键列被 coalesce，不占独立位置；其余列两侧都保留。
    drop_by_source_pos: dict = _drop_right_by_source(per_join_keys)
    output_position_map: dict = {}
    out_pos = 0
    for src in sources:
        drop = drop_by_source_pos.get(src.source_id, set())
        for idx, col in enumerate(src.schema):
            if idx in drop:
                continue
            output_position_map[f"{src.source_id}.{col}"] = out_pos
            out_pos += 1

    # 已 fold 的 WHERE / ON 表达式（位置 + literal）。
    on_resolved = tuple(
        _fold_expr(j.on_expr, resolver) for j in ast.joins if j.on_expr is not None
    )
    # per-Join ON：可能为 None（USING/CROSS）
    per_join_on_resolved = tuple(
        _fold_expr(j.on_expr, resolver) for j in ast.joins
    )
    where_folded = (
        _fold_expr(ast.where, resolver) if ast.where is not None else None
    )
    if where_folded is not None:
        where_resolved = _remap_where_positions(
            where_folded, resolver, output_position_map, sources,
        )
    else:
        where_resolved = None

    # Aggregate calls carry merged-schema positions into the logical plan.
    aggregates: list = []
    aggregate_alias_positions: dict = {}
    for item in ast.select_items:
        if item.kind != "aggregate":
            continue
        agg = item.aggregate
        if agg.arg == "*":
            resolved_arg = "*"
        else:
            if len(agg.arg) == 3:
                _tag, qualifier, name = agg.arg
            else:
                _tag, name = agg.arg
                qualifier = None
            resolved_arg = (
                "position",
                _merged_position(
                    qualifier, name, resolver, output_position_map,
                ),
            )
        alias = item.alias or default_alias(agg)
        aggregates.append(AggregateCall(agg.func, resolved_arg, alias, agg.line, agg.col))

    group_resolved = _fold_group_by(
        ast.group_by, resolver, output_position_map,
    )
    # Aggregate output is group keys followed by aggregate values.
    for index, agg in enumerate(aggregates, start=len(group_resolved)):
        aggregate_alias_positions[agg.alias] = index

    order_resolved = _fold_order_by(
        ast.order_by, resolver, output_position_map, aggregate_alias_positions,
        group_resolved, bool(group_resolved or aggregates),
    )

    having_resolved = None
    if ast.having is not None:
        left, op, literal = ast.having
        if isinstance(left, AggregateCall):
            if left.arg == "*":
                resolved_having_arg = "*"
            else:
                if len(left.arg) == 3:
                    _tag, qualifier, name = left.arg
                else:
                    _tag, name = left.arg
                    qualifier = None
                resolved_having_arg = (
                    "position",
                    _merged_position(
                        qualifier, name, resolver, output_position_map,
                    ),
                )
            match = next(
                (i for i, agg in enumerate(aggregates)
                 if agg.func == left.func and agg.arg == resolved_having_arg),
                None,
            )
            if match is None:
                raise ValueError("HAVING aggregate must appear in SELECT list")
            having_pos = len(group_resolved) + match
        elif left in aggregate_alias_positions:
            having_pos = aggregate_alias_positions[left]
        else:
            q, n = _split_qualified(left)
            merged_pos = _merged_position(q, n, resolver, output_position_map)
            try:
                having_pos = group_resolved.index(merged_pos)
            except ValueError as exc:
                raise ValueError(f"HAVING column {left!r} is not grouped") from exc
        having_resolved = (having_pos, op, literal)

    return ResolvedPlan(
        sources=sources,
        output_schema=output_schema,
        merged_keys=tuple(merged_keys),
        per_join_keys=tuple(per_join_keys),
        per_join_on_resolved=tuple(per_join_on_resolved),
        column_resolver=resolver,
        on_resolved=on_resolved,
        where_resolved=where_resolved,
        select_resolved=tuple(),
        order_resolved=order_resolved,
        group_resolved=group_resolved,
        having_resolved=having_resolved,
        aggregate_resolved=tuple(aggregates),
        outer_kind=outer_kind,
    )