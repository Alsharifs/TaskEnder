import ctypes
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

IS_ADMIN = bool(ctypes.windll.shell32.IsUserAnAdmin())


@pytest.fixture
def spawn():
    """Start a helper Python process (a stand-in for the app locking your file); always cleaned up."""
    children = []

    def start(code, *args, cwd=None, ready_file=None):
        # The base interpreter, not the venv one: a venv python.exe is a launcher that runs the
        # real interpreter as a second process, which would hold the file under a different PID.
        child = subprocess.Popen([sys._base_executable, "-c", code, *map(str, args)], cwd=cwd)
        children.append(child)
        deadline = time.time() + 10
        while ready_file and not Path(ready_file).exists() and time.time() < deadline:
            time.sleep(0.1)
        time.sleep(0.5)
        return child

    yield start
    for child in children:
        child.kill()
        child.wait()


HOLD_FILE_OPEN = "import sys, time; f = open(sys.argv[1], 'a'); time.sleep(120)"
SIT_IN_FOLDER = "import time; open('ready.txt', 'w').close(); time.sleep(120)"
