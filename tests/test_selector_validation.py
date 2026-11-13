from collections.abc import Callable
from unittest.mock import Mock

import geopandas as gpd
import ibis
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.types as ir
import pytest
import shapely
from shapely.geometry import Point

from lyra.runtime.selector_validation import validate_selector_expression
from lyra.sdk.query_schema import compile_query_schema
from tests.provider_schema_helpers import declaration


@pytest.fixture
def target() -> ir.Table:
    return compile_query_schema(declaration()).target_relation()


@pytest.mark.parametrize(
    "select",
    [
        lambda t: t,
        lambda t: t.filter(ibis.literal(value=True)),
        lambda t: t.filter(ibis.literal(value=False)),
        lambda t: t.filter(t["count"].isnull()),
        lambda t: t.filter(t["count"].notnull()),
        lambda t: t.filter(t.location_id.isin(["a", "b"])),
        lambda t: t.filter(t.location_id.notin(["a"])),
        lambda t: t.filter(
            (t["count"] > 2) & (~(t["count"] < 5) | t["count"].isnull())
        ),
        lambda t: t.filter(t["count"] > 2).filter(lambda x: x["count"] < 8),
        lambda t: t.filter((t["count"] + 1).between(3, 10)),
        lambda t: t.filter(t.location_id.lower().startswith("a")),
        lambda t: t.filter(t["count"].fill_null(0) == 0),
    ],
)
def test_supported_filters_preserve_expression(
    target: ir.Table, select: Callable[[ir.Table], ir.Table]
) -> None:
    expression = select(target)
    assert validate_selector_expression(expression, target) is expression
    assert expression.schema() == target.schema()


def test_combined_spatial_filters_preserve_coordinates_and_srid(
    target: ir.Table,
) -> None:
    literal = ibis.literal(Point(1, 2), type=dt.Point(srid=4326))
    expression = target.filter(
        target["count"] > 5,
        target.geometry.intersects(literal)
        | target.geometry.d_within(literal, distance=10),
    ).filter(ibis._.location_id != "excluded")
    original = expression.op()
    assert validate_selector_expression(expression, target) is expression
    assert expression.op() is original
    literal_op = literal.op()
    assert isinstance(literal_op, ops.Literal)
    assert literal_op.value.equals(Point(1, 2))
    dtype = literal.type()
    assert isinstance(dtype, dt.GeoSpatial)
    assert dtype.srid == 4326


@pytest.mark.parametrize(
    "select",
    [
        lambda t: t.select("location_id"),
        lambda t: t.select(*t.columns),
        lambda t: t.mutate(count=t["count"] + 1),
        lambda t: t.union(t),
        lambda t: t.order_by("count"),
        lambda t: t.limit(3),
        lambda t: t.limit(3, offset=1),
        lambda t: t.sample(0.5),
        lambda t: t.distinct(),
        lambda t: t.aggregate(total=t["count"].sum()),
        lambda t: t.filter(t["count"] > t["count"].mean()),
        lambda t: t.filter(t["count"] > t["count"].mean().over()),
        lambda t: t.filter(ibis.random() > ibis.literal(0.5)),
        lambda t: t.filter(ibis.now() > ibis.timestamp("2020-01-01")),
        lambda t: t.filter(ibis.uuid() == "constant"),
        lambda t: t.filter(t["count"] > ibis.param(dt.int64)),
        lambda t: t.join(t.view(), "location_id"),
        lambda t: t.filter(t["count"].isin(t.filter(t["count"] > 5)["count"])),
    ],
)
def test_unsupported_shapes(
    target: ir.Table, select: Callable[[ir.Table], ir.Table]
) -> None:
    expression = select(target)
    with pytest.raises(ValueError, match="Unsupported selector operation"):
        validate_selector_expression(expression, target)


@pytest.mark.parametrize("hidden", [False, True])
def test_matching_relation_does_not_authorize_another_target(
    target: ir.Table, *, hidden: bool
) -> None:
    other = ibis.table(target.schema(), name=target.op().name)
    assert other.op() == target.op()
    assert other.op() is not target.op()
    expression = (
        target.filter(target["count"].isin(other["count"])) if hidden else other
    )
    with pytest.raises(ValueError, match="Unsupported selector operation"):
        validate_selector_expression(expression, target)


def test_predicate_cannot_reference_another_relation(target: ir.Table) -> None:
    other = ibis.table(target.schema(), name="other")
    expression = target.filter(target["count"].isin(other["count"]))
    with pytest.raises(ValueError, match="Unsupported selector operation"):
        validate_selector_expression(expression, target)


