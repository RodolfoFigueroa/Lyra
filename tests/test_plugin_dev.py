"""Exercise development sessions with isolated Redis and child processes."""

from __future__ import annotations

import json
import os
import signal
import sys
import tomllib
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from redis.exceptions import RedisError
from rq.serializers import JSONSerializer

from lyra.sdk import dev
from lyra.sdk.plugin_cli import main
from lyra.server import dev_queue
from lyra.server.config import load_config


def project(path: Path, name: str = "local_plugin") -> Path:
    path.mkdir()
    (path / "pyproject.toml").write_text(
        f'[project]\nname="{name}"\nversion="1"\n'
        '[project.entry-points."lyra.plugins"]\nplugin="local:factory"\n'
    )
    return path


class Child:
    def __init__(self, *, busy: bool = False, stuck: bool = False) -> None:
        self.signals: list[int] = []
        self.stuck = stuck
        self.busy = busy
        self.code: int | None = None
        self.reaped = False

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)
        if not self.stuck and (not self.busy or len(self.signals) == 2):
            self.code = 0

    def kill(self) -> None:
        self.signals.append(signal.SIGKILL)
        self.code = -signal.SIGKILL

    def poll(self) -> int | None:
        return self.code

    def wait(self) -> int:
        assert self.code is not None
        self.reaped = True
        return self.code


class Session:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.checkout = project(tmp_path / "primary")
        self.args = ["dev", "--project", str(self.checkout)]
        self.events: list[str] = []
        self.directory = tmp_path
        self.children: dict[str, Child] = {}
        self.queues: defaultdict[str, list[str]] = defaultdict(list)
        self.client = MagicMock()
        self.client.__enter__.return_value = self.client
        self.client.ping.side_effect = lambda: self.events.append("ping")
        self.duplicate = False
        self.failure = ""
        self.ticks = 0
        self.synced: list[tuple[Path, str]] = []
        self.origin: dict = {"url": Path(__file__).parents[1].as_uri(), "dir_info": {}}
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(self.checkout / ".venv"))
        monkeypatch.setattr(dev.shutil, "which", lambda _: "/bin/uv")
        monkeypatch.setattr(
            dev,
            "subprocess",
            SimpleNamespace(
                Popen=self.spawn,
                run=self.run_command,
            ),
        )
        monkeypatch.setattr(
            dev,
            "time",
            SimpleNamespace(sleep=self.tick, monotonic=lambda: self.ticks * 0.25),
        )
        monkeypatch.setattr(
            dev.metadata,
            "distribution",
            lambda _: SimpleNamespace(
                version="1.2.3", read_text=lambda _: json.dumps(self.origin)
            ),
        )
        monkeypatch.setattr(
            dev_queue, "create_sync_client", Mock(return_value=self.client)
        )
        monkeypatch.setattr(dev_queue.Catalog, "load", Mock())
        monkeypatch.setattr(dev_queue, "Queue", self.queue)

    def queue(self, name: str, *, connection: object, serializer: object) -> Mock:
        assert connection is self.client
        assert serializer is JSONSerializer

        def empty() -> None:
            self.events.append("empty")
            self.queues[name].clear()

        return Mock(empty=empty)

    def run_command(
        self, argv: list, *, cwd: Path, env: dict, check: bool
    ) -> SimpleNamespace:
        assert not check
        if argv[0] == "/bin/uv":
            assert argv[1] == "sync"
            assert env["UV_PROJECT_ENVIRONMENT"] == str(cwd / ".venv")
            self.directory = cwd
            self.events.append("sync")
            if self.failure == "sync":
                return SimpleNamespace(returncode=1)
            if self.duplicate:
                assert main(self.args) == 2
            sources = tomllib.loads((cwd / "pyproject.toml").read_text())["tool"]["uv"][
                "sources"
            ]
            path = Path(sources["local-plugin"]["path"]) / "pyproject.toml"
            self.synced.append((cwd, path.read_text()))
        else:
            assert argv == [
                self.directory / ".venv/bin/python",
                "-I",
                "-u",
                "-m",
                "lyra.server.dev_queue",
                self.directory / "lyra.toml",
            ]
            with pytest.MonkeyPatch.context() as settings, patch.dict(os.environ, env):
                settings.chdir(cwd)
                try:
                    dev_queue.prepare(argv[-1])
                except (ValueError, RuntimeError) as exc:
                    sys.stderr.write(f"{exc}\n")
                    return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    def spawn(
        self, argv: list, *, cwd: Path, env: dict, start_new_session: bool
    ) -> Child:
        assert env["PATH"].split(os.pathsep)[0] == str(self.directory / ".venv/bin")
        assert env["VIRTUAL_ENV"] == str(self.directory / ".venv")
        assert start_new_session
        assert argv[:4] == [self.directory / ".venv/bin/python", "-I", "-u", "-m"]
        assert argv[5:] == ["--config", self.directory / "lyra.toml"]
        module = argv[4].removeprefix("lyra.server.")
        if self.failure in {"spawn", "stuck"} and module == "worker_launcher":
            msg = "Cannot spawn worker"
            raise OSError(msg)
        if module == "main":
            assert (
                env["LYRA_AGENT_API_KEY"] in (self.directory / "keys.env").read_text()
            )
        assert cwd.is_dir()
        self.events.append(module)
        child = self.children[module] = Child(
            busy=self.failure in {"busy", "drain"} and module == "worker_launcher",
            stuck=self.failure == "stuck",
        )
        if self.failure == module:
            child.code = 9
        return child

    def tick(self, _delay: float) -> None:
        self.ticks += 1
        if self.failure == "drain" and self.ticks == 4:
            worker = self.children["worker_launcher"]
            assert worker.signals == [signal.SIGINT]
            worker.code = 0
        if self.failure in {"", "busy", "drain"} and self.ticks == 1:
            signal.raise_signal(signal.SIGINT)
        if self.failure == "busy" and self.ticks == 2:
            signal.raise_signal(signal.SIGINT)

    def launch(self, *args: str) -> int:
        self.ticks = 0
        self.events.clear()
        self.children.clear()
        return main([*self.args, *args])


