"""A separate worker process for the task queue (server mode): `python -m cvmaker.worker [threads]`.

Run as many as needed next to the web processes (which then set CVMAKER_TASK_WORKERS=0). Tasks survive restarts of
either: they wait in the queue, and a task whose worker died is picked up again when its lease runs out. With
--scheduler, this process also runs the scheduler (saved searches, reminders, backups); it is safe to run it in
several processes, only one of them acts per tick.
"""

import argparse
import signal
import threading

from . import app as _app  # noqa: F401 - importing the web app registers every task runner (most live beside their routes)
from . import auto, tasks


def main() -> None:
    parser = argparse.ArgumentParser(description="CVMaker task worker")
    parser.add_argument("threads", nargs="?", type=int, default=4, help="tasks run at the same time (default 4)")
    parser.add_argument("--scheduler", action="store_true", help="also run the scheduler")
    opts = parser.parse_args()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    tasks.start_workers(opts.threads)
    if opts.scheduler:
        auto.start()
    print(f"cvmaker worker: {opts.threads} threads{' + scheduler' if opts.scheduler else ''}, waiting for tasks")
    stop.wait()
    print("cvmaker worker: stopping (running tasks are picked up again after a restart)")


if __name__ == "__main__":
    main()
