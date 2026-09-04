#!/bin/bash
# Create locked-down "trinity" customer user + install customer tools to /opt.
#
# Roles:
#   geoserver  → ADMIN (full SSH/login, owns app + venv, runs geofence service)
#   trinity    → CUSTOMER (no password; SSH keys only; cannot read GeoFenceBase)
#
# Layout:
#   /home/geoserver/GeoFenceBase  → geoserver:geoserver 750 (trinity cannot read)
#   /home/geoserver/Secure        → geoserver:geoserver 700
#   /opt/geofence-tools           → root:root 755 — ONLY place trinity uses for GUIs
#   trinity                       → SSH keys only; desktop icons; passwordless systemctl
#
# Customer tools never live under GeoFenceBase. This script installs them to
# /opt/geofence-tools (copies WifiSetupGui/JournalGui from the admin tree as
# source, then writes save-wifi helpers directly into /opt).
#
# Run as root after ConfigureRaspberry.sh / InstallService.sh:
#   sudo bash SetupTrinityUser.sh
#
# SSH keys: edit ssh_keys (one public key per line), then re-run this script.

set -e

# WinSCP often uploads CRLF; that breaks .desktop files ("Invalid desktop entry file").
# Strip this script in place (keep same path so SCRIPT_DIR still finds app files), then re-exec.
if grep -q $'\r' "$0" 2>/dev/null; then
    _self="$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")"
    tr -d '\r' < "$_self" > "${_self}.lf"
    mv "${_self}.lf" "$_self"
    exec bash "$_self" "$@"
fi

SERVICE_USER="geoserver"
CUSTOMER_USER="trinity"
SERVICE_NAME="geofence"
APP_DIR="/home/${SERVICE_USER}/GeoFenceBase"
SECURE_DIR="/home/${SERVICE_USER}/Secure"
VENV_PYTHON="/home/${SERVICE_USER}/venv312/bin/python"
TOOLS_DIR="/opt/geofence-tools"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Keys file next to this script, or already under APP_DIR
SSH_KEYS_FILE=""
for cand in "$SCRIPT_DIR/ssh_keys" "$APP_DIR/ssh_keys"; do
    if [ -f "$cand" ]; then
        SSH_KEYS_FILE="$cand"
        break
    fi
done

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root: sudo bash SetupTrinityUser.sh"
    exit 1
fi

if [ ! -f "$APP_DIR/GeoFenceClient.py" ]; then
    echo "ERROR: app not found at $APP_DIR — run InstallService.sh first."
    exit 1
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: venv python not found: $VENV_PYTHON"
    exit 1
fi

# Ensure service/admin user exists
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "ERROR: user $SERVICE_USER does not exist."
    exit 1
fi

echo "=== Admin user: $SERVICE_USER (keep login shell) ==="
# Restore normal shell if a previous lockdown set nologin
if getent passwd "$SERVICE_USER" | grep -q nologin; then
    echo "Restoring /bin/bash for $SERVICE_USER (admin account)..."
    usermod -s /bin/bash "$SERVICE_USER"
fi
usermod -aG netdev,adm,sudo "$SERVICE_USER" 2>/dev/null || \
    usermod -aG netdev,sudo "$SERVICE_USER" 2>/dev/null || true

echo "=== Creating customer user: $CUSTOMER_USER ==="
if ! id "$CUSTOMER_USER" &>/dev/null; then
    adduser --disabled-password --gecos "trinity" "$CUSTOMER_USER"
else
    echo "User $CUSTOMER_USER already exists."
fi
# Login screen / user list display name
chfn -f "trinity" "$CUSTOMER_USER" 2>/dev/null || \
    usermod -c "trinity" "$CUSTOMER_USER" 2>/dev/null || true

# Desktop: empty password allowed (unlock + delete). SSH stays publickey-only (sshd Match).
passwd -u "$CUSTOMER_USER" 2>/dev/null || true
passwd -d "$CUSTOMER_USER" 2>/dev/null || true
usermod -s /bin/bash "$CUSTOMER_USER"

# Allow blank password at local GUI / console (SSH still rejects passwords via sshd_config)
PAM_AUTH="/etc/pam.d/common-auth"
if [ -f "$PAM_AUTH" ] && grep -q 'pam_unix\.so' "$PAM_AUTH"; then
    if ! grep -E 'pam_unix\.so.*nullok' "$PAM_AUTH" >/dev/null 2>&1; then
        cp -a "$PAM_AUTH" "${PAM_AUTH}.geofence.bak"
        sed -i -E 's/(pam_unix\.so)([ \t]|$)/\1 nullok\2/' "$PAM_AUTH"
        echo "  PAM: added nullok for blank local password"
    else
        echo "  PAM: nullok already present"
    fi
