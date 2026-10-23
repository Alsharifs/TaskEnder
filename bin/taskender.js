#!/usr/bin/env node
// Task Ender launcher for npm/npx: finds Python 3.12+, keeps a private environment in
// %LOCALAPPDATA%\TaskEnder\.venv (rebuilt when requirements.txt changes), then starts app.py.
// Options: --check sets everything up and test-imports the app without opening the window.
"use strict";
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const VERSION = require(path.join(ROOT, "package.json")).version;
const DATA_DIR = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "TaskEnder");
const VENV = path.join(DATA_DIR, ".venv");
const VENV_PYTHON = path.join(VENV, "Scripts", "python.exe");
const VENV_PYTHONW = path.join(VENV, "Scripts", "pythonw.exe");
const STAMP = path.join(VENV, "taskender-requirements.txt");
const REQUIREMENTS = path.join(ROOT, "requirements.txt");

function fail(message) {
  console.error(`Task Ender: ${message}`);
  process.exit(1);
}

function run(command, args, options = {}) {
  return spawnSync(command, args, { encoding: "utf8", windowsHide: true, ...options });
}

// Python 3.12+ 64-bit, via the py launcher first (python.org installs it), then PATH.
function findPython() {
  const check = "import sys; print(sys.version_info >= (3, 12) and sys.maxsize > 2**32)";
  for (const [command, args] of [["py", ["-3"]], ["python", []], ["python3", []]]) {
    const result = run(command, [...args, "-c", check]);
    if (result.status === 0 && result.stdout.trim() === "True") return [command, args];
  }
  return null;
}

function venvIsReady() {
  if (!fs.existsSync(VENV_PYTHONW) || run(VENV_PYTHON, ["-c", "pass"]).status !== 0) return false;
  const wanted = fs.readFileSync(REQUIREMENTS, "utf8");
  return fs.existsSync(STAMP) && fs.readFileSync(STAMP, "utf8") === wanted;
}

function setUp() {
  if (venvIsReady()) return;
  const python = findPython();
  if (!python) fail("Python 3.12 or newer (64-bit) is needed: https://www.python.org/downloads/");
  console.log("Setting up Task Ender, this takes about a minute the first time...");
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const [command, args] = python;
  if (run(command, [...args, "-m", "venv", "--clear", VENV], { stdio: "inherit" }).status !== 0) {
    fail(`could not create the Python environment in ${VENV}`);
  }
  const pip = run(VENV_PYTHON, ["-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", REQUIREMENTS],
                  { stdio: "inherit" });
  if (pip.status !== 0) fail("could not install psutil and pywin32 (see the pip output above)");
  fs.copyFileSync(REQUIREMENTS, STAMP);
}

function main() {
  const argv = process.argv.slice(2);
  if (argv.includes("--version") || argv.includes("-v")) return console.log(VERSION);
  if (process.platform !== "win32") fail("Task Ender runs on Windows 10/11 only.");
  setUp();

  if (argv.includes("--check")) {
    const imported = run(VENV_PYTHON, ["-c", "import app; print(app.APP_NAME)"], { cwd: ROOT });
    if (imported.status !== 0) fail(`the app failed to load:\n${imported.stderr}`);
    return console.log(`${imported.stdout.trim()} is ready (environment: ${VENV}).`);
  }

  // Detached so the console can close; the app shows the UAC prompt itself.
  spawn(VENV_PYTHONW, [path.join(ROOT, "app.py")], { cwd: ROOT, detached: true, stdio: "ignore" }).unref();
  console.log(`Task Ender v${VERSION} is starting. Click Yes on the Windows administrator prompt.`);
}

main();
