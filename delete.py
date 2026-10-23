"""Feature A: delete a file or folder, including read-only items."""
import logging
import os
import shutil
import stat
import time

log = logging.getLogger("task_ender")

RETRIES = 3   # antivirus or the search indexer can grab a file for a moment right after it is freed


def delete_path(path):
    """Delete a file, folder or link. Raises OSError with the reason if it still cannot be deleted."""
    path = os.path.abspath(path)
    for attempt in range(1, RETRIES + 1):
        try:
            if os.path.isjunction(path) or (os.path.islink(path) and os.path.isdir(path)):
                os.rmdir(path)   # removes only the link, never the folder it points to
            elif os.path.isdir(path):
                shutil.rmtree(path, onexc=_clear_read_only_and_retry)
            else:
                os.chmod(path, stat.S_IWRITE)
                os.remove(path)
            log.info("Deleted %s", path)
            return
        except FileNotFoundError:
            log.info("%s is already gone", path)
            return
        except OSError as error:
            if attempt == RETRIES:
                log.error("Could not delete %s: %s", path, error)
                raise
            log.warning("Delete attempt %d of %s failed: %s; retrying", attempt, path, error)
            time.sleep(attempt * 0.5)


def _clear_read_only_and_retry(func, item, error):
    if isinstance(error, FileNotFoundError):
        return
    os.chmod(item, stat.S_IWRITE)
    func(item)
