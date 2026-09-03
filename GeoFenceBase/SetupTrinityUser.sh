#!/bin/bash
# Create locked-down "trinity" customer user + move GUIs to /opt/geofence-tools.
#
# Layout:
#   /home/geoserver/GeoFenceBase  → root:geoserver 750 (trinity cannot read)
#   /home/geoserver/Secure        → geoserver:geoserver 700
#   /opt/geofence-tools           → root:root, GUIs world-executable; save-wifi root-only via sudo
#   trinity                       → no password (SSH keys only); desktop icons; passwordless systemctl
#
# Run as root (or with sudo) after ConfigureRaspberry.sh / InstallService.sh:
#   sudo bash SetupTrinityUser.sh
#
# SSH keys: edit ssh_keys (one public key per line), then re-run this script.

set -e

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

# Ensure service user exists
if ! id "$SERVICE_USER" &>/dev/null; then
    echo "ERROR: user $SERVICE_USER does not exist."
    exit 1
fi

echo "=== Creating customer user: $CUSTOMER_USER ==="
if ! id "$CUSTOMER_USER" &>/dev/null; then
    adduser --disabled-password --gecos "GeoFence customer" "$CUSTOMER_USER"
else
    echo "User $CUSTOMER_USER already exists."
fi

# No password login (SSH keys only)
passwd -d "$CUSTOMER_USER" 2>/dev/null || true
passwd -l "$CUSTOMER_USER" 2>/dev/null || true
usermod -s /bin/bash "$CUSTOMER_USER"

# Groups: journal read + netdev (optional); never geoserver group
usermod -aG adm,systemd-journal,netdev "$CUSTOMER_USER" 2>/dev/null || \
    usermod -aG adm,netdev "$CUSTOMER_USER" 2>/dev/null || true

CUSTOMER_HOME="$(getent passwd "$CUSTOMER_USER" | cut -d: -f6)"
mkdir -p "$CUSTOMER_HOME/.ssh"
chmod 700 "$CUSTOMER_HOME/.ssh"
AUTH_KEYS="$CUSTOMER_HOME/.ssh/authorized_keys"

if [ -n "$SSH_KEYS_FILE" ]; then
    echo "Installing SSH keys from $SSH_KEYS_FILE"
    # Strip comments/blank lines and Windows CR; write authorized_keys
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

# Service account: no interactive login for customers
if getent passwd "$SERVICE_USER" | grep -qv nologin; then
    echo "Locking interactive login for $SERVICE_USER (service only)..."
    usermod -s /usr/sbin/nologin "$SERVICE_USER" || true
fi

echo "=== Installing tools to $TOOLS_DIR ==="
mkdir -p "$TOOLS_DIR"
# Prefer copies from app dir (may still be writable during install)
SRC_DIR="$SCRIPT_DIR"
[ -f "$APP_DIR/WifiSetupGui.py" ] && SRC_DIR="$APP_DIR"

for f in WifiSetupGui.py JournalGui.py; do
    if [ -f "$SRC_DIR/$f" ]; then
        sed -i 's/\r$//' "$SRC_DIR/$f" 2>/dev/null || true
        cp "$SRC_DIR/$f" "$TOOLS_DIR/$f"
    elif [ -f "$TOOLS_DIR/$f" ]; then
        echo "  keep existing $TOOLS_DIR/$f"
    else
        echo "ERROR: missing $f (looked in $SRC_DIR)"
        exit 1
    fi
done

if [ -f "$SRC_DIR/tools/save_wifi.py" ]; then
    sed -i 's/\r$//' "$SRC_DIR/tools/save_wifi.py" "$SRC_DIR/tools/save-wifi" 2>/dev/null || true
    cp "$SRC_DIR/tools/save_wifi.py" "$TOOLS_DIR/save_wifi.py"
    cp "$SRC_DIR/tools/save-wifi" "$TOOLS_DIR/save-wifi"
elif [ -f "$APP_DIR/tools/save_wifi.py" ]; then
    cp "$APP_DIR/tools/save_wifi.py" "$TOOLS_DIR/save_wifi.py"
    cp "$APP_DIR/tools/save-wifi" "$TOOLS_DIR/save-wifi"
else
    echo "ERROR: tools/save_wifi.py not found"
    exit 1
fi

chmod 755 "$TOOLS_DIR"
chmod 755 "$TOOLS_DIR/WifiSetupGui.py" "$TOOLS_DIR/JournalGui.py"
chmod 755 "$TOOLS_DIR/save-wifi"
chmod 644 "$TOOLS_DIR/save_wifi.py"
chown -R root:root "$TOOLS_DIR"

# tkinter
if ! dpkg -s python3-tk &>/dev/null; then
    apt-get update -qq
    apt-get install -y python3-tk
fi

