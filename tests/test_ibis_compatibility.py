"""Connection-free evidence for the Ibis 12 / DuckDB 1.4 adapter foundation.

Keep these compiler checks when changing dependency bounds. DuckDB 1.4 LTS is
bounded separately because backend materialization APIs change between releases.
Ibis 12 uses native psycopg 3; SQLBackend.disconnect() closes its connection.
DuckDB setup accepts extensions=["spatial"] or load_extension("spatial"); both
may INSTALL/LOAD and belong to acquisition, never declaration inspection.

DuckDB 1.4 ST_GeomFromText accepts WKT and an optional *Boolean*, not an SRID:
https://duckdb.org/docs/1.4/core_extensions/spatial/functions#st_geomfromtext
Phase 12 must validate literal SRIDs against the target CRS before emitting the
one-argument form below, preserving coordinates and decoded CRS. PostgreSQL keeps
the SRID. These tests prove compilation, not live spatial execution.
"""

from unittest.mock import Mock

import ibis
import ibis.expr.datatypes as dt
import ibis.expr.types as ir
import pytest
import sqlglot as sg
from ibis.backends.duckdb import Backend as DuckDBBackend
from ibis.backends.postgres import Backend as PostgresBackend
from shapely.geometry import Point
from sqlglot import expressions as sge


@pytest.fixture(params=[DuckDBBackend, PostgresBackend], ids=["duckdb", "postgres"])
def backend(request: pytest.FixtureRequest) -> DuckDBBackend | PostgresBackend:
    # Constructing a compiler backend does not call connect(). The global guard
    # rejects accidental acquisition through either native driver.
    return request.param()


@pytest.fixture
def target() -> ir.Table:
    return ibis.table(
        {
            "location_id": "string",
            "height": "float64",
            "active": "boolean",
            "geometry": dt.GeoSpatial(geotype="geometry", srid=3857),
        },
        name="target",
    )


def test_chained_predicates_survive_relation_binding(
    backend: DuckDBBackend | PostgresBackend,
    target: ir.Table,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point = ibis.literal(Point(1, 2), type=dt.Point())
    selected = target.filter(
        (target.height > 10) & (target.active | target.height.isnull())
    ).filter(
        target.location_id.isin(["a", "b"]),
        target.geometry.intersects(point),
        target.geometry.d_within(point, distance=50),
    )
    original = backend.compile(selected, limit=None)
    # Mock introspection and extension loading; retain native relation binding.
    monkeypatch.setattr(backend, "get_schema", Mock(return_value=target.schema()))
    if isinstance(backend, DuckDBBackend):
        monkeypatch.setattr(backend, "load_extension", Mock())
    relation = backend.table("records", database="city")
    bound = selected.op().replace({target.op(): relation.op()}).to_expr()
    sql = backend.compile(bound, limit=None)
    tree = sg.parse_one(sql, dialect=backend.name)
    original_tree = sg.parse_one(original, dialect=backend.name)
    assert tree.find(sge.Where) == original_tree.find(sge.Where)
    assert {(table.db, table.name) for table in tree.find_all(sge.Table)} == {
        ("city", "records")
    }
    for predicate in (
        '"height" > 10',
        '"active" OR',
        '"height" IS NULL',
        "\"location_id\" IN ('a', 'b')",
        "ST_INTERSECTS(",
        "ST_DWITHIN(",
        "ST_GEOMFROMTEXT('POINT (1 2)')",
        "50.0",
    ):
        assert predicate in sql
    assert bound.schema() == selected.schema() == target.schema()
    assert backend.compile(selected, limit=None) == original
    assert tree.find(sge.Limit) is None


@pytest.mark.parametrize("srid", [None, 3857])
def test_geometry_literal_emission(
    backend: DuckDBBackend | PostgresBackend, target: ir.Table, srid: int | None
) -> None:
    # The SRID-bearing DuckDB SQL documents the translation requirement above;
    # successful compilation alone does not make that constructor executable.
    point = ibis.literal(Point(1, 2), type=dt.Point(srid=srid))
    dtype = point.type()
    assert isinstance(dtype, dt.GeoSpatial)
    assert dtype.srid == srid
    sql = backend.compile(target.filter(target.geometry.intersects(point)), limit=None)
    arguments = "'POINT (1 2)'" + ("" if srid is None else ", 3857")
    assert f"ST_GEOMFROMTEXT({arguments})" in sql
    encoding = "ST_ASWKB" if backend.name == "duckdb" else "ST_ASEWKB"
    assert encoding in sql


def test_explicit_unlimited_compilation(
    backend: DuckDBBackend | PostgresBackend,
    target: ir.Table,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Later acquisition must explicitly pass limit=None to materialization too.
    monkeypatch.setattr(ibis.options.sql, "default_limit", 7)
    assert sg.parse_one(backend.compile(target, limit=7)).find(sge.Limit)
    assert sg.parse_one(backend.compile(target, limit=None)).find(sge.Limit) is None
