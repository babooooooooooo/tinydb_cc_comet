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
    """fold ``EqualsExpr`` -> ``(op, left_pos, value)``。"""
    pos, _ = resolver((getattr(expr, "qualifier", None), expr.column))
    return (expr.column, "=", pos, expr.value)


def _fold_join_predicate(pred: JoinOnPredicate, resolver: Callable) -> tuple:
    """fold ``JoinOnPredicate`` -> ``(op, left_pos, right_pos_or_lit)``。"""
    lpos, _ = resolver((pred.left.qualifier, pred.left.name))
    rpos, _ = resolver((pred.right.qualifier, pred.right.name))
    return (pred.op, lpos, rpos)


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


def _fold_group_by(group_by: tuple, resolver: Callable) -> tuple:
    """fold GROUP BY ``("u.id", ...)`` -> ``(pos, label)`` 元组列表。"""
    out: list = []
    for col in group_by:
        q, n = _split_qualified(col)
        pos, src = resolver((q, n))
        out.append((pos, f"{src.source_id}.{n}" if q else n))
    return tuple(out)


def _fold_order_by(order_by: tuple, resolver: Callable) -> tuple:
    """fold ORDER BY ``OrderByItem`` 列表 -> ``(pos, descending)`` 元组列表。"""
    out: list = []
    for item in order_by:
        pos, src = resolver((item.qualifier, item.column))
        out.append((pos, item.descending, f"{src.source_id}.{item.column}" if item.qualifier else item.column))
    return tuple(out)


def _merged_schema(sources: tuple) -> tuple:
    """构造合并后 output_schema：每个 source 顺序贡献列，USING/NATURAL
    共同列仅在首次出现的 source 中输出。"""
    seen: set = set()
    out: list = []
    for src in sources:
        for col in src.schema:
            if col in seen:
                continue
            out.append(col)
            seen.add(col)
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
    outer_kind: Optional[str] = None

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
        merged_keys.extend(keys)
        if join.natural and not keys:
            outer_kind = join.kind

    # 构造 output_schema：source schema 顺序去重（共同列已并入第一个 source）。
    output_schema = _merged_schema(sources)

    # 已 fold 的 WHERE / ON 表达式（位置 + literal）。
    on_resolved = tuple(
        _fold_expr(j.on_expr, resolver) for j in ast.joins if j.on_expr is not None
    )
    where_resolved = (
        _fold_expr(ast.where, resolver) if ast.where is not None else None
    )

    return ResolvedPlan(
        sources=sources,
        output_schema=output_schema,
        merged_keys=tuple(merged_keys),
        column_resolver=resolver,
        on_resolved=on_resolved,
        where_resolved=where_resolved,
        select_resolved=tuple(),
        order_resolved=_fold_order_by(ast.order_by, resolver),
        group_resolved=_fold_group_by(ast.group_by, resolver),
        having_resolved=None,
        aggregate_resolved=tuple(),
        outer_kind=outer_kind,
    )