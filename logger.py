"""Async logging: callers only put records on a queue; one background thread writes them to disk."""
import atexit
import logging
import logging.handlers
import os
import queue
from pathlib import Path

# Kept outside the program folder, which may be read-only or npm's cache when started with npx.
DATA_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "TaskEnder"
LOG_FILE = DATA_DIR / "logs" / "task_ender.log"
LOGGER_NAME = "task_ender"

_listener = None


def setup_logging(level=logging.INFO):
    """Configure the shared 'task_ender' logger once and return it."""
    global _listener
    log = logging.getLogger(LOGGER_NAME)
    if _listener:
        return log

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)s %(module)s: %(message)s"))

    records = queue.SimpleQueue()
    _listener = logging.handlers.QueueListener(records, file_handler)
    _listener.start()
    atexit.register(_listener.stop)  # flushes pending records on exit

    log.setLevel(level)
    log.addHandler(logging.handlers.QueueHandler(records))
    log.propagate = False
    return log