fi

# Auto-login as customer on graphical desktop (no password prompt at boot)
if [ -d /etc/lightdm ] || command -v lightdm >/dev/null 2>&1; then
    mkdir -p /etc/lightdm/lightdm.conf.d
    cat > /etc/lightdm/lightdm.conf.d/22-geofence-autologin.conf <<EOF
[Seat:*]
autologin-user=${CUSTOMER_USER}
autologin-user-timeout=0
EOF
    # Ensure autologin group exists (Debian/lightdm)
    getent group autologin >/dev/null 2>&1 || groupadd -r autologin 2>/dev/null || true
    usermod -aG autologin "$CUSTOMER_USER" 2>/dev/null || true
    echo "  LightDM autologin → $CUSTOMER_USER"
fi
# Raspberry Pi OS also stores boot behaviour here on some images
if [ -f /etc/lightdm/lightdm.conf ]; then
    sed -i -E "s/^#?autologin-user=.*/autologin-user=${CUSTOMER_USER}/" /etc/lightdm/lightdm.conf
    if ! grep -q '^autologin-user=' /etc/lightdm/lightdm.conf; then
        printf '\n[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n' "$CUSTOMER_USER" >> /etc/lightdm/lightdm.conf
    fi
fi

# Groups: journal read + netdev; never geoserver group (would expose app files)
usermod -aG adm,systemd-journal,netdev "$CUSTOMER_USER" 2>/dev/null || \
    usermod -aG adm,netdev "$CUSTOMER_USER" 2>/dev/null || true

CUSTOMER_HOME="$(getent passwd "$CUSTOMER_USER" | cut -d: -f6)"
mkdir -p "$CUSTOMER_HOME/.ssh"
chmod 700 "$CUSTOMER_HOME/.ssh"
AUTH_KEYS="$CUSTOMER_HOME/.ssh/authorized_keys"

if [ -n "$SSH_KEYS_FILE" ]; then
    echo "Installing SSH keys from $SSH_KEYS_FILE"
    grep -vE '^\s*(#|$)' "$SSH_KEYS_FILE" | sed 's/\r$//' > "$AUTH_KEYS"
    KEY_COUNT="$(grep -c . "$AUTH_KEYS" || true)"
    echo "  installed $KEY_COUNT key(s)"
else
    echo "WARNING: ssh_keys file not found — creating empty authorized_keys"
    echo "  Add keys to $APP_DIR/ssh_keys and re-run this script."
    : > "$AUTH_KEYS"
fi

chmod 600 "$AUTH_KEYS"
chown -R "$CUSTOMER_USER:$CUSTOMER_USER" "$CUSTOMER_HOME/.ssh"

echo "=== Installing customer tools to $TOOLS_DIR (not under GeoFenceBase) ==="
mkdir -p "$TOOLS_DIR"

# GUIs: copy from admin source tree into /opt (trinity never reads APP_DIR)
find_file() {
    local name="$1"
    for d in "$SCRIPT_DIR" "$APP_DIR"; do
        if [ -f "$d/$name" ]; then
            echo "$d/$name"
            return 0
        fi
    done
    return 1
}

for f in WifiSetupGui.py JournalGui.py; do
    src="$(find_file "$f" || true)"
    if [ -n "$src" ]; then
        sed -i 's/\r$//' "$src" 2>/dev/null || true
        cp "$src" "$TOOLS_DIR/$f"
        echo "  $f ← $src  →  $TOOLS_DIR/$f"
    elif [ -f "$TOOLS_DIR/$f" ]; then
        echo "  keep existing $TOOLS_DIR/$f"
    else
        echo "ERROR: missing $f (looked in $SCRIPT_DIR and $APP_DIR)"
        exit 1
    fi
done

# Privileged helpers: always write into /opt (repo tools/ is optional source only)
if [ -f "$SCRIPT_DIR/tools/save_wifi.py" ]; then
    sed -i 's/\r$//' "$SCRIPT_DIR/tools/save_wifi.py" 2>/dev/null || true
    cp "$SCRIPT_DIR/tools/save_wifi.py" "$TOOLS_DIR/save_wifi.py"
    echo "  save_wifi.py ← $SCRIPT_DIR/tools/"
