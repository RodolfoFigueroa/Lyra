from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from build_scripts import profile_retained_inputs as profile


def test_case_matrix() -> None:
    cases = profile.cases()
    assert len({case.name for case in cases}) == len(cases) == 47
    assert all(case.references == 0 for case in cases if case.graph != "direct")
    assert sum(case.operation == "mixed" for case in cases) == 4
    for case in cases:
        assert profile.modes(case, "baseline") == ["baseline"]
        assert ("memory" in profile.modes(case, "all")) == (
            case.largest
            and case.graph != "full"
            and case.operation in {"cached", "description", "native"}
        )


@pytest.mark.parametrize(
    ("operation", "expected"), [("cold", 0), ("mixed", 90), ("cached", 100)]
)
def test_seed_state(
    operation: str, expected: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = Mock()
    factory = Mock(return_value=runner)
    monkeypatch.setattr(profile.LocalRunner, "from_definitions", factory)
    profile.prepare(profile.Case("direct", 100, 100, operation), tmp_path / "seed")
    if expected:
        assert len(runner.run.call_args.kwargs["inputs"]["main"]["ids"]) == expected
    else:
        factory.assert_not_called()


def test_measurement_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events = []
    monkeypatch.setattr(
        profile,
        "time",
        Mock(
            perf_counter=lambda: events.append("wall") or 1,
            process_time=lambda: events.append("cpu") or 1,
        ),
    )
    value, sample = profile.measure(
        lambda: events.append("operation") or "value", "baseline", tmp_path / "sample"
    )
    assert value == "value"
    assert events == ["wall", "cpu", "operation", "wall", "cpu"]
    assert sample["wall_seconds"] == 0


@pytest.mark.parametrize("mode", ["cpu", "memory"])
def test_instrumentation_cleanup(tmp_path: Path, mode: str) -> None:
    original = profile.LocationInputs.load

    def fail() -> None:
        message = "operation failure"
        raise ValueError(message)

    with pytest.raises(ValueError, match="operation failure"), profile.sql_probe():
        profile.measure(fail, mode, tmp_path / "sample")
    assert profile.LocationInputs.load is original
    assert not profile.tracemalloc.is_tracing()
    assert profile.sys.getprofile() is None


def test_disposable_samples_and_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = []

    def prepare(_case: profile.Case, path: Path) -> str:
        path.write_text("seed", encoding="utf-8")
        paths.append(path)
        return "reference"

    def operation(_case: profile.Case, path: Path, reference: str) -> tuple:
        assert path.read_text(encoding="utf-8") == "seed"
        assert reference == "reference"
        paths.append(path)

        def execute() -> None:
            path.write_text("changed", encoding="utf-8")

        return execute, []

    monkeypatch.setattr(profile, "validate_store", Mock())
    monkeypatch.setattr(profile, "prepare", prepare)
    monkeypatch.setattr(profile, "operation", operation)
    case = profile.Case("direct", 10, 100, "cached")
    profile.worker(case, "baseline", tmp_path)
    result = profile.json.loads((tmp_path / f"{case.name}-baseline.json").read_text())
    assert len(result["samples"]) == 3
    assert result["case"]["partitions"] == 10
    assert all(not path.exists() for path in paths)


def test_worker_failure_removes_disposable_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = []

    def prepare(_case: profile.Case, path: Path) -> str:
        path.touch()
        paths.append(path)
        return "reference"

    def operation(_case: profile.Case, path: Path, _reference: str) -> tuple:
        paths.append(path)

        def fail() -> None:
            message = "failed operation"
            raise ValueError(message)

        return fail, []

    monkeypatch.setattr(profile, "validate_store", Mock())
    monkeypatch.setattr(profile, "prepare", prepare)
    monkeypatch.setattr(profile, "operation", operation)
    with pytest.raises(ValueError, match="failed operation"):
        profile.worker(profile.Case("direct", 10, 100, "cached"), "baseline", tmp_path)
    assert all(not path.exists() for path in paths)


def test_sql_probe_preserves_values_and_removes_listeners(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        profile.event, "listen", lambda *args: calls.append(("add", args))
    )
    monkeypatch.setattr(
        profile.event, "remove", lambda *args: calls.append(("remove", args))
    )
    selection = Mock()
    original = Mock(return_value=selection)
    monkeypatch.setattr(profile.LocationInputs, "load", original)
    with profile.sql_probe() as details:
        reader = profile.LocationInputs(Mock())
        assert reader.load(Mock(), "key") is selection
        assert reader.load(Mock(), "key") is selection
    assert details["selection_loads"] == 2
    assert details["distinct_selection_loads"] == 1
    assert [args for action, args in calls if action == "add"] == [
        args for action, args in calls if action == "remove"
    ]
    assert profile.LocationInputs.load is original


def test_seed_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    prepare = Mock(return_value="reference")
    monkeypatch.setattr(profile, "prepare", prepare)
    monkeypatch.setattr(profile, "operation", Mock(return_value=(lambda: None, [])))
    monkeypatch.setattr(profile, "validate_store", Mock())
    case = profile.Case("direct", 10, 100, "cached")
    profile.worker(case, "baseline", tmp_path, seeds)
    profile.worker(case, "baseline", tmp_path, seeds)
    prepare.assert_called_once()
    result = profile.json.loads(
        (tmp_path / f"{case.name}-baseline.json").read_text(encoding="utf-8")
    )
    assert result["seed_reused"] is True


def test_stream_consumes_encoded_chunks_and_closes_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    consumed = []

    def chunks() -> Iterator[bytes]:
        for chunk in (b"header", b"body", b"end"):
            consumed.append(chunk)
            yield chunk

    context = MagicMock()
    context.__enter__.return_value.stream_location_input.return_value = chunks()
    monkeypatch.setattr(profile.ResultStore, "open", Mock(return_value=context))
    monkeypatch.setattr(profile.LocalRunner, "from_definitions", Mock())
    case = profile.Case("direct", 1000, 10000, "stream")
    execute, calls = profile.operation(case, tmp_path / "store", "reference")
    value = execute()
    assert value == len(b"headerbodyend")
    assert len(consumed) == 3
    context.__exit__.assert_called_once()
    profile.validate(case, value, calls)


def test_cli_case_selection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    name = "direct-10-100-cached"
    monkeypatch.setattr(profile.sys, "argv", ["profile", "--list", "--case", name])
    profile.asyncio.run(profile.main())
    assert capsys.readouterr().out.splitlines() == [name]


def test_cli_rejects_unknown_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(profile.sys, "argv", ["profile", "--list", "--case", "unknown"])
    with pytest.raises(SystemExit) as error:
        profile.asyncio.run(profile.main())
    assert error.value.code == 2
