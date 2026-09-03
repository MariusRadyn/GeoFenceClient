#!/usr/bin/env python3
"""
Desktop journal viewer for GeoFence Base.

Shows live output from:
  journalctl -u geofence -f

Run:
  ~/venv312/bin/python ~/GeoFenceBase/JournalGui.py
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_NAME = "geofence"
VENV_PYTHON = os.path.expanduser("~/venv312/bin/python")
MAX_LINES = 5000  # keep UI responsive


class JournalGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"GeoFence Journal — {SERVICE_NAME}")
        self.geometry("900x560")
        self.minsize(640, 400)

        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._line_q: queue.Queue[str | None] = queue.Queue()
        self._running = False
        self._paused = False
        self._autoscroll = tk.BooleanVar(value=True)
        self._boot_only = tk.BooleanVar(value=True)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.start_follow)
        self.after(80, self._drain_queue)

    def _build_ui(self):
        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(fill=tk.X)

        self.btn_start = ttk.Button(top, text="Follow", command=self.start_follow)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_stop = ttk.Button(top, text="Stop", command=self.stop_follow)
        self.btn_stop.pack(side=tk.LEFT, padx=4)

        self.btn_clear = ttk.Button(top, text="Clear", command=self.clear_view)
        self.btn_clear.pack(side=tk.LEFT, padx=4)

        self.btn_restart = ttk.Button(top, text="Restart Service", command=self.restart_service)
        self.btn_restart.pack(side=tk.LEFT, padx=4)

        ttk.Checkbutton(
            top, text="This boot only (-b)", variable=self._boot_only, command=self._on_options_changed
        ).pack(side=tk.LEFT, padx=(12, 4))

        ttk.Checkbutton(
            top, text="Auto-scroll", variable=self._autoscroll
        ).pack(side=tk.LEFT, padx=4)

        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(top, textvariable=self.status_var).pack(side=tk.RIGHT)

        mid = ttk.Frame(self, padding=(8, 0, 8, 8))
        mid.pack(fill=tk.BOTH, expand=True)

        self.text = scrolledtext.ScrolledText(
            mid,
            wrap=tk.NONE,
            font=("DejaVu Sans Mono", 10),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#ffffff",
            state=tk.DISABLED,
        )
        self.text.pack(fill=tk.BOTH, expand=True)

        # Horizontal scrollbar for long lines
        xscroll = ttk.Scrollbar(mid, orient=tk.HORIZONTAL, command=self.text.xview)
        xscroll.pack(fill=tk.X)
        self.text.configure(xscrollcommand=xscroll.set)

        self.text.tag_configure("error", foreground="#f48771")
        self.text.tag_configure("warn", foreground="#dcdcaa")
        self.text.tag_configure("ok", foreground="#89d185")

    def _journal_cmd(self) -> list[str]:
        cmd = ["journalctl", "-u", SERVICE_NAME, "-f", "--no-pager", "-o", "short-iso"]
        if self._boot_only.get():
            cmd.insert(1, "-b")
        return cmd

    def _on_options_changed(self):
        if self._running:
            self.start_follow()  # restart with new flags

    def clear_view(self):
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.configure(state=tk.DISABLED)

    def start_follow(self):
        self.stop_follow(join=False)
        self.clear_view()
        cmd = self._journal_cmd()
        self.status_var.set(" ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
        except FileNotFoundError:
            messagebox.showerror("journalctl missing", "journalctl was not found on this system.")
            self.status_var.set("journalctl not found")
            return
        except Exception as e:
            messagebox.showerror("Start failed", str(e))
            self.status_var.set(f"Error: {e}")
            return

        self._running = True
        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()
        self.status_var.set(f"Following {SERVICE_NAME}…")

    def _read_stdout(self):
        proc = self._proc
        if not proc or not proc.stdout:
            self._line_q.put(None)
            return
        try:
            for line in proc.stdout:
                self._line_q.put(line.rstrip("\n"))
        except Exception:
            pass
        self._line_q.put(None)

    def _drain_queue(self):
        try:
            while True:
                line = self._line_q.get_nowait()
                if line is None:
                    self._running = False
                    if self.status_var.get().startswith("Following"):
                        self.status_var.set("Stopped")
                    break
                self._append_line(line)
        except queue.Empty:
            pass
        self.after(80, self._drain_queue)

    def _line_tag(self, line: str) -> str | None:
        low = line.lower()
        if "error" in low or "fail" in low or "traceback" in low:
            return "error"
        if "warning" in low or "warn" in low:
            return "warn"
        if "wifi ok" in low or "restored" in low or "reconnected" in low or "started" in low:
            return "ok"
        return None

    def _append_line(self, line: str):
        tag = self._line_tag(line)
        self.text.configure(state=tk.NORMAL)
        if tag:
            self.text.insert(tk.END, line + "\n", tag)
        else:
            self.text.insert(tk.END, line + "\n")

        # Trim old lines
        end_line = int(float(self.text.index("end-1c").split(".")[0]))
        if end_line > MAX_LINES:
            self.text.delete("1.0", f"{end_line - MAX_LINES}.0")

        if self._autoscroll.get():
            self.text.see(tk.END)
        self.text.configure(state=tk.DISABLED)

    def stop_follow(self, join: bool = True):
        self._running = False
        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            try:
                proc.send_signal(signal.SIGINT)
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if join and self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)
        self._reader_thread = None
        if self.status_var.get().startswith("Following"):
            self.status_var.set("Stopped")

    def restart_service(self):
        if not messagebox.askyesno("Restart service", f"Restart {SERVICE_NAME}.service now?"):
            return
        self.status_var.set("Restarting service…")

        def work():
            try:
                r = subprocess.run(
                    ["sudo", "-n", "systemctl", "restart", SERVICE_NAME],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                ok = r.returncode == 0
                msg = (r.stderr or r.stdout or "").strip()
            except Exception as e:
                ok = False
                msg = str(e)
            self.after(0, lambda: self._on_restart_done(ok, msg))

        threading.Thread(target=work, daemon=True).start()

    def _on_restart_done(self, ok: bool, msg: str):
        if ok:
            self.status_var.set("Service restarted — following…")
            self.start_follow()
        else:
            self.status_var.set("Restart failed")
            messagebox.showerror(
                "Restart failed",
                msg
                or "Could not restart (need passwordless sudo for systemctl).\n"
                "Run: bash InstallService.sh",
            )

    def on_close(self):
        self.stop_follow()
        self.destroy()


def main():
    try:
        app = JournalGui()
        app.mainloop()
    except tk.TclError as e:
        print(
            "ERROR: tkinter UI failed. On the Pi install:\n"
            "  sudo apt install -y python3-tk\n"
            f"Details: {e}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
