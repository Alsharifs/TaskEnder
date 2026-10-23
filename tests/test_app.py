import threading
import time

import app
from logger import LOG_FILE


def test_window_builds_with_version_and_both_tabs():
    window = app.App()
    try:
        window.update()
        assert f"v{app.VERSION}" in window.title()
        assert window.delete_tab.buttons[1].cget("text") == "End & Delete"
        assert window.usb_tab.buttons[1].cget("text") == "End & Eject"
    finally:
        window.destroy()


def test_logger_writes_from_a_worker_thread():
    marker = f"worker-thread-log-check {time.time()}"
    worker = threading.Thread(target=lambda: app.log.info(marker))
    worker.start()
    worker.join()
    time.sleep(0.5)   # the listener thread writes asynchronously
    assert marker in LOG_FILE.read_text(encoding="utf-8")
