"""Run a native API and worker against editable plugin checkouts."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- supervise the native services
import sys
import time
import tomllib
from contextlib import ExitStack
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from urllib.request import url2pathname

from pydantic import ValidationError

from lyra.contracts.config import LyraConfig

if sys.platform != "win32":
    import fcntl

if TYPE_CHECKING:
    from types import FrameType

REPO = "https://github.com/RodolfoFigueroa/Lyra.git"


def _write(path: Path, content: str) -> None:
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as out:
        out.write(content)
    Path(out.name).replace(path)


def _credentials(path: Path) -> dict[str, str]:
    if not path.exists():
        _write(
            path,
            "".join(
                f"LYRA_{role}_API_KEY={secrets.token_hex(32)}\n"
                for role in ("AGENT", "ADMIN")
            ),
        )
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="ascii").splitlines()
    )


def _editable(path: Path) -> str:
    return f"{{ path = {json.dumps(str(path), ensure_ascii=False)}, editable = true }}"


def _sdk_source() -> str:
    dist = metadata.distribution("lyra")
    origin = json.loads(dist.read_text("direct_url.json") or "{}")
    url = origin.get("url", "")
    if "dir_info" in origin and url.startswith("file:"):
        checkout = Path(url2pathname(urlsplit(url).path)).resolve()
        if not (checkout / "src/lyra").is_dir():
            msg = f"Lyra SDK checkout is unavailable: {checkout}"
            raise ValueError(msg)
        return _editable(checkout)
    commit = origin.get("vcs_info", {}).get("commit_id")
    if commit:
        return f"{{ git = {json.dumps(url)}, rev = {json.dumps(commit)} }}"
    return f'{{ git = "{REPO}", tag = "v{dist.version}" }}'


def _sync(directory: Path, projects: dict[str, Path]) -> Path:
    uv = shutil.which("uv")
    if uv is None:
        msg = "Install uv to prepare the development environment."
        raise ValueError(msg)
    python_version = ".".join(map(str, sys.version_info[:3]))
    dependencies = ["lyra[server]", *projects]
    sources = {name: _editable(path) for name, path in projects.items()}
    sources["lyra"] = _sdk_source()
    _write(
        directory / "pyproject.toml",
        f"""[project]
name = "{directory.name}-environment"
version = "0"
requires-python = "=={python_version}"
dependencies = {json.dumps(dependencies)}

[tool.uv.sources]
"""
        + "".join(f"{name} = {source}\n" for name, source in sources.items()),
    )
    environment = directory / ".venv"
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment)}
    env.pop("VIRTUAL_ENV", None)
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- resolved uv executable and literal options
        [uv, "sync", "--project", directory, "--no-dev", "--python", sys.executable],
        cwd=directory,
        env=env,
        check=False,
    )
    if result.returncode:
        msg = f"uv sync exited with status {result.returncode}"
        raise ValueError(msg)
    return environment / "bin/python"


def _configuration(
    directory: Path,
    projects: dict[str, Path],
    port: int,
    valkey_url: str,
    resources: Path | None,
) -> Path:
    bindings = ""
    if resources is not None:
        bindings = resources.read_text(encoding="utf-8")
        if set(tomllib.loads(bindings)) - {"resources"}:
            msg = "--resources accepts only [resources.*] settings"
            raise ValueError(msg)
    config = f"""schema_version = 8
