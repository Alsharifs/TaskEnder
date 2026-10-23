"""Task Ender: find what is using a file, folder or USB drive, end it after you confirm,
then delete the item or safely eject the drive. Start it with run.bat."""
import ctypes
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pythoncom

from blockers import ACTIONS, end_blockers, find_blockers
from delete import delete_path
from logger import setup_logging
from usb import eject, list_usb_drives

VERSION = "1.0.1"
APP_NAME = f"Task Ender v{VERSION}"
log = setup_logging()


class ActionTab(ttk.Frame):
    """A tab with a target picker on top (filled in by App), a blocker table and two buttons.
    `get_target()` returns (path to scan, function that deletes/ejects) or None."""

    def __init__(self, parent, app, finish_label, done_text, get_target, after_success=None):
        super().__init__(parent, padding=8)
        self.app, self.done_text, self.get_target = app, done_text, get_target
        self.after_success = after_success
        self.blockers = []

        self.picker = ttk.Frame(self)
        self.picker.pack(fill="x", pady=(0, 8))

        columns = {"name": ("Name", 190), "pid": ("PID", 70), "type": ("Type", 130), "holds": ("Holds", 380)}
        self.table = ttk.Treeview(self, columns=list(columns), show="headings", height=12)
        for column, (title, width) in columns.items():
            self.table.heading(column, text=title)
            self.table.column(column, width=width, anchor="w")
        self.table.tag_configure("protected", foreground="gray")
        self.table.pack(fill="both", expand=True)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(8, 0))
        self.buttons = [ttk.Button(buttons, text="Find blockers", command=self.find),
                        ttk.Button(buttons, text=finish_label, command=self.end_and_finish)]
        for button in self.buttons:
            button.pack(side="left", padx=(0, 8))

    def find(self, then=None):
        target = self.get_target()
        if not target:
            return
        path, finish = target
        self.app.run_in_background(f"Looking for what is using {path} ...", lambda: find_blockers(path),
                                   lambda result, error: self._show(result, error, then and (lambda: then(path, finish))))

    def end_and_finish(self):
        self.find(then=self._confirm_and_run)   # always re-scan so the confirmation list is current

    def _show(self, blockers, error, then):
        self.table.delete(*self.table.get_children())
        if error:
            self.app.report(f"Scan failed: {error}", error=True)
            return
        self.blockers = blockers
        for b in blockers:
            kind = "Protected (kept)" if b.kind == "Protected" else b.kind
            self.table.insert("", "end", values=(b.name, b.pid, kind, b.holds),
                              tags=("protected",) if b.kind == "Protected" else ())
        self.app.report(f"{len(blockers)} blocker(s) found." if blockers else "Nothing is using it.")
        if then:
            then()

    def _confirm_and_run(self, path, finish):
        to_end = [b for b in self.blockers if b.kind != "Protected"]
        if to_end:
            lines = "\n".join(f"- {ACTIONS[b.kind]}: {b.name} (PID {b.pid})" for b in to_end[:15])
            if len(to_end) > 15:
                lines += f"\n- ...and {len(to_end) - 15} more"
            question = (f"To free {path}, Task Ender will:\n\n{lines}\n\n"
                        "Unsaved work in these apps may be lost. Continue?")
            if not messagebox.askyesno(APP_NAME, question, icon="warning"):
                self.app.report("Cancelled. Nothing was changed.")
                return
        log.info("User confirmed ending %d blocker(s) for %s", len(to_end), path)
        self.app.run_in_background("Ending blockers ...", lambda: self._end_then_finish(path, to_end, finish),
                                   self._finished)

    @staticmethod
    def _end_then_finish(path, to_end, finish):
        failures = end_blockers(to_end, path) if to_end else []
        finish()   # raises with the reason if the item is still blocked
        return failures

    def _finished(self, failures, error):
        self.table.delete(*self.table.get_children())
        if error:
            self.app.report(f"Failed: {error}", error=True)
            messagebox.showerror(APP_NAME, f"{error}\n\nClick 'Find blockers' to see what is still using it.")
            return
        if self.after_success:
            self.after_success()
        self.app.report(self.done_text)
        if failures:
            messagebox.showwarning(APP_NAME, f"{self.done_text}\n\nSome items could not be ended:\n\n"
                                   + "\n".join(failures))
        else:
            messagebox.showinfo(APP_NAME, self.done_text)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME if is_admin() else f"{APP_NAME}  (not administrator: some blockers can't be ended)")
        self.geometry("860x500")
        self.minsize(640, 380)
        self._results = queue.Queue()
        self.status = tk.StringVar(value="Ready.")

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self.delete_tab = ActionTab(notebook, self, "End & Delete", "Deleted.", self._delete_target)
        self.usb_tab = ActionTab(notebook, self, "End & Eject", "Ejected. It is safe to remove the device.",
                                 self._usb_target, after_success=self.refresh_drives)
        notebook.add(self.delete_tab, text="Delete file / folder")
        notebook.add(self.usb_tab, text="Eject USB")

        self.path = tk.StringVar()
        ttk.Entry(self.delete_tab.picker, textvariable=self.path).pack(side="left", fill="x", expand=True)
        ttk.Button(self.delete_tab.picker, text="File...", command=self._pick_file).pack(side="left", padx=(8, 0))
        ttk.Button(self.delete_tab.picker, text="Folder...", command=self._pick_folder).pack(side="left", padx=(8, 0))

        self.drives = []
        self.drive_box = ttk.Combobox(self.usb_tab.picker, state="readonly")
        self.drive_box.pack(side="left", fill="x", expand=True)
        ttk.Button(self.usb_tab.picker, text="Refresh", command=self.refresh_drives).pack(side="left", padx=(8, 0))

        self.status_label = ttk.Label(self, textvariable=self.status, anchor="w", padding=(8, 0, 8, 8))
        self.status_label.pack(fill="x")
        self._all_buttons = self.delete_tab.buttons + self.usb_tab.buttons
        self.after(100, self._poll)
        self.refresh_drives()

    # --- background work: tkinter is single-threaded, so workers hand results back through a queue ---

    def run_in_background(self, busy_text, work, on_done):
        self.report(busy_text)
        self._set_busy(True)

        def worker():
            pythoncom.CoInitialize()
            try:
                result, error = work(), None
            except Exception as exc:   # shown to the user; full trace goes to the log
                log.exception("%s failed", busy_text)
                result, error = None, exc
            finally:
                pythoncom.CoUninitialize()
            self._results.put(lambda: (self._set_busy(False), on_done(result, error)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        while not self._results.empty():
            self._results.get()()
        self.after(100, self._poll)

    def _set_busy(self, busy):
        for button in self._all_buttons:
            button.state(["disabled"] if busy else ["!disabled"])
        self.configure(cursor="watch" if busy else "")

    def report(self, text, error=False):
        self.status.set(text)
        self.status_label.configure(foreground="firebrick" if error else "")

    # --- Delete tab ---

    def _pick_file(self):
        if chosen := filedialog.askopenfilename(title="Choose the file to delete"):
            self.path.set(os.path.normpath(chosen))

    def _pick_folder(self):
        if chosen := filedialog.askdirectory(title="Choose the folder to delete", mustexist=True):
            self.path.set(os.path.normpath(chosen))

    def _delete_target(self):
        path = self.path.get().strip().strip('"')
        if not path or not os.path.lexists(path):
            messagebox.showinfo(APP_NAME, "Choose an existing file or folder first.")
            return None
        return path, lambda: delete_path(path)

    # --- Eject USB tab ---

    def refresh_drives(self):
        self.run_in_background("Looking for USB drives ...", list_usb_drives, self._show_drives)

    def _show_drives(self, drives, error):
        self.drives = drives or []
        self.drive_box["values"] = [str(d) for d in self.drives]
        self.drive_box.set(str(self.drives[0]) if self.drives else "")
        if error:
            self.report(f"Could not list USB drives: {error}", error=True)
        else:
            self.report(f"{len(self.drives)} USB drive(s) found." if self.drives else "No USB drives found.")

    def _usb_target(self):
        index = self.drive_box.current()
        if index < 0:
            messagebox.showinfo(APP_NAME, "Plug in a USB drive and click Refresh first.")
            return None
        drive = self.drives[index]
        return drive.root, lambda: eject(drive)


def is_admin():
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def relaunch_as_admin():
    """Show the UAC prompt and start this script again elevated. True if the elevated copy started."""
    script = os.path.abspath(__file__)
    arguments = subprocess.list2cmdline([script, *sys.argv[1:]])
    return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, arguments,
                                               os.path.dirname(script), 1) > 32


def main():
    if not is_admin() and relaunch_as_admin():
        return
    os.chdir(os.path.dirname(os.path.abspath(__file__)))   # so the app never holds the user's folders itself
    log.info("%s started (administrator: %s)", APP_NAME, is_admin())
    App().mainloop()
    log.info("%s closed", APP_NAME)


if __name__ == "__main__":
    main()