@pytest.fixture
def session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    return Session(tmp_path, monkeypatch)


def test_editable_session_and_restart(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    extra = project(tmp_path / "extra provider", "provider")
    resources = extra / "settings.toml"
    resources.write_text(
        '[resources.db.secrets.token]\nenv="PLUG_KEY"\n'
        '[resources.db.secrets.key]\nfile="local key"'
    )
    (extra / "local key").write_text("from-file")
    monkeypatch.setenv("PLUG_KEY", "from-env")
    args = (
        "--plugin",
        str(extra),
        "--resources",
        str(resources),
        "--port",
        "5220",
        "--redis-url",
        "redis://user:secret@localhost:6379/0",
    )
    assert session.launch(*args) == 0
    assert session.events == ["sync", "ping", "empty", "main", "worker_launcher"]
    directory = session.directory
    config = load_config(directory / "lyra.toml")
    assert config.api.host == "127.0.0.1"
    assert config.api.port == 5220
    assert config.worker_temp_dir().is_dir()
    assert config.redis.url == args[-1]
    assert [p.distribution for p in config.plugins.installed] == [
        "local-plugin",
        "provider",
    ]
    settings = tomllib.loads((directory / "pyproject.toml").read_text())
    assert settings["project"]["dependencies"] == [
        "lyra[server]",
        "local-plugin",
        "provider",
    ]
    assert settings["tool"]["uv"]["sources"] == {
        "lyra": {"path": str(Path(__file__).parents[1]), "editable": True},
        "provider": {"path": str(extra), "editable": True},
        "local-plugin": {"path": str(session.checkout), "editable": True},
    }
    keys = directory / "keys.env"
    original_keys = keys.read_bytes()
    output = capsys.readouterr()
    assert str(keys) in output.out
    assert config.api.public_base_url in output.out
    assert "secret@" not in output.out + output.err
    assert all(
        p.stat().st_mode & 0o077 == 0
        for p in (directory, keys, directory / "lyra.toml")
    )
    retained = b"previous session result store"
    config.result_store_path.write_bytes(retained)
    session.queues[config.jobs.queue].append("waiting")
    session.queues["production"].append("other job")
    metadata = session.checkout / "pyproject.toml"
    original_metadata = metadata.read_text()
    metadata.write_text(
        original_metadata.replace(
            'version="1"', 'version="1"\ndependencies=["example"]'
        )
    )
    assert session.launch(*args) == 0
    assert session.synced == [
        (directory, original_metadata),
        (directory, metadata.read_text()),
    ]
    assert session.queues == {config.jobs.queue: [], "production": ["other job"]}
    assert config.result_store_path.read_bytes() == retained
    assert keys.read_bytes() == original_keys
    assert {p.name for p in session.checkout.iterdir()} == {"pyproject.toml"}


def test_checkout_isolation(
    session: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session.duplicate = True
    assert session.launch() == 0
    assert "already running" in capsys.readouterr().err
    assert session.events == ["sync", "ping", "empty", "main", "worker_launcher"]
    initial = load_config(session.directory / "lyra.toml")
    assert initial.api.port == 5219
    assert initial.redis.url == "redis://127.0.0.1:6379/0"
    session.queues[initial.jobs.queue].append("first checkout")
    keys = (session.directory / "keys.env").read_bytes()
    other = project(tmp_path / "second")
    session.args = ["dev", "--project", str(other)]
    assert session.launch("--port", "5220") == 0
    second = load_config(session.directory / "lyra.toml")
    assert initial.jobs.queue != second.jobs.queue
    assert initial.worker_temp_dir() != second.worker_temp_dir()
    assert initial.result_store_path != second.result_store_path
    assert keys != (session.directory / "keys.env").read_bytes()
    assert session.queues[initial.jobs.queue] == ["first checkout"]


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("", 0),
        ("drain", 0),
        ("busy", 0),
        ("spawn", 2),
        ("main", 9),
        ("worker_launcher", 9),
    ],
)
def test_service_lifecycle(session: Session, mode: str, code: int) -> None:
    session.failure = mode
    previous = signal.getsignal(signal.SIGINT)
    assert session.launch() == code
    assert signal.getsignal(signal.SIGINT) == previous
    for name, child in session.children.items():
        assert child.reaped
        count = 2 if mode == "busy" and name == "worker_launcher" else int(mode != name)
        assert child.signals == [signal.SIGINT] * count
    session.failure = ""
    assert session.launch() == 0


