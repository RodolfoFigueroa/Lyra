from collections.abc import Mapping
from typing import Annotated, ClassVar
from unittest.mock import Mock

import pytest

from lyra.contracts.errors import ResourceConfigurationError
from lyra.contracts.resources import IOConfig
from lyra.runtime.resources import ResourceScope, resource_invocation
from lyra.sdk.resources import BaseIO, Resource


class SetupError(RuntimeError):
    pass


class CleanupError(ValueError):
    pass


class CleanupInterrupt(BaseException):
    pass


class LifecycleIO(BaseIO):
    events: ClassVar[list[tuple[str, str]]] = []
    setup_errors: ClassVar[dict[str, BaseException]] = {}
    close_errors: ClassVar[dict[str, BaseException]] = {}

    def setup(self) -> None:
        self.usable = True
        self.events.append(("setup", self.resource_name))
        if error := self.setup_errors.get(self.resource_name):
            raise error

    def read(self) -> None:
        assert self.usable

    def close(self) -> None:
        self.events.append(("close", self.resource_name))
        self.usable = False
        if error := self.close_errors.get(self.resource_name):
            raise error


class DependantIO(LifecycleIO):
    dependency: Annotated[LifecycleIO, Resource("dependency")]

    def setup(self) -> None:
        self.dependency.read()
        super().setup()

    def close(self) -> None:
        self.dependency.read()
        super().close()


@pytest.fixture
def scope(monkeypatch: pytest.MonkeyPatch) -> ResourceScope:
    monkeypatch.setattr(LifecycleIO, "events", [])
    monkeypatch.setattr(LifecycleIO, "setup_errors", {})
    monkeypatch.setattr(LifecycleIO, "close_errors", {})
    return ResourceScope(
        {
            "dependency": LifecycleIO,
            "dependant": DependantIO,
            "unrelated": LifecycleIO,
            "unused": DependantIO,
        }
    )


@pytest.mark.parametrize("failure", ["dependency", "dependant", "execution", None])
@pytest.mark.parametrize("cleanup_error_type", [CleanupError, CleanupInterrupt])
def test_failure_precedence_and_complete_cleanup(
    scope: ResourceScope,
    failure: str | None,
    cleanup_error_type: type[BaseException],
) -> None:
    original = SetupError("original failure")
    if failure in {"dependency", "dependant"}:
        LifecycleIO.setup_errors[failure] = original
    cleanup_errors = {
        name: cleanup_error_type(name)
        for name in ("dependency", "dependant", "unrelated")
    }
    LifecycleIO.close_errors.update(cleanup_errors)

    def execute() -> None:
        with scope.activate():
            scope.get("unrelated")
            scope.get("dependant")
            if failure == "execution":
                raise original

    expected = SetupError if failure is not None else cleanup_error_type
    with pytest.raises(expected) as caught:
        execute()
    if failure is not None:
        assert caught.value is original
    else:
        assert caught.value in cleanup_errors.values()
    initialized = {"dependency", "unrelated"}
    if failure != "dependency":
        initialized.add("dependant")
    for name in initialized:
        assert LifecycleIO.events.count(("setup", name)) == 1
        assert LifecycleIO.events.count(("close", name)) == 1
    assert {name for _, name in LifecycleIO.events} == initialized
    if "dependant" in initialized:
        assert LifecycleIO.events.index(("close", "dependant")) < (
            LifecycleIO.events.index(("close", "dependency"))
        )
    assert not scope.instances
    # Cleanup errors must not leak state into an independent execution.
    LifecycleIO.setup_errors.clear()
    LifecycleIO.close_errors.clear()
    LifecycleIO.events.clear()
    fresh = ResourceScope(scope.providers)
    with fresh.activate():
        fresh.get("dependant")
    assert LifecycleIO.events == [
        ("setup", "dependency"),
        ("setup", "dependant"),
        ("close", "dependant"),
        ("close", "dependency"),
    ]


def test_caught_setup_failure_cannot_be_reused(scope: ResourceScope) -> None:
    failure = SetupError("partial setup")
    LifecycleIO.setup_errors["dependency"] = failure
    with scope.activate():
        with pytest.raises(SetupError) as caught:
            scope.get("dependency")
        assert caught.value is failure
        for name in ("dependency", "dependant"):
            with pytest.raises(ResourceConfigurationError):
                scope.get(name)
        assert LifecycleIO.events == [("setup", "dependency")]
    assert LifecycleIO.events == [
        ("setup", "dependency"),
        ("close", "dependency"),
    ]
    with pytest.raises(ResourceConfigurationError):
        scope.get("dependency")
    with pytest.raises(ResourceConfigurationError), scope.activate():
        pass


def test_constructor_failure_does_not_own_an_instance(scope: ResourceScope) -> None:
    failure = RuntimeError("constructor failed")

    class UnconstructedIO(DependantIO):
        def __init__(self, config: IOConfig, secrets: Mapping[str, str]) -> None:
            super().__init__(config, secrets)
            raise failure

    broken = ResourceScope({**scope.providers, "dependant": UnconstructedIO})
    with pytest.raises(RuntimeError) as caught, broken.activate():
        broken.get("dependant")
    assert caught.value is failure
    assert LifecycleIO.events == [
        ("setup", "dependency"),
        ("close", "dependency"),
    ]


def test_cleanup_failure_restores_scope_and_reporting(scope: ResourceScope) -> None:
    parent = Mock(job_id="parent", node="parent")
    child = Mock(job_id="child", node="child")
    failure = CleanupError("cleanup failed")
    LifecycleIO.close_errors["dependant"] = failure
    resource = BaseIO(IOConfig(), {})
    outer = ResourceScope({})

    def execute() -> None:
        with scope.activate(), resource_invocation(child):
            scope.get("dependant")
            with resource.operation("child"):
                pass

    with outer.activate(), resource_invocation(parent):
        with pytest.raises(CleanupError) as caught:
            execute()
        assert caught.value is failure
        with resource_invocation(parent) as restored:
            assert restored is outer
        with resource.operation("parent"):
            pass
    assert (
        parent.logger.info.call_args.kwargs["extra"]["structured_fields"]["job_id"]
        == "parent"
    )
    assert (
        child.logger.info.call_args.kwargs["extra"]["structured_fields"]["job_id"]
        == "child"
    )
    with pytest.raises(ResourceConfigurationError), resource_invocation(parent):
        pass


def test_teardown_cannot_acquire_or_reactivate_resources() -> None:
    attempts = []

    class ReentrantIO(BaseIO):
        def close(self) -> None:
            for name in (self.resource_name, "unused"):
                with pytest.raises(ResourceConfigurationError):
                    scope.get(name)
                attempts.append(name)
            with pytest.raises(ResourceConfigurationError), scope.activate():
                pass

    scope = ResourceScope({"resource": ReentrantIO, "unused": BaseIO})
    with scope.activate():
        scope.get("resource")
        with pytest.raises(ResourceConfigurationError), scope.activate():
            pass
        assert scope.get("resource") is scope.instances["resource"]
    assert attempts == ["resource", "unused"]
