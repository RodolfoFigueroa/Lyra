"""Worker process command construction and launch orchestration."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Required, TypedDict, Unpack

from rq import Queue, Worker
from rq.job import Job
from rq.serializers import JSONSerializer
from rq.worker_pool import WorkerPool

from lyra_app.auth import initialize_earth_engine
from lyra_app.config import (
    LyraConfig,
    WorkerCredentials,
    ensure_runtime_directories,
    load_config,
    load_worker_credentials,
)
from lyra_app.db.connection import probe_worker_database
from lyra_app.db.redis import create_sync_client
from lyra_app.logging_config import configure_logging
from lyra_app.worker import refresh_runner_registry

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redis import Redis


class WorkerInitialization(TypedDict, total=False):
    """Native WorkerPool initialization options."""

    connection: Required[Redis]
    name: str | None
    serializer: object
    job_class: type[Job]
    queue_class: type[Queue]
    exception_handlers: object


class JsonWorker(Worker):
    """Initialize native workers with JSON queues and bounded maintenance intervals."""

    def __init__(
        self, queues: Sequence[str | Queue], **options: Unpack[WorkerInitialization]
    ) -> None:
        """Set initialization options while leaving all lifecycle handling to RQ."""
        connection = options["connection"]
        json_queues = [
            Queue(
                q.name if isinstance(q, Queue) else q,
                connection=connection,
                serializer=JSONSerializer,
            )
            for q in queues
        ]
        super().__init__(
            json_queues,
            connection=connection,
            name=options.get("name"),
            serializer=JSONSerializer,
            job_class=options.get("job_class", Job),
            queue_class=options.get("queue_class", Queue),
            exception_handlers=options.get("exception_handlers"),
            worker_ttl=30,
            job_monitoring_interval=5,
            maintenance_interval=15,
        )


def launch_worker(
    worker_name: str,
    *,
    config: LyraConfig,
    credentials: WorkerCredentials,
) -> None:
    """Initialize dependencies, load plugins, and enter the RQ worker process."""
    config.get_worker(worker_name)
    ensure_runtime_directories(config)
    configure_logging(config)
    connection = create_sync_client(config.redis)
    try:
        probe_worker_database(config, password=credentials.postgres_password)
        if config.earth_engine is not None:
            initialize_earth_engine(config.earth_engine)

        refresh_runner_registry(worker_name, config=config, credentials=credentials)
        worker = config.get_worker(worker_name)
        pool = WorkerPool(
            worker.queues,
            connection=connection,
            num_workers=worker.concurrency,
            worker_class=JsonWorker,
            serializer=JSONSerializer,
        )
        pool.start(burst=False, logging_level=config.logging.level)

    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the supported worker-launcher argument parser.

    Returns:
        The parser requiring a configured worker name.
    """
    parser = argparse.ArgumentParser(
        prog="python -m lyra_app.worker_launcher",
        description="Launch a Lyra RQ worker from /lyra_data/config/lyra.toml.",
    )
    parser.add_argument("worker_name", help="Name from the [workers.<name>] table.")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Launch a configured worker from command-line arguments."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config()
        config.get_worker(args.worker_name)
        credentials = load_worker_credentials()
        launch_worker(args.worker_name, config=config, credentials=credentials)
    except KeyError as exc:
        message = str(exc.args[0]) if exc.args else str(exc)
        parser.error(message)


if __name__ == "__main__":
    main()
