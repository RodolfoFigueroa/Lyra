"""Worker process command construction and launch orchestration."""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Required, TypedDict, Unpack

from typing_extensions import override

from lyra.server._bootstrap import command_imports

with command_imports(__name__):
    from rq import Queue, Worker
    from rq.exceptions import NoSuchJobError
    from rq.job import Job
    from rq.serializers import JSONSerializer
    from rq.worker_pool import WorkerPool

    from lyra.contracts.config import DEFAULT_CONFIG_PATH
    from lyra.server.config import ensure_runtime_directories, load_config
    from lyra.server.db.redis import create_sync_client
    from lyra.server.logging_config import configure_logging
    from lyra.server.worker import (
        initialize_worker,
        persist_failed_job,
        retain_rq_failure,
    )

if TYPE_CHECKING:
    from collections.abc import Sequence

    from redis import Redis
    from rq.registry import StartedJobRegistry

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

    @override
    def handle_job_failure(
        self,
        job: Job,
        queue: Queue,
        started_job_registry: StartedJobRegistry | None = None,
        exc_string: str = "",
    ) -> None:
        """Record failures observed by the supervisor even if the child was killed."""
        try:
            retain_rq_failure(job)
        finally:
            super().handle_job_failure(job, queue, started_job_registry, exc_string)

    @override
    def clean_registries(self) -> None:
        """Retry outstanding SQLite writes while RQ retains each failed job."""
        super().clean_registries()
        for queue in self.queues:
            key = f"lyra:pending-failures:{queue.name}"
            for job_id in self.connection.sscan_iter(key):
                try:
                    job = Job.fetch(
                        job_id.decode() if isinstance(job_id, bytes) else job_id,
                        connection=self.connection,
                        serializer=JSONSerializer,
                    )
                except NoSuchJobError:
                    self.connection.srem(key, job_id)
                else:
                    if persist_failed_job(job):
                        self.connection.srem(key, job_id)


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
            exception_handlers=[retain_rq_failure],
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
    parser = argparse.ArgumentParser(
        prog="python -m lyra.server.worker_launcher",
        description="Launch a Lyra RQ worker.",
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Launch a configured worker from command-line arguments."""
    args = build_parser().parse_args(argv)
    launch_worker(config=load_config(args.config))


if __name__ == "__main__":
    main()
