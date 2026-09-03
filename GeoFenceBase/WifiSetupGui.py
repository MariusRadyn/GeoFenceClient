#!/usr/bin/env python3
"""
Desktop WiFi setup for GeoFence Base.

GUI replacement for --newcreds:
  - scan / pick SSID
  - enter password
  - verify with nmcli
  - save encrypted creds (~/Secure/wificredentials.enc)
  - restart geofence.service

Run:
  ~/venv312/bin/python ~/GeoFenceBase/WifiSetupGui.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

# App dir on PATH so Settings / WifiCredentials import works from desktop launch
APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import WifiCredentials


SERVICE_NAME = "geofence"
VENV_PYTHON = os.path.expanduser("~/venv312/bin/python")


def restart_geofence_service() -> tuple[bool, str]:
    """Restart systemd unit without prompting when sudoers allows it."""
    cmd = ["sudo", "-n", "systemctl", "restart", SERVICE_NAME]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return False, str(e)

    if result.returncode == 0:
        return True, f"Service '{SERVICE_NAME}' restarted."

    err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
    hint = (
        "\n\nAllow passwordless restart (once):\n"
        f"  echo '{os.getenv('USER', 'geoserver')} ALL=(ALL) NOPASSWD: "
        f"/bin/systemctl restart {SERVICE_NAME}' | sudo tee "
        f"/etc/sudoers.d/geofence-wifi-setup"
    )
    return False, err + hint


class WifiSetupApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GeoFence WiFi Setup")
        self.geometry("460x320")
        self.minsize(420, 300)
        self.resizable(True, True)

        self._busy = False
        self._ssids: list[str] = []

        self._build_ui()
        self.after(200, self.refresh_networks)

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="WiFi network (SSID)").grid(row=0, column=0, sticky="w", **pad)
        self.ssid_var = tk.StringVar()
        self.ssid_combo = ttk.Combobox(frm, textvariable=self.ssid_var, width=36)
        self.ssid_combo.grid(row=0, column=1, sticky="ew", **pad)

        self.btn_refresh = ttk.Button(frm, text="Scan", command=self.refresh_networks)
        self.btn_refresh.grid(row=0, column=2, sticky="e", **pad)

        ttk.Label(frm, text="Password").grid(row=1, column=0, sticky="w", **pad)
        self.pw_var = tk.StringVar()
        self.pw_entry = ttk.Entry(frm, textvariable=self.pw_var, show="*", width=36)
        self.pw_entry.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        self.pw_entry.bind("<Return>", lambda _e: self.on_save())

        self.show_pw = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frm,
            text="Show password",
            variable=self.show_pw,
            command=self._toggle_pw,
        ).grid(row=2, column=1, sticky="w", **pad)

        self.status_var = tk.StringVar(value="Scan for networks, then enter password.")
        ttk.Label(frm, textvariable=self.status_var, wraplength=400).grid(
            row=3, column=0, columnspan=3, sticky="w", **pad
        )

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)
        self.btn_save = ttk.Button(btn_row, text="Save & Restart GeoFence", command=self.on_save)
        self.btn_save.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side=tk.LEFT)

        frm.columnconfigure(1, weight=1)

        # Prefill current SSID if file exists
        try:
            if os.path.exists(WifiCredentials.DATA_FILE):
                cur = WifiCredentials.read_credentials_file()
                if cur.get("ssid"):
                    self.ssid_var.set(cur["ssid"])
                    self.status_var.set(f"Current saved SSID: {cur['ssid']}")
        except Exception:
            pass

    def _toggle_pw(self):
        self.pw_entry.config(show="" if self.show_pw.get() else "*")

    def _set_busy(self, busy: bool, status: str = ""):
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.btn_save.config(state=state)
        self.btn_refresh.config(state=state)
        self.ssid_combo.config(state="readonly" if not busy else tk.DISABLED)
        if status:
            self.status_var.set(status)

    def refresh_networks(self):
        if self._busy:
            return
        self._set_busy(True, "Scanning WiFi networks...")

        def work():
            err = ""
            ssids = []
            try:
                ssids = WifiCredentials.list_wifi_ssids(rescan=True)
            except Exception as e:
                err = str(e)
            self.after(0, lambda: self._on_scan_done(ssids, err))

        threading.Thread(target=work, daemon=True).start()

    def _on_scan_done(self, ssids: list[str], err: str):
        self._set_busy(False)
        if err:
            self.status_var.set(f"Scan failed: {err}")
            return
        self._ssids = ssids
        self.ssid_combo["values"] = ssids
        if ssids and not self.ssid_var.get():
            self.ssid_var.set(ssids[0])
        self.status_var.set(
            f"Found {len(ssids)} network(s)." if ssids else "No WiFi networks found."
        )

    def on_save(self):
        if self._busy:
            return
        ssid = self.ssid_var.get().strip()
        password = self.pw_var.get()
        if not ssid:
            messagebox.showwarning("Missing SSID", "Enter or select a WiFi network name.")
            return

        self._set_busy(True, f"Testing connection to '{ssid}'...")

        def work():
            ok = False
            err = ""
            try:
                ok = WifiCredentials.save_new_credentials(ssid, password)
                if not ok:
                    err = "Join failed — wrong password or network unavailable. Credentials not saved."
            except Exception as e:
                err = str(e)

            restart_ok = False
            restart_msg = ""
            if ok:
                restart_ok, restart_msg = restart_geofence_service()

            self.after(0, lambda: self._on_save_done(ok, err, restart_ok, restart_msg, ssid))

        threading.Thread(target=work, daemon=True).start()

    def _on_save_done(self, ok: bool, err: str, restart_ok: bool, restart_msg: str, ssid: str):
        self._set_busy(False)
        if not ok:
            self.status_var.set(err or "Save failed.")
            messagebox.showerror("WiFi Setup Failed", err or "Could not verify credentials.")
            return

        if restart_ok:
            self.status_var.set(f"Saved '{ssid}' and restarted GeoFence.")
            messagebox.showinfo(
                "WiFi Setup Complete",
                f"Credentials for '{ssid}' verified and saved.\n\n{restart_msg}",
            )
        else:
            self.status_var.set(f"Saved '{ssid}', but service restart failed.")
            messagebox.showwarning(
                "Saved — restart needed",
                f"Credentials for '{ssid}' were saved, but the service did not restart:\n\n"
                f"{restart_msg}\n\n"
                f"Run manually:\n  sudo systemctl restart {SERVICE_NAME}",
            )


def main():
    # Prefer venv crypto/bleak stack if launched with system python by mistake
    if not sys.executable.startswith(os.path.expanduser("~/venv312")) and os.path.isfile(VENV_PYTHON):
        # Still OK if cryptography is importable; WifiCredentials will fail otherwise.
        pass

    try:
        app = WifiSetupApp()
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