result_store_path = {json.dumps(str(directory / "results.sqlite3"), ensure_ascii=False)}
[api]
host = "127.0.0.1"
port = {port}
public_base_url = "http://127.0.0.1:{port}"
[valkey]
url = {json.dumps(valkey_url, ensure_ascii=False)}
[jobs]
queue = "{directory.name}"
[worker]
temp_dir = {json.dumps(str(directory / "scratch"), ensure_ascii=False)}
[logging]
"""
    for name in projects:
        config += f'\n[[plugins.installed]]\ndistribution = "{name}"\n'
    config += "\n" + bindings
    try:
        LyraConfig.model_validate(tomllib.loads(config))
    except ValidationError as exc:
        # Input values may contain Valkey or resource credentials.
        errors = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        msg = f"Invalid development configuration at {errors}"
        raise ValueError(msg) from None
    path = directory / "lyra.toml"
    _write(path, config)
    return path


@dataclass
class _Session:
    children: dict[str, subprocess.Popen] = field(default_factory=dict)
    broker: subprocess.Popen | None = None
    interrupts: int = 0
    status: int = 0

    def stop(self, _signum: int, _frame: FrameType | None) -> None:
        self.interrupts += 1

    def reap(self, *, stopping: bool) -> None:
        if self.broker is not None and self.broker.poll() is not None:
            code = self.broker.wait()
            self.broker = None
            sys.stderr.write(f"Valkey exited unexpectedly (status {code})\n")
            self.status = code if code > 0 else 1
        for service, child in self.children.copy().items():
            code = child.poll()
            if code is None:
                continue
            child.wait()
            del self.children[service]
            if not stopping:
                sys.stderr.write(f"{service} exited unexpectedly (status {code})\n")
                self.status = code if code > 0 else 1

    def interrupt_children(self) -> None:
        for child in self.children.values():
            child.send_signal(signal.SIGINT)

    def wait(self) -> None:
        warm_started = None
        forced_at = None
        while self.children:
            self.reap(stopping=bool(self.interrupts or self.status))
            if warm_started is None and (self.interrupts or self.status):
                self.interrupt_children()
                warm_started = time.monotonic()
            # RQ ignores a second interrupt within one second of its warm stop.
            if (
                warm_started is not None
                and forced_at is None
                and (self.interrupts >= 2 or self.status)
                and time.monotonic() - warm_started >= 1.5
            ):
                self.interrupt_children()
                forced_at = time.monotonic()
            # A failed startup may leave a sibling stuck before its signal handlers run.
            if (
                self.status
                and forced_at is not None
                and time.monotonic() - forced_at > 5
            ):
                for child in self.children.values():
                    child.kill()
                    child.wait()
                self.children.clear()
            if self.children:
                time.sleep(0.1)


@dataclass
class _ServerEnvironment:
    python: Path
    config: Path
    cwd: Path
    env: dict[str, str]
    keys: dict[str, str]

    def spawn(
        self, module: str, *args: str | Path, credentials: bool = False
    ) -> subprocess.Popen:
        return subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- prepared interpreter and literal arguments
            [self.python, "-I", "-u", "-m", f"lyra.server.{module}", *args],
            cwd=self.cwd,
            env={**self.env, **self.keys} if credentials else self.env,
            start_new_session=True,
        )


@dataclass
class _LocalBroker:
    executable: str
    directory: Path

    def start(self, env: dict[str, str]) -> subprocess.Popen:
        return subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- resolved local executable
            [
                self.executable,
                "--port",
                "0",
                "--unixsocket",
                str(self.directory / "sock"),
                "--unixsocketperm",
                "700",
                "--save",
                "",
                "--appendonly",
                "no",
                "--daemonize",
                "no",
            ],
            cwd=self.directory,
            env=env,
            start_new_session=True,
        )


def _stop_broker(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        process.wait()
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _start_services(session: _Session, server: _ServerEnvironment, port: int) -> None:
    options = ["--clear-waiting"] if session.broker is None else []
    preflight = server.spawn("dev_queue", server.config, *options, credentials=True)
    session.children["preflight"] = preflight
    while preflight.poll() is None:
        if session.interrupts or (session.broker and session.broker.poll() is not None):
            return
        time.sleep(0.1)
    del session.children["preflight"]
    if preflight.wait():
        msg = "Development session preparation failed."
        raise ValueError(msg)
    if session.interrupts:
        return
    sys.stdout.write(
        f"API: http://127.0.0.1:{port}\n"
        f"Credentials: {server.config.parent / 'keys.env'}\n"
    )
    sys.stdout.flush()
    for name, module in (("API", "main"), ("worker", "worker_launcher")):
        if session.interrupts:
            break
        session.children[name] = server.spawn(
            module, "--config", server.config, credentials=name == "API"
        )


def _serve(server: _ServerEnvironment, local: _LocalBroker | None, port: int) -> int:
    session = _Session()
    with ExitStack() as stack:
        for sig in (signal.SIGINT, signal.SIGTERM):
            stack.callback(signal.signal, sig, signal.signal(sig, session.stop))
        if local:
            session.broker = local.start(server.env)
            stack.callback(_stop_broker, session.broker)
        try:
            _start_services(session, server, port)
        except (OSError, ValueError):
            session.status = 1
            raise
        finally:
            session.wait()
    return session.status


def launch(
    projects: dict[str, Path],
    port: int,
    resources: Path | None,
    valkey_url: str | None = None,
) -> int:
    """Synchronize and supervise one checkout's development session.

    Returns:
        Zero after an orderly stop, or the unexpected service's exit status.

    Raises:
        ValueError: If the checkout is busy or its environment cannot start.
    """
    if sys.platform == "win32":
        msg = "Use Linux, macOS or WSL to run the fork-based Lyra worker."
        raise ValueError(msg)
    primary = next(iter(projects.values()))
    identity = hashlib.sha256(os.fsencode(primary)).hexdigest()[:20]
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache").resolve()
    directory = cache / "lyra" / f"lyra-dev-{identity}"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "session.lock").open("a") as lock, ExitStack() as stack:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            msg = f"A development session is already running for {primary}"
            raise ValueError(msg) from None
        local = None
        if valkey_url is None:
            executable = shutil.which("valkey-server")
            if executable is None:
                msg = "Install valkey-server on PATH or supply --valkey-url."
                raise ValueError(msg)
            # A short base path fits the Unix socket address limit even on macOS.
            socket_dir = Path(
                stack.enter_context(TemporaryDirectory(prefix="lyra-", dir="/tmp"))
            )
            local = _LocalBroker(executable, socket_dir)
            valkey_url = f"unix://{socket_dir}/sock"
        config = _configuration(directory, projects, port, valkey_url, resources)
        keys_path = directory / "keys.env"
        keys = _credentials(keys_path)
        python = _sync(directory, projects)
        env = {
            **os.environ,
            "VIRTUAL_ENV": str(python.parent.parent),
            "PATH": str(python.parent)
            + os.pathsep
            + os.environ.get("PATH", os.defpath),
        }
        cwd = resources.absolute().parent if resources else primary
        return _serve(_ServerEnvironment(python, config, cwd, env, keys), local, port)
