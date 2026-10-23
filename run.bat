@echo off
rem Task Ender launcher: creates a private Python environment the first time, then starts the app.
rem The environment lives in %LOCALAPPDATA%\TaskEnder\.venv, the same one "npx taskender" uses.
rem The app asks for administrator rights (UAC) because ending services needs them.
cd /d "%~dp0"
set "VENV=%LOCALAPPDATA%\TaskEnder\.venv"
if not exist "%VENV%\Scripts\pythonw.exe" (
    echo First run: setting up Task Ender, this takes about a minute...
    python -m venv "%VENV%" || (echo Python 3.12 or newer is needed: https://www.python.org/downloads/ & pause & exit /b 1)
    "%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || (pause & exit /b 1)
)
start "" "%VENV%\Scripts\pythonw.exe" app.py