elif [ -f "$APP_DIR/tools/save_wifi.py" ]; then
    # legacy leftover under APP_DIR — copy out to /opt then ignore APP_DIR copy
    cp "$APP_DIR/tools/save_wifi.py" "$TOOLS_DIR/save_wifi.py"
    echo "  save_wifi.py ← $APP_DIR/tools/ (legacy; runtime is $TOOLS_DIR)"
else
    echo "  writing save_wifi.py → $TOOLS_DIR"
    cat > "$TOOLS_DIR/save_wifi.py" <<'PY'
#!/usr/bin/env python3
"""Privileged WiFi helper for GeoFence (run via sudo as root only).

Installed at: /opt/geofence-tools/save_wifi.py
Imports WifiCredentials from /home/geoserver/GeoFenceBase as root only.
"""
from __future__ import annotations

import json
import os
import sys

GEOSERVER_HOME = "/home/geoserver"
APP_DIR = f"{GEOSERVER_HOME}/GeoFenceBase"

os.environ["HOME"] = GEOSERVER_HOME
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import WifiCredentials  # noqa: E402


class _StdoutToStderr:
    def __enter__(self):
        self._out = sys.stdout
        sys.stdout = sys.stderr
        return self

    def __exit__(self, *args):
        sys.stdout = self._out


