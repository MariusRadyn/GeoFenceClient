#!/usr/bin/env python3
"""
Desktop WiFi setup for GeoFence Base (trinity / customer UI).

Lives in /opt/geofence-tools — does NOT import GeoFenceBase.
Privileged save/list goes through: sudo /opt/geofence-tools/save-wifi
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

SERVICE_NAME = "geofence"
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_WIFI = os.path.join(TOOLS_DIR, "save-wifi")


def _run_save_wifi(args: list[str], stdin_text: str | None = None) -> tuple[bool, dict]:
    cmd = ["sudo", "-n", SAVE_WIFI, *args]
    try:
        result = subprocess.run(
            cmd,
            input=stdin_text,
            capture_output=True,
            text=True,
            timeout=90,
        )
    except Exception as e:
        return False, {"error": str(e)}

    out = (result.stdout or "").strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        err = out or (result.stderr or "").strip() or f"exit {result.returncode}"
        return False, {"error": err}

    if result.returncode != 0 or not data.get("ok"):
        return False, data if data else {"error": (result.stderr or out or "failed")}
    return True, data


def restart_geofence_service() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["sudo", "-n", "systemctl", "restart", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        return False, str(e)
    if result.returncode == 0:
        return True, f"Service '{SERVICE_NAME}' restarted."
    err = (result.stderr or result.stdout or "").strip() or f"exit {result.returncode}"
    return False, err


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
        self.after(300, self._load_current_ssid)

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
        self.btn_save = ttk.Button(btn_row, text="Save & Restart", command=self.on_save)
        self.btn_save.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text="Close", command=self.destroy).pack(side=tk.LEFT)

        frm.columnconfigure(1, weight=1)

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

    def _load_current_ssid(self):
        def work():
            ok, data = _run_save_wifi(["--show-ssid"])
            ssid = (data.get("ssid") or "") if ok else ""
            err = "" if ok else (data.get("error") or "could not read saved SSID")
            self.after(0, lambda: self._on_show_ssid(ssid, err if not ok else ""))

        threading.Thread(target=work, daemon=True).start()

    def _on_show_ssid(self, ssid: str, err: str):
        if ssid:
            self.ssid_var.set(ssid)
            self.status_var.set(f"Current saved SSID: {ssid}")
        elif err:
            self.status_var.set(f"Scan for networks, then enter password. ({err})")

    def refresh_networks(self):
        if self._busy:
            return
        self._set_busy(True, "Scanning WiFi networks...")

        def work():
            ok, data = _run_save_wifi(["--list"])
            ssids = data.get("ssids") or [] if ok else []
            err = "" if ok else (data.get("error") or "scan failed")
            self.after(0, lambda: self._on_scan_done(ssids, err if not ok else ""))

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
            payload = json.dumps({"ssid": ssid, "password": password})
            ok, data = _run_save_wifi([], stdin_text=payload)
            err = ""
            if not ok:
                err = data.get("error") or "Join failed — credentials not saved."

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
            self.status_var.set(f"Saved '{ssid}' and restarted Service.")
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
    if not os.path.isfile(SAVE_WIFI):
        messagebox.showerror(
            "Not installed",
            f"Missing helper:\n{SAVE_WIFI}\n\nRun SetupTrinityUser.sh on the Pi.",
        )
        sys.exit(1)
    try:
        app = WifiSetupApp()
        app.mainloop()
    except tk.TclError as e:
        print(
            "ERROR: tkinter UI failed. Install: sudo apt install -y python3-tk\n"
            f"Details: {e}",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
