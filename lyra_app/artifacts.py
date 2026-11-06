"""Offline pruning of shared native artifacts and computation lookups.

Stop all processes using the artifact directory before invoking this module.
Redis must remain available so retained jobs can protect their output artifacts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus
from rq.serializers import JSONSerializer

from lyra_app.config import DEFAULT_CONFIG_PATH, load_config
from lyra_app.db.redis import create_sync_client
from lyra_app.job_store import StoredArtifact

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redis import Redis


_ID = re.compile(r"^[0-9a-f]{32}$")


def retained_artifacts(connection: Redis) -> set[str]:
    """Return all retained job references, including unconfigured queues.

    Raises:
        ValueError: If a successful job's retained result cannot be interpreted.
    """
    references = set()
    for raw_key in connection.scan_iter(match="rq:job:*"):
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        if connection.type(key) not in {b"hash", "hash"}:
            continue
        try:
            job = Job.fetch(
                key.removeprefix("rq:job:"),
                connection=connection,
                serializer=JSONSerializer,
            )
        except NoSuchJobError:
            continue
        if job.get_status(refresh=False) != JobStatus.FINISHED:
            continue
        result = job.latest_result()
        if result is None:
            message = f"Cannot inspect retained result for job {job.id}"
            raise ValueError(message)
        references.add(StoredArtifact.model_validate(result.return_value).artifact_id)
    return references


def _cache_roots(
    directory: Path, retained: set[str], *, clear_cache: bool
) -> tuple[list[Path], list[Path]]:
    lookups: list[Path] = []
    temporary: list[Path] = []
    cache = directory / "cache"
    if cache.exists():
        for path in cache.iterdir():
            if path.name.startswith(".writing-"):
                temporary.append(path)
            elif path.is_file() and not path.name.endswith(".lock"):
                reference = path.read_text()
                if not _ID.fullmatch(reference):
                    message = f"Invalid artifact reference in {path.name}"
                    raise ValueError(message)
                lookups.append(path)
                if not clear_cache:
                    retained.add(reference)
    return lookups, temporary


def prune(
    directory: Path,
    connection: Redis,
    *,
    dry_run: bool = False,
    clear_cache: bool = False,
) -> dict[str, int]:
    """Remove unreferenced bundles after collecting every live reference.

    Returns:
        Counts and bytes selected for removal; dry runs do not change storage.
    """
    retained = retained_artifacts(connection)
    lookups, temporary = _cache_roots(directory, retained, clear_cache=clear_cache)
    bundles = []
    root = directory / "bundles"
    if root.exists():
        for path in root.iterdir():
            if path.name.startswith(".writing-"):
                temporary.append(path)
            elif _ID.fullmatch(path.name) and path.name not in retained:
                bundles.append(path)
    selected = [*bundles, *temporary, *(lookups if clear_cache else [])]
    size = sum(
        member.stat().st_size
        for path in selected
        for member in ([path] if path.is_file() else path.rglob("*"))
        if member.is_file() and not member.is_symlink()
    )
    if not dry_run:
        for path in selected:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    return {
        "bundles": len(bundles),
        "temporary": len(temporary),
        "lookups": len(lookups) if clear_cache else 0,
        "bytes": size,
    }


def main(argv: Sequence[str] | None = None) -> None:
    """Run maintenance with application processes stopped and Redis available."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prune"])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--clear-cache", action="store_true")
    arguments = parser.parse_args(argv)
    config = load_config(arguments.config)
    with create_sync_client(config.redis) as connection:
        result = prune(
            config.artifact_dir,
            connection,
            dry_run=arguments.dry_run,
            clear_cache=arguments.clear_cache,
        )
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