def test_stuck_sibling_is_reaped_after_failed_start(session: Session) -> None:
    session.failure = "stuck"
    assert session.launch() == 2
    api = session.children["main"]
    assert api.signals == [signal.SIGINT, signal.SIGINT, signal.SIGKILL]
    assert api.reaped
    session.failure = ""
    assert session.launch() == 0


@pytest.mark.parametrize("failure", ["sync", "resource"])
def test_failed_preparation_preserves_waiting_jobs(
    session: Session, failure: str
) -> None:
    assert session.launch() == 0
    queue = load_config(session.directory / "lyra.toml").jobs.queue
    session.queues[queue].append("waiting")
    resource = session.checkout / "resource.toml"
    resource.write_text('[resources.db.secrets.key]\nfile="missing"')
    session.failure = failure
    assert session.launch("--resources", str(resource)) == 2
    assert session.events == ["sync"]
    assert session.queues[queue] == ["waiting"]
    session.failure = ""
    assert session.launch() == 0
    assert session.queues[queue] == []


def test_errors_before_services(
    session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert session.launch("--plugin", str(session.checkout)) == 2
    assert "Duplicate" in capsys.readouterr().err
    assert session.launch("--redis-url", "invalid://user:password@localhost") == 2
    assert "password" not in capsys.readouterr().err
    assert session.events == []
    session.client.ping.side_effect = RedisError("user:secret@redis")
    assert session.launch("--redis-url", "redis://user:secret@redis") == 2
    assert session.events == ["sync"]
    error = capsys.readouterr().err
    assert "Redis" in error
    assert "secret" not in error
    session.client.__exit__.assert_called_once()
    session.client.ping.side_effect = None
    assert session.launch() == 0


@pytest.mark.parametrize(
    ("origin", "source"),
    [
        ({}, {"git": dev.REPO, "tag": "v1.2.3"}),
        (
            {"url": "https://example.org/project", "vcs_info": {"commit_id": "abc"}},
            {"git": "https://example.org/project", "rev": "abc"},
        ),
    ],
)
def test_environment_matches_installed_sdk(
    session: Session, origin: dict, source: dict
) -> None:
    session.origin = origin
    assert session.launch() == 0
    settings = tomllib.loads((session.directory / "pyproject.toml").read_text())
    assert settings["tool"]["uv"]["sources"]["lyra"] == source
