#!/usr/bin/env python3
"""
Desktop journal viewer for GeoFence Base.

Shows live output from:
  journalctl -u geofence -f

Run:
  ~/venv312/bin/python ~/GeoFenceBase/JournalGui.py
  sudo bash SetupTrinityUser.sh to copy to /opt
"""

from __future__ import annotations

import json
import os
import queue
import shutil
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
TOOLS_DIR = "/opt/geofence-tools"
SERVICE_CONFIG = os.path.join(TOOLS_DIR, "service-config")


def _parse_json_payload(text: str) -> dict | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    return None


def _run_service_config(
    args: list[str], *, ask_password: bool = False
) -> tuple[bool, dict]:
    """
    ask_password=False → sudo -n (NOPASSWD --get-verbose only).
    ask_password=True  → pkexec GUI admin password (or sudo -S fallback).
    """
    if ask_password:
        if shutil.which("pkexec"):
            cmd = ["pkexec", SERVICE_CONFIG, *args]
        else:
            # Fallback if pkexec missing: prompt in Tk then sudo -S
            return False, {
                "error": "pkexec not found — install policykit-1, or re-run SetupTrinityUser.sh"
            }
    else:
        cmd = ["sudo", "-n", SERVICE_CONFIG, *args]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return False, {"error": "timed out waiting for authentication"}
    except Exception as e:
        return False, {"error": str(e)}

    out = (result.stdout or "").strip()
    err = (result.stderr or "").strip()
    data = _parse_json_payload(out) or _parse_json_payload(err) or {}
    if data.get("ok"):
        return True, data
    if result.returncode in (126, 127) or "dismissed" in err.lower() or "cancelled" in err.lower():
        return False, {"error": "Authentication cancelled"}
    if not data:
        data = {"error": out or err or f"exit {result.returncode}"}
    return False, data


class JournalGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Service Monitor — {SERVICE_NAME}")
        self.geometry("900x560")
        self.minsize(640, 400)

        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._line_q: queue.Queue[str | None] = queue.Queue()
        self._running = False
        self._paused = False
        self._autoscroll = tk.BooleanVar(value=True)
        self._boot_only = tk.BooleanVar(value=True)
        self._show_meta = tk.BooleanVar(value=False)  # False = messages only (-o cat)
        self._verbose = False

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.start_follow)
        self.after(80, self._drain_queue)
        # Verbose is opt-in: force OFF in config when opening the monitor
        self.after(150, self._ensure_verbose_off_default)

    def _build_ui(self):
        header = ttk.Frame(self, padding=(8, 8, 8, 4))
        header.pack(fill=tk.X)

        btn_row = ttk.Frame(header)
        btn_row.pack(fill=tk.X)

        self.btn_start = ttk.Button(btn_row, text="Follow", command=self.start_follow)
        self.btn_start.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_stop = ttk.Button(btn_row, text="Stop", command=self.stop_follow)
        self.btn_stop.pack(side=tk.LEFT, padx=4)

        self.btn_clear = ttk.Button(btn_row, text="Clear", command=self.clear_view)
        self.btn_clear.pack(side=tk.LEFT, padx=4)

        self.btn_restart = ttk.Button(btn_row, text="Restart", command=self.restart_service)
        self.btn_restart.pack(side=tk.LEFT, padx=4)

        self.btn_verbose = ttk.Button(btn_row, text="Verbose: OFF", command=self.toggle_verbose)
        self.btn_verbose.pack(side=tk.LEFT, padx=4)

        self.status_var = tk.StringVar(value="Starting…")
        ttk.Label(btn_row, textvariable=self.status_var).pack(side=tk.RIGHT)

        opt_row = ttk.Frame(header)
        opt_row.pack(fill=tk.X, pady=(6, 0))

        ttk.Checkbutton(
            opt_row, text="This boot", variable=self._boot_only, command=self._on_options_changed
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Checkbutton(
            opt_row, text="Time", variable=self._show_meta, command=self._on_options_changed
        ).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Checkbutton(
            opt_row, text="Scroll", variable=self._autoscroll
        ).pack(side=tk.LEFT)

        mid = ttk.Frame(self, padding=(8, 0, 8, 8))
        mid.pack(fill=tk.BOTH, expand=True)

        self.text = scrolledtext.ScrolledText(
            mid,
            wrap=tk.WORD,
            font=("DejaVu Sans Mono", 10),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#ffffff",
            state=tk.DISABLED,
        )
        self.text.pack(fill=tk.BOTH, expand=True)

        self.text.tag_configure("error", foreground="#f48771")
        self.text.tag_configure("warn", foreground="#dcdcaa")
        self.text.tag_configure("ok", foreground="#89d185")

    def _journal_cmd(self) -> list[str]:
        # -o cat = message text only (no host/unit/pid/timestamp noise)
        fmt = "short-iso" if self._show_meta.get() else "cat"
        cmd = ["journalctl", "-u", SERVICE_NAME, "-f", "--no-pager", "-o", fmt]
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
        # Skip blank / whitespace-only lines (verbose mode pads journal with spaces)
        if not line or not line.strip():
            return
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

    def _update_verbose_button(self):
        self.btn_verbose.config(text=f"Verbose: {'ON' if self._verbose else 'OFF'}")

    def _ensure_verbose_off_default(self):
        """Verbose is off by default when opening Service Monitor."""

        def work():
            was_on = False
            ok_get, data_get = _run_service_config(["--get-verbose"])
            if ok_get:
                was_on = bool(data_get.get("verbose"))

            if was_on:
                # Clear sticky ON and restart so journal matches the button
                ok, data = _run_service_config(["--set-verbose", "off"])
            else:
                ok, data = _run_service_config(
                    ["--set-verbose", "off", "--no-restart"]
                )
                if not ok and ok_get:
                    ok, data = True, {"verbose": False}

            if not ok:
                verbose = bool(data_get.get("verbose")) if ok_get else False
                err = (data or {}).get("error") or (data_get or {}).get("error") or ""
                self.after(0, lambda: self._on_verbose_state(ok_get, verbose, err))
                return

            def done():
                self._on_verbose_state(True, False, "")
                if was_on:
                    self.start_follow()

            self.after(0, done)

        threading.Thread(target=work, daemon=True).start()

    def _refresh_verbose_state(self):
        def work():
            ok, data = _run_service_config(["--get-verbose"])
            verbose = bool(data.get("verbose")) if ok else False
            err = "" if ok else (data.get("error") or "could not read verbose")
            self.after(0, lambda: self._on_verbose_state(ok, verbose, err))

        threading.Thread(target=work, daemon=True).start()

    def _on_verbose_state(self, ok: bool, verbose: bool, err: str):
        if ok:
            self._verbose = bool(verbose)
            self._update_verbose_button()
        else:
            # Default to OFF if status cannot be read
            self._verbose = False
            self._update_verbose_button()
            if err:
                self.status_var.set(f"Verbose status unavailable: {err}")

    def toggle_verbose(self):
        new_val = not self._verbose
        label = "ON" if new_val else "OFF"
        if not messagebox.askyesno(
            "Toggle verbose",
            f"Turn verbose logging {label}?\n\n"
            f"You will be asked for an admin password.",
        ):
            return
        self.btn_verbose.config(state=tk.DISABLED)
        self.status_var.set(f"Authenticate to set verbose {label}…")

        def work():
            ok, data = _run_service_config(
                ["--set-verbose", "on" if new_val else "off"],
                ask_password=True,
            )
            err = "" if ok else (data.get("error") or data.get("restart") or "failed")
            verbose = bool(data.get("verbose", new_val)) if ok else self._verbose
            self.after(0, lambda: self._on_verbose_toggled(ok, verbose, err))

        threading.Thread(target=work, daemon=True).start()

    def _on_verbose_toggled(self, ok: bool, verbose: bool, err: str):
        self.btn_verbose.config(state=tk.NORMAL)
        if ok:
            self._verbose = verbose
            self._update_verbose_button()
            self.status_var.set(f"Verbose {'ON' if verbose else 'OFF'} — following…")
            self.start_follow()
        else:
            self.status_var.set("Verbose toggle failed")
            messagebox.showerror(
                "Verbose toggle failed",
                err
                or "Authentication failed or was cancelled.\n"
                "Use the admin password when prompted.",
            )

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
