"""Service-free structural validation of author-written target filters."""

from __future__ import annotations

from typing import NoReturn

import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pyproj
import shapely
from ibis.expr.operations import geospatial, logical, numeric, strings, temporal

# These Ibis 12 families contain row-wise builtins. Use exact classes, so custom
# subclasses cannot inherit permission. Reductions and impure nodes are excluded
# separately, including spatial reductions. Backend support is checked at binding.
_ROWWISE = frozenset(
    value
    for module in (geospatial, logical, numeric, strings, temporal)
    for value in vars(module).values()
    if isinstance(value, type)
    and issubclass(value, ops.Value)
    and value.__module__ == module.__name__
) | {
    ops.Alias,
    ops.Literal,
    ops.Field,
    ops.Cast,
    ops.TryCast,
    ops.IsNull,
    ops.NotNull,
    ops.NullIf,
    ops.Coalesce,
    ops.Greatest,
    ops.Least,
    ops.SimpleCase,
    ops.SearchedCase,
    ops.Pi,
    ops.E,
}


def validate_selector_expression(result: object, target: ir.Table) -> ir.Table:
    """Validate a filter tree and return the complete, unchanged author expression.

    The target must be the invocation's declaration-derived unbound relation.
    Relation identity is intentional: equal names and schemas confer no authority.
    Invalid shapes and detectable SRID mismatches raise ValueError.
    This boundary never compiles, executes, or acquires records. Call it after
    selector invocation and before managed binding.

    Returns:
        The original table, with every predicate and literal intact.

    Raises:
        TypeError: If the selector does not return an Ibis table.
    """
    if not isinstance(result, ir.Table):
        message = (
            "Target selector must return an Ibis Table, not a standalone predicate"
        )
        raise TypeError(message)
    root = target.op()
    relation = result.op()
    allowed_relations = {id(root)}
    while relation is not root:
        if type(relation) is not ops.Filter:
            _unsupported(relation)
        allowed_relations.add(id(relation))
        relation = relation.parent

    srids = {
        dtype.srid
        for dtype in target.schema().values()
        if isinstance(dtype, dt.GeoSpatial)
    }
    pending = [result.op()]
    visited: set[int] = set()
    while pending:
        node = pending.pop()
        if id(node) in visited:
            continue
        visited.add(id(node))
        if isinstance(node, ops.Relation):
            if id(node) not in allowed_relations:
                _unsupported(node)
        else:
            _validate_value(node, srids)
        pending.extend(node.__children__)
    return result


def _validate_value(node: ops.Node, srids: set[int | None]) -> None:
    if type(node) not in _ROWWISE or isinstance(node, ops.Reduction | ops.Impure):
        _unsupported(node)
    if isinstance(node, ops.GeoSetSRID | ops.GeoTransform):
        if not isinstance(node.srid, ops.Literal):
            _unsupported(node)
        _validate_srid(node.srid.value, srids)
    if isinstance(node, ops.GeoConvert):
        for crs in (node.source, node.target):
            declared_srid = pyproj.CRS(crs).to_epsg()
            if declared_srid is None:
                message = (
                    f"Selector geometry CRS {crs!r} does not match target SRID {srids}"
                )
                raise ValueError(message)
            _validate_srid(declared_srid, srids)
    if isinstance(node, ops.Value) and isinstance(node.dtype, dt.GeoSpatial):
        _validate_srid(node.dtype.srid, srids)
        if isinstance(node, ops.Literal):
            # Shapely geometries may carry EWKB SRID metadata independently of
            # the explicit Ibis dtype. Neither declaration may hide a conflict.
            _validate_srid(int(shapely.get_srid(node.value)), srids)


def _validate_srid(srid: int | None, srids: set[int | None]) -> None:
    if srid not in {None, 0} and srid not in srids:
        message = (
            f"Selector geometry SRID {srid} does not match target SRID {srids}; "
            "reproject source geometry before constructing the literal"
        )
        raise ValueError(message)


def _unsupported(node: ops.Node) -> NoReturn:
    message = (
        f"Unsupported selector operation {type(node).__name__}: return the exact "
        "supplied target table or filters on it, using deterministic row-wise "
        "predicates over target fields and literals; additional relations, "
        "subqueries, reductions, windows, and output transformations are not supported"
    )
    raise ValueError(message)
