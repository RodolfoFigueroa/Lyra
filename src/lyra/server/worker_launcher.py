"""Worker process command construction and launch orchestration."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Required, TypedDict, Unpack

from lyra.server._bootstrap import command_imports

with command_imports(__name__):
    from rq import Queue, Worker
    from rq.job import Job
    from rq.serializers import JSONSerializer
    from rq.worker_pool import WorkerPool

    from lyra.server.config import ensure_runtime_directories, load_config
    from lyra.server.db.redis import create_sync_client
    from lyra.server.logging_config import configure_logging
    from lyra.server.worker import initialize_worker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redis import Redis

    from lyra.contracts.config import LyraConfig


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


def launch_worker(*, config: LyraConfig) -> None:
    """Initialize dependencies, load plugins, and enter the RQ worker process."""
    ensure_runtime_directories(config)
    configure_logging(config)
    connection = create_sync_client(config.redis)
    try:
        initialize_worker(config=config)
        pool = WorkerPool(
            [config.jobs.queue],
            connection=connection,
            num_workers=config.worker.concurrency,
            worker_class=JsonWorker,
            serializer=JSONSerializer,
        )
        pool.start(burst=False, logging_level=config.logging.level)

    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    """Build the supported worker-launcher argument parser.

    Returns:
        The worker command parser.
    """
    return argparse.ArgumentParser(
        prog="python -m lyra.server.worker_launcher",
        description="Launch a Lyra RQ worker from /lyra_data/config/lyra.toml.",
    )


def main(argv: Sequence[str] | None = None) -> None:
    """Launch a configured worker from command-line arguments."""
    build_parser().parse_args(argv)
    launch_worker(config=load_config())


if __name__ == "__main__":
    main()
