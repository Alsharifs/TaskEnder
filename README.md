# Task Ender v1.0.1

Fixes two Windows errors:

- **"Folder In Use" / "File In Use"**: the file or folder can't be deleted because something has it open.
- **"Problem Ejecting USB Mass Storage Device"**: the USB drive can't be ejected because something is using it.

Task Ender shows exactly what is holding the item. After you confirm, it closes those apps, stops
those services and closes those Explorer windows, then deletes the item or safely ejects the drive.

Windows 10/11 only. You need Python 3.12 or newer (64-bit) from https://www.python.org/downloads/.

## Run it

**With npm** (Node.js 18 or newer):

```
npx taskender
```

or install it once and then just type `taskender`:

```
npm install -g taskender
taskender
```

**From the source**: download or clone https://github.com/Alsharifs/TaskEnder and double-click **`run.bat`**.

The first run sets up a private Python environment in `%LOCALAPPDATA%\TaskEnder\.venv`, which takes
about a minute. Windows then asks for administrator rights, because stopping services and seeing every
program's open files needs them.

`taskender --check` sets everything up and checks the app loads, without opening the window.
`taskender --version` prints the version.

## Use it

**Delete file / folder tab**
1. Click **File...** or **Folder...**, or paste a path.
2. Click **Find blockers** to see what is using it. This step changes nothing.
3. Click **End & Delete**. A dialog lists exactly what will be closed or stopped. Click **Yes**.

**Eject USB tab**
1. Pick the drive from the list. Click **Refresh** after you plug one in.
2. Click **Find blockers** to see what is using it.
3. Click **End & Eject**, then click **Yes** to confirm. When it says "safe to remove", unplug the drive.

Rows marked **Protected (kept)** are core Windows parts. Task Ender never ends them.

## How it finds blockers

| Method | Catches |
|---|---|
| Windows Restart Manager | Apps and services with files open (what installers use) |
| System-wide handle snapshot | Any program with a file **or folder** open, e.g. a terminal or node.exe sitting inside the folder |
| Working folder / program path | Programs started from inside the folder or USB drive |
| Shell windows | File Explorer windows showing the folder |

It ends them gently and in this order:
1. It closes Explorer windows and checks again.
2. It stops services.
3. It asks apps to close, and forces them only after 5 seconds.
4. If Windows Explorer itself is still holding the item, it restarts Explorer.

## Files (to read or change it)

| File | What it does |
|---|---|
| `app.py` | The window: tabs, table, confirmation dialog. `VERSION` is set here. |
| `blockers.py` | Finding and ending blockers. Used by both tabs. The protected lists are at the top. |
| `delete.py` | Deletes a file or folder, including read-only items. |
| `usb.py` | Lists USB drives and safely ejects one. |
| `logger.py` | Writes the log in the background. |
| `bin/taskender.js` | The `taskender` command: finds Python, builds the environment, starts `app.py`. |
| `run.bat` | The same, for running straight from the source folder. |
| `tests\` | Automated tests (in the GitHub repo, not in the npm package). |

**When you change something:** bump `VERSION` in `app.py`, `version` in `package.json` and the title of
this README, then publish with `npm publish`. The environment is rebuilt automatically when
`requirements.txt` changes. To rebuild it by hand, delete `%LOCALAPPDATA%\TaskEnder\.venv`.

## Run the tests

```
set PY=%LOCALAPPDATA%\TaskEnder\.venv\Scripts\python.exe
%PY% -m pip install -r requirements-dev.txt
%PY% -m pytest tests -q
```
- To run the real USB eject test, plug in a USB stick you don't mind ejecting (say it is E:), then run
  `set TASK_ENDER_TEST_USB=E:` before the command above.
- The service test runs only from an administrator command prompt.

## Log

`%LOCALAPPDATA%\TaskEnder\logs\task_ender.log` rotates at 1 MB and keeps 3 old files. It records every
scan, everything that was ended, and every error.

## License

MIT, see [LICENSE](LICENSE).