def test_udf_is_rejected(target: ir.Table) -> None:
    @ibis.udf.scalar.builtin
    def custom(value: int) -> int:
        return value

    with pytest.raises(ValueError, match="custom"):
        validate_selector_expression(
            target.filter(custom(target["count"]) > ibis.literal(0)), target
        )


@pytest.mark.parametrize("value", [None, True, "table"])
def test_table_result_required(target: ir.Table, value: object) -> None:
    with pytest.raises(TypeError, match="Ibis Table"):
        validate_selector_expression(value, target)
    with pytest.raises(TypeError, match="Ibis Table"):
        validate_selector_expression(target["count"] > 0, target)


@pytest.mark.parametrize("wrap", [False, True])
def test_mismatched_srid_is_rejected(target: ir.Table, *, wrap: bool) -> None:
    literal = ibis.literal(Point(1, 2), type=dt.Point(srid=3857))
    assert isinstance(literal, ir.GeoSpatialScalar)
    geometry = literal.buffer(1) if wrap else literal
    expression = target.filter(target.geometry.intersects(geometry))
    with pytest.raises(ValueError, match=r"SRID 3857.*target SRID"):
        validate_selector_expression(expression, target)


def test_explicit_source_reprojection_before_literal(target: ir.Table) -> None:
    source = gpd.GeoSeries([Point(1000, 2000)], crs=3857)
    projected = source.to_crs(4326).iloc[0]
    literal = ibis.literal(projected, type=dt.Point(srid=4326))
    expression = target.filter(target.geometry.d_within(literal, distance=0.1))
    assert validate_selector_expression(expression, target) is expression
    literal_op = literal.op()
    assert isinstance(literal_op, ops.Literal)
    assert literal_op.value.equals(projected)
    assert source.iloc[0].equals(Point(1000, 2000))
    assert source.crs is not None
    assert source.crs.to_epsg() == 3857


def test_srid_assignment_cannot_hide_mismatch(target: ir.Table) -> None:
    literal = ibis.literal(Point(1, 2), type=dt.Point(srid=3857))
    assert isinstance(literal, ir.GeoSpatialScalar)
    srid = ibis.literal(4326)
    assert isinstance(srid, ir.IntegerScalar)
    literal = literal.set_srid(srid)
    with pytest.raises(ValueError, match=r"SRID 3857.*target SRID"):
        validate_selector_expression(
            target.filter(target.geometry.intersects(literal)), target
        )


def test_validation_does_not_materialize(
    target: ir.Table, monkeypatch: pytest.MonkeyPatch
) -> None:
    forbidden = Mock(side_effect=AssertionError("materialization is forbidden"))
    for method in ("execute", "to_pandas", "to_pyarrow", "compile"):
        monkeypatch.setattr(ir.Table, method, forbidden)
    expression = target.filter(target["count"] > 0)
    assert validate_selector_expression(expression, target) is expression
    forbidden.assert_not_called()
    assert isinstance(target.op(), ops.UnboundTable)


def test_raw_sql_rejected(target: ir.Table, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = Mock(**{"_get_sql_string_view_schema.return_value": target.schema()})
    monkeypatch.setattr(ir.Table, "_find_backend", Mock(return_value=backend))
    expression = target.sql("SELECT * FROM target")
    with pytest.raises(ValueError, match="SQLStringView"):
        validate_selector_expression(expression, target)


@pytest.mark.parametrize("srid", [None, 4326])
def test_embedded_geometry_srid_conflict(target: ir.Table, srid: int | None) -> None:
    geometry = shapely.set_srid(Point(1, 2), 3857)
    literal = ibis.literal(geometry, type=dt.Point(srid=srid))
    with pytest.raises(ValueError, match=r"SRID 3857.*target SRID"):
        validate_selector_expression(
            target.filter(target.geometry.intersects(literal)), target
        )


@pytest.mark.parametrize("method", ["set_srid", "transform"])
def test_explicit_geometry_operation_cannot_change_target_crs(
    target: ir.Table, method: str
) -> None:
    geometry = getattr(target.geometry, method)(ibis.literal(3857))
    with pytest.raises(ValueError, match=r"SRID 3857.*target SRID"):
        validate_selector_expression(
            target.filter(geometry.intersects(target.geometry)), target
        )


def test_geometry_conversion_cannot_change_target_crs(target: ir.Table) -> None:
    geometry = target.geometry.convert("EPSG:4326", "EPSG:3857")
    with pytest.raises(ValueError, match=r"SRID 3857.*target SRID"):
        validate_selector_expression(
            target.filter(geometry.intersects(target.geometry)), target
        )
