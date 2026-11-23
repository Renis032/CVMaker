"""CVMaker."""

import logging

# One logger for the app, written to stderr with a time. Background work (the task worker, the scheduler, the
# Telegram bot) has no page to show an error on, so it reports here. CVMAKER_LOG_LEVEL=DEBUG adds tracebacks for
# failures that are otherwise one line (a task that failed, a source that didn't answer).
log = logging.getLogger("cvmaker")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s cvmaker: %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(_handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def debugging() -> bool:
    return log.isEnabledFor(logging.DEBUG)