def main() -> int:
    if os.geteuid() != 0:
        print("ERROR: must run as root (via sudo)", file=sys.stderr)
        return 2

    if "--list" in sys.argv:
        try:
            with _StdoutToStderr():
                ssids = WifiCredentials.list_wifi_ssids(rescan=True)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return 1
        print(json.dumps({"ok": True, "ssids": ssids}))
        return 0

    if "--show-ssid" in sys.argv:
        ssid = ""
        try:
            with _StdoutToStderr():
                if os.path.exists(WifiCredentials.DATA_FILE):
                    ssid = WifiCredentials.read_credentials_file().get("ssid", "") or ""
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e), "ssid": ""}))
            return 1
        print(json.dumps({"ok": True, "ssid": ssid}))
        return 0

    try:
        data = json.loads(sys.stdin.read())
        ssid = (data.get("ssid") or "").strip()
        password = data.get("password") or ""
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"invalid input: {e}"}))
        return 1

    if not ssid:
        print(json.dumps({"ok": False, "error": "empty ssid"}))
        return 1

    try:
        with _StdoutToStderr():
            ok = WifiCredentials.save_new_credentials(ssid, password)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1

    if not ok:
        print(json.dumps({"ok": False, "error": "verify/join failed — not saved"}))
        return 1

    print(json.dumps({"ok": True, "ssid": ssid}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
PY
fi

cat > "$TOOLS_DIR/save-wifi" <<EOF
#!/bin/bash
# sudoers allows this single path; runs as root, reads admin GeoFenceBase.
set -e
export HOME=/home/${SERVICE_USER}
exec ${VENV_PYTHON} ${TOOLS_DIR}/save_wifi.py "\$@"
EOF
echo "  save-wifi → $TOOLS_DIR/save-wifi"

# Optional: remove leftover tools/ under APP_DIR so it is not confused with runtime
if [ -d "$APP_DIR/tools" ]; then
    rm -rf "$APP_DIR/tools"
    echo "  removed leftover $APP_DIR/tools (runtime is $TOOLS_DIR only)"
fi

chmod 755 "$TOOLS_DIR"
chmod 755 "$TOOLS_DIR/WifiSetupGui.py" "$TOOLS_DIR/JournalGui.py"
chmod 755 "$TOOLS_DIR/save-wifi"
chmod 644 "$TOOLS_DIR/save_wifi.py"
chown -R root:root "$TOOLS_DIR"

if ! dpkg -s python3-tk &>/dev/null; then
    apt-get update -qq
    apt-get install -y python3-tk
fi

echo "=== App ownership for admin $SERVICE_USER (hide from $CUSTOMER_USER) ==="
# Admin owns and can edit; trinity (other) has no access (750 / 640)
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
find "$APP_DIR" -type d -exec chmod 750 {} \;
find "$APP_DIR" -type f -exec chmod 640 {} \;
chmod 750 "$APP_DIR"/*.sh 2>/dev/null || true
chmod 750 "$APP_DIR"/*.py 2>/dev/null || true

mkdir -p "$SECURE_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$SECURE_DIR"
chmod 700 "$SECURE_DIR"
find "$SECURE_DIR" -type f -exec chmod 600 {} \; 2>/dev/null || true

if [ -d "/home/${SERVICE_USER}/venv312" ]; then
    chown -R "$SERVICE_USER:$SERVICE_USER" "/home/${SERVICE_USER}/venv312"
    chmod -R 750 "/home/${SERVICE_USER}/venv312"
fi

# Block trinity from entering /home/geoserver
chmod 750 "/home/${SERVICE_USER}"
chown "$SERVICE_USER:$SERVICE_USER" "/home/${SERVICE_USER}"

echo "=== Sudoers for $CUSTOMER_USER (passwordless service control) ==="
SUDOERS="/etc/sudoers.d/geofence-trinity"
cat > "$SUDOERS" <<EOF
# GeoFence customer — passwordless start/stop/restart/status + WiFi save helper
Cmnd_Alias GEOFENCE_CTL = /bin/systemctl start ${SERVICE_NAME}, /bin/systemctl stop ${SERVICE_NAME}, /bin/systemctl restart ${SERVICE_NAME}, /bin/systemctl status ${SERVICE_NAME}
Cmnd_Alias GEOFENCE_WIFI = ${TOOLS_DIR}/save-wifi
${CUSTOMER_USER} ALL=(root) NOPASSWD: GEOFENCE_CTL, GEOFENCE_WIFI
EOF
chmod 440 "$SUDOERS"
visudo -cf "$SUDOERS"

if [ -f /etc/sudoers.d/geofence-wifi-setup ]; then
    rm -f /etc/sudoers.d/geofence-wifi-setup
    echo "Removed old /etc/sudoers.d/geofence-wifi-setup"
fi

echo "=== Desktop icons for $CUSTOMER_USER ==="
DESKTOP_DIR="${CUSTOMER_HOME}/Desktop"
APPS_DIR="${CUSTOMER_HOME}/.local/share/applications"
mkdir -p "$DESKTOP_DIR" "$APPS_DIR"

PY3="$(command -v python3)"

# Shell wrappers — more reliable than Exec=python script.py on Raspberry Pi OS
# Write with printf so lines are always LF (never CRLF).
write_lines() {
    # write_lines DEST line1 line2 ...
    local dest="$1"
    shift
    : > "$dest"
    local line
    for line in "$@"; do
        printf '%s\n' "$line" >> "$dest"
    done
    sed -i 's/\r$//' "$dest" 2>/dev/null || true
}

cat > "$TOOLS_DIR/run-wifi-setup.sh" <<EOF
#!/bin/bash
cd "$TOOLS_DIR" || exit 1
export DISPLAY="\${DISPLAY:-:0}"
LOG="\${HOME}/geofence-gui.log"
{
  echo "---- \$(date) WiFi Setup ----"
  echo "user=\$(id -un) DISPLAY=\$DISPLAY"
} >>"\$LOG"
exec "$PY3" "$TOOLS_DIR/WifiSetupGui.py" "\$@" >>"\$LOG" 2>&1
EOF
tr -d '\r' < "$TOOLS_DIR/run-wifi-setup.sh" > "$TOOLS_DIR/run-wifi-setup.sh.tmp"
mv "$TOOLS_DIR/run-wifi-setup.sh.tmp" "$TOOLS_DIR/run-wifi-setup.sh"

cat > "$TOOLS_DIR/run-journal.sh" <<EOF
#!/bin/bash
cd "$TOOLS_DIR" || exit 1
export DISPLAY="\${DISPLAY:-:0}"
LOG="\${HOME}/geofence-gui.log"
{
  echo "---- \$(date) Journal ----"
  echo "user=\$(id -un) DISPLAY=\$DISPLAY"
} >>"\$LOG"
exec "$PY3" "$TOOLS_DIR/JournalGui.py" "\$@" >>"\$LOG" 2>&1
EOF
tr -d '\r' < "$TOOLS_DIR/run-journal.sh" > "$TOOLS_DIR/run-journal.sh.tmp"
mv "$TOOLS_DIR/run-journal.sh.tmp" "$TOOLS_DIR/run-journal.sh"

chmod 755 "$TOOLS_DIR/run-wifi-setup.sh" "$TOOLS_DIR/run-journal.sh"
chmod 755 "$TOOLS_DIR/WifiSetupGui.py" "$TOOLS_DIR/JournalGui.py"
sed -i '1s|^#!.*|#!/usr/bin/env python3|' "$TOOLS_DIR/WifiSetupGui.py" "$TOOLS_DIR/JournalGui.py" 2>/dev/null || true
sed -i 's/\r$//' "$TOOLS_DIR/WifiSetupGui.py" "$TOOLS_DIR/JournalGui.py" 2>/dev/null || true

# Minimal .desktop entries — Raspberry Pi OS rejects CRLF / odd keys as "Invalid desktop entry"
write_desktop() {
    local dest="$1"
    local name="$2"
    local exec_cmd="$3"
    local icon="$4"
    # Absolute Exec + TryExec; no Path= (pcmanfm can reject bad Path)
    write_lines "$dest" \
        "[Desktop Entry]" \
        "Type=Application" \
        "Name=${name}" \
        "Exec=${exec_cmd}" \
        "TryExec=${exec_cmd}" \
        "Icon=${icon}" \
        "Terminal=false" \
        "StartupNotify=true"
    chmod 755 "$dest"
    if command -v desktop-file-validate >/dev/null 2>&1; then
        desktop-file-validate "$dest" || echo "WARNING: validate failed for $dest"
    fi
}

write_desktop "$DESKTOP_DIR/geofence-wifi-setup.desktop" \
    "Set Wifi Credentials" \
    "$TOOLS_DIR/run-wifi-setup.sh" \
    "network-wireless"

write_desktop "$DESKTOP_DIR/geofence-journal.desktop" \
    "Service Monitor" \
    "$TOOLS_DIR/run-journal.sh" \
    "utilities-system-monitor"

cp "$DESKTOP_DIR/geofence-wifi-setup.desktop" "$APPS_DIR/"
cp "$DESKTOP_DIR/geofence-journal.desktop" "$APPS_DIR/"
chmod 755 "$APPS_DIR"/geofence-*.desktop
chown -R "$CUSTOMER_USER:$CUSTOMER_USER" "$DESKTOP_DIR" "$APPS_DIR"

# Mark trusted (Raspberry Pi OS). Must run as desktop user when possible.
trust_desktop() {
    local f="$1"
    chmod 755 "$f"
    if command -v gio >/dev/null 2>&1; then
        sudo -u "$CUSTOMER_USER" gio set "$f" metadata::trusted true 2>/dev/null || \
        sudo -u "$CUSTOMER_USER" gio set -t string "$f" metadata::trusted true 2>/dev/null || true
    fi
}
trust_desktop "$DESKTOP_DIR/geofence-wifi-setup.desktop"
trust_desktop "$DESKTOP_DIR/geofence-journal.desktop"

# Disable "Do you want to execute this file?" (PCManFM / Raspberry Pi OS)
# for customer (trinity) and admin (geoserver)
set_pcmanfm_quick_exec() {
    local user="$1"
    local home
    home="$(getent passwd "$user" | cut -d: -f6)"
    if [ -z "$home" ] || [ ! -d "$home" ]; then
        echo "  skip quick_exec for $user (no home)"
        return 0
    fi
    for profile in default LXDE-pi LXDE rpd-x rpd-wayland; do
        conf_dir="${home}/.config/pcmanfm/${profile}"
        mkdir -p "$conf_dir"
        conf="${conf_dir}/pcmanfm.conf"
        if [ -f "$conf" ]; then
            if grep -q '^quick_exec=' "$conf" 2>/dev/null; then
                sed -i 's/^quick_exec=.*/quick_exec=1/' "$conf"
            elif grep -q '^\[config\]' "$conf" 2>/dev/null; then
                sed -i '/^\[config\]/a quick_exec=1' "$conf"
            else
                printf '\n[config]\nquick_exec=1\n' >> "$conf"
            fi
        else
            printf '[config]\nquick_exec=1\n' > "$conf"
        fi
    done
    chown -R "$user:$user" "${home}/.config/pcmanfm"
    echo "  PCManFM quick_exec=1 for $user"
}
set_pcmanfm_quick_exec "$CUSTOMER_USER"
set_pcmanfm_quick_exec "$SERVICE_USER"

# Trinity desktop wallpaper (readable by trinity — not under GeoFenceBase)
echo "=== Desktop wallpaper for $CUSTOMER_USER ==="
WALLPAPER_DEST="/usr/share/geofence/trinity-3d.png"
WALLPAPER_SRC=""
# Also match WinSCP odd names / case
while IFS= read -r -d '' cand; do
    WALLPAPER_SRC="$cand"
    break
done < <(find "$SCRIPT_DIR" "$APP_DIR" -maxdepth 1 -type f \
    \( -iname 'trinity*3d*.png' -o -iname 'trinity*3d*.jpg' -o -iname 'trinity 3d.png' \) \
    -print0 2>/dev/null)

for cand in \
    "$SCRIPT_DIR/Trinity 3D.png" \
    "$APP_DIR/Trinity 3D.png" \
    "$SCRIPT_DIR/trinity-3d.png" \
    "$APP_DIR/trinity-3d.png"
do
    if [ -z "$WALLPAPER_SRC" ] && [ -f "$cand" ]; then
        WALLPAPER_SRC="$cand"
        break
    fi
done

if [ -n "$WALLPAPER_SRC" ]; then
    mkdir -p "$(dirname "$WALLPAPER_DEST")"
    cp "$WALLPAPER_SRC" "$WALLPAPER_DEST"
    chmod 644 "$WALLPAPER_DEST"
    chown root:root "$WALLPAPER_DEST"
    echo "  wallpaper ← $WALLPAPER_SRC → $WALLPAPER_DEST"

    # Helper applied at login (config alone is often ignored until pcmanfm runs)
    cat > "$TOOLS_DIR/set-wallpaper.sh" <<EOF
#!/bin/bash
IMG="$WALLPAPER_DEST"
[ -f "\$IMG" ] || exit 0
export DISPLAY="\${DISPLAY:-:0}"
export WAYLAND_DISPLAY="\${WAYLAND_DISPLAY:-wayland-0}"
# Raspberry Pi OS desktop background is drawn by pcmanfm
if command -v pcmanfm >/dev/null 2>&1; then
    pcmanfm --set-wallpaper="\$IMG" >/dev/null 2>&1 || true
fi
EOF
    chmod 755 "$TOOLS_DIR/set-wallpaper.sh"

    set_trinity_wallpaper() {
        local user="$1"
        local img="$2"
        local home
        home="$(getent passwd "$user" | cut -d: -f6)"
        [ -n "$home" ] && [ -d "$home" ] || return 0

        # Copy into user Pictures (GUI file picker path)
        mkdir -p "$home/Pictures"
        cp "$img" "$home/Pictures/trinity-3d.png"
        chown -R "$user:$user" "$home/Pictures"

        for profile in default LXDE-pi LXDE rpd-x rpd-wayland; do
            conf_dir="${home}/.config/pcmanfm/${profile}"
            mkdir -p "$conf_dir"

            # Pi OS reads wallpaper from desktop-items-0.conf (not only pcmanfm.conf)
            for items in desktop-items-0.conf desktop-items-1.conf; do
                cat > "${conf_dir}/${items}" <<EOF
[*]
wallpaper_mode=crop
wallpaper_common=1
wallpaper=${img}
desktop_bg=#000000
desktop_fg=#ffffff
desktop_shadow=#000000
desktop_font=PibotoLt 12
show_wm_menu=0
folder=${home}/Desktop
show_documents=0
show_trash=0
show_mounts=0
EOF
            done

            conf="${conf_dir}/pcmanfm.conf"
            if [ ! -f "$conf" ]; then
                printf '[config]\nquick_exec=1\n\n[desktop]\nwallpaper_mode=crop\nwallpaper=%s\n' "$img" > "$conf"
            else
                grep -q '^\[config\]' "$conf" || printf '[config]\n' >> "$conf"
                if grep -q '^quick_exec=' "$conf"; then
                    sed -i 's/^quick_exec=.*/quick_exec=1/' "$conf"
                else
                    sed -i '/^\[config\]/a quick_exec=1' "$conf"
                fi
                if ! grep -q '^\[desktop\]' "$conf"; then
                    printf '\n[desktop]\nwallpaper_mode=crop\nwallpaper=%s\n' "$img" >> "$conf"
                else
                    if grep -q '^wallpaper=' "$conf"; then
                        sed -i "s|^wallpaper=.*|wallpaper=${img}|" "$conf"
                    else
                        sed -i "/^\[desktop\]/a wallpaper=${img}" "$conf"
                    fi
                    if grep -q '^wallpaper_mode=' "$conf"; then
                        sed -i 's|^wallpaper_mode=.*|wallpaper_mode=crop|' "$conf"
                    else
                        sed -i '/^\[desktop\]/a wallpaper_mode=crop' "$conf"
                    fi
                fi
            fi
        done

        # Autostart: apply wallpaper every graphical login
        mkdir -p "${home}/.config/autostart"
        cat > "${home}/.config/autostart/geofence-wallpaper.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=GeoFence Wallpaper
Exec=${TOOLS_DIR}/set-wallpaper.sh
Hidden=false
X-GNOME-Autostart-enabled=true
EOF
        chmod 755 "${home}/.config/autostart/geofence-wallpaper.desktop"
        chown -R "$user:$user" "${home}/.config"

        # Try apply now (works if trinity has an active desktop session)
        sudo -u "$user" env DISPLAY="${DISPLAY:-:0}" \
            XDG_RUNTIME_DIR="/run/user/$(id -u "$user")" \
            "$TOOLS_DIR/set-wallpaper.sh" 2>/dev/null || true

        echo "  wallpaper config + autostart for $user"
        echo "  Log out/in as $user (or reboot) to apply if not visible yet."
    }
    set_trinity_wallpaper "$CUSTOMER_USER" "$WALLPAPER_DEST"
else
    echo "  WARNING: Trinity 3D.png not found — wallpaper NOT set"
    echo "    Copy 'Trinity 3D.png' next to SetupTrinityUser.sh and re-run."
    echo "    looked in: $SCRIPT_DIR  and  $APP_DIR"
fi

# Show what was installed (helps debug "Invalid desktop entry")
echo "  Desktop files:"
file "$DESKTOP_DIR"/geofence-*.desktop 2>/dev/null || true
od -c "$DESKTOP_DIR/geofence-wifi-setup.desktop" | head -2 2>/dev/null || true
grep -n $'\r' "$DESKTOP_DIR"/geofence-*.desktop && echo "ERROR: CRLF still present" || echo "  line endings: LF OK"

# Quick smoke test (no display) — catch missing tkinter early
if ! sudo -u "$CUSTOMER_USER" "$PY3" -c "import tkinter" 2>/dev/null; then
    echo "WARNING: python3 tkinter not available for $CUSTOMER_USER — installing python3-tk..."
    apt-get install -y python3-tk || true
fi

echo "  If icon still says Invalid desktop entry:"
echo "    cat -A ~/Desktop/geofence-wifi-setup.desktop   # ^M means CRLF — re-run this script"
echo "    Right-click → Allow Launching"
echo "    Or run: $TOOLS_DIR/run-wifi-setup.sh"

# SSH: trinity keys only; geoserver unchanged (admin password/keys as you configured)
SSHD_DROPIN="/etc/ssh/sshd_config.d/geofence-trinity.conf"
mkdir -p /etc/ssh/sshd_config.d
cat > "$SSHD_DROPIN" <<EOF
# GeoFence: trinity uses SSH keys only (password locked)
# geoserver remains a normal admin account (not matched here)
Match User ${CUSTOMER_USER}
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AuthenticationMethods publickey
EOF
if systemctl is-active --quiet ssh 2>/dev/null || systemctl is-active --quiet sshd 2>/dev/null; then
    systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
fi

echo ""
echo "=== Done ==="
echo "  ADMIN:    $SERVICE_USER — login/SSH OK, owns $APP_DIR"
echo "  CUSTOMER: $CUSTOMER_USER — blank desktop password + autologin; SSH keys only"
echo "  App:      $APP_DIR → ${SERVICE_USER}:${SERVICE_USER} 750 (hidden from trinity)"
echo "  Tools:    $TOOLS_DIR"
echo ""
echo "  trinity desktop: blank password OK (or reboot for autologin)"
echo "  trinity SSH:     publickey only (password still disabled over SSH)"
echo "  trinity sudo:    systemctl start|stop|restart|status ${SERVICE_NAME}"
echo "                   Set Wifi Credentials + Service Monitor icons"
echo ""
echo "  trinity cannot:"
echo "    ls/read $APP_DIR"
echo ""
echo "Test:"
echo "  sudo -u $CUSTOMER_USER ls $APP_DIR          # should fail"
echo "  sudo -u $CUSTOMER_USER sudo -n systemctl status ${SERVICE_NAME}"
echo "  # admin still works:"
echo "  sudo -u $SERVICE_USER ls $APP_DIR           # should succeed"