echo "=== Locking down $APP_DIR (root:geoserver 750) ==="
# Service user must still run the app; trinity must not read it.
chown -R root:"$SERVICE_USER" "$APP_DIR"
find "$APP_DIR" -type d -exec chmod 750 {} \;
find "$APP_DIR" -type f -exec chmod 640 {} \;
# scripts that root/geoserver may execute
chmod 750 "$APP_DIR"/*.sh 2>/dev/null || true

# Secure secrets — service user only
mkdir -p "$SECURE_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$SECURE_DIR"
chmod 700 "$SECURE_DIR"
find "$SECURE_DIR" -type f -exec chmod 600 {} \; 2>/dev/null || true

# Venv: service user can run; trinity cannot browse
if [ -d "/home/${SERVICE_USER}/venv312" ]; then
    chown -R root:"$SERVICE_USER" "/home/${SERVICE_USER}/venv312"
    chmod -R 750 "/home/${SERVICE_USER}/venv312"
fi

# Home of geoserver: block trinity from listing
chmod 750 "/home/${SERVICE_USER}"
chown "$SERVICE_USER:$SERVICE_USER" "/home/${SERVICE_USER}"
# But APP_DIR is under home and owned root:geoserver — ensure home allows group traverse
# home 750 geoserver:geoserver means only geoserver enters; systemd User=geoserver OK.
# root can always access. Good.

echo "=== Sudoers for $CUSTOMER_USER ==="
SUDOERS="/etc/sudoers.d/geofence-trinity"
cat > "$SUDOERS" <<EOF
# GeoFence customer user — passwordless service control + WiFi save helper
Cmnd_Alias GEOFENCE_CTL = /bin/systemctl start ${SERVICE_NAME}, /bin/systemctl stop ${SERVICE_NAME}, /bin/systemctl restart ${SERVICE_NAME}, /bin/systemctl status ${SERVICE_NAME}
Cmnd_Alias GEOFENCE_WIFI = ${TOOLS_DIR}/save-wifi
${CUSTOMER_USER} ALL=(root) NOPASSWD: GEOFENCE_CTL, GEOFENCE_WIFI
EOF
chmod 440 "$SUDOERS"
visudo -cf "$SUDOERS"

# Remove old geoserver-oriented wifi sudoers if present (optional cleanup)
if [ -f /etc/sudoers.d/geofence-wifi-setup ]; then
    rm -f /etc/sudoers.d/geofence-wifi-setup
    echo "Removed old /etc/sudoers.d/geofence-wifi-setup"
fi

echo "=== Desktop icons for $CUSTOMER_USER ==="
DESKTOP_DIR="${CUSTOMER_HOME}/Desktop"
APPS_DIR="${CUSTOMER_HOME}/.local/share/applications"
mkdir -p "$DESKTOP_DIR" "$APPS_DIR"

PY3="$(command -v python3)"

cat > "$DESKTOP_DIR/geofence-wifi-setup.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=GeoFence WiFi Setup
Comment=Enter WiFi credentials and restart GeoFence
Exec=${PY3} ${TOOLS_DIR}/WifiSetupGui.py
Path=${TOOLS_DIR}
Icon=network-wireless
Terminal=false
Categories=Network;Settings;
StartupNotify=true
EOF

cat > "$DESKTOP_DIR/geofence-journal.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=GeoFence Journal
Comment=Live journalctl for GeoFence service
Exec=${PY3} ${TOOLS_DIR}/JournalGui.py
Path=${TOOLS_DIR}
Icon=utilities-system-monitor
Terminal=false
Categories=System;Monitor;
StartupNotify=true
EOF

cp "$DESKTOP_DIR/geofence-wifi-setup.desktop" "$APPS_DIR/"
cp "$DESKTOP_DIR/geofence-journal.desktop" "$APPS_DIR/"
chmod +x "$DESKTOP_DIR"/*.desktop "$APPS_DIR"/geofence-*.desktop
chown -R "$CUSTOMER_USER:$CUSTOMER_USER" "$DESKTOP_DIR" "$APPS_DIR"

if command -v gio >/dev/null 2>&1; then
    sudo -u "$CUSTOMER_USER" gio set "$DESKTOP_DIR/geofence-wifi-setup.desktop" metadata::trusted true 2>/dev/null || true
    sudo -u "$CUSTOMER_USER" gio set "$DESKTOP_DIR/geofence-journal.desktop" metadata::trusted true 2>/dev/null || true
fi

# SSH: prefer keys for trinity (do not force global PasswordAuthentication=no)
SSHD_DROPIN="/etc/ssh/sshd_config.d/geofence-trinity.conf"
mkdir -p /etc/ssh/sshd_config.d
cat > "$SSHD_DROPIN" <<EOF
# GeoFence: trinity uses SSH keys only (password locked above)
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
echo "  Customer user:  $CUSTOMER_USER (password locked — SSH keys only)"
echo "  App (hidden):   $APP_DIR  → root:${SERVICE_USER} 750"
echo "  Tools (OK):     $TOOLS_DIR"
echo "  Desktop:        $DESKTOP_DIR"
echo ""
echo "  trinity can:"
echo "    sudo systemctl start|stop|restart|status ${SERVICE_NAME}"
echo "    sudo ${TOOLS_DIR}/save-wifi"
echo "    run WiFi Setup + Journal GUIs"
echo ""
echo "  trinity cannot:"
echo "    ls/read $APP_DIR"
echo ""
echo "Test:"
echo "  sudo -u $CUSTOMER_USER ls $APP_DIR          # should fail"
echo "  sudo -u $CUSTOMER_USER sudo -n systemctl status ${SERVICE_NAME}"
echo "  sudo -u $CUSTOMER_USER ${PY3} ${TOOLS_DIR}/JournalGui.py"
