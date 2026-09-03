#!/bin/bash
# Install GeoFenceClient as a systemd service that starts on boot.
# Run on the Raspberry Pi (from anywhere):
#   chmod +x InstallService.sh && ./InstallService.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-$USER}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
APP_DIR="${APP_HOME}/GeoFenceBase"
VENV_PYTHON="${APP_HOME}/venv312/bin/python"
SECURE_DIR="${APP_HOME}/Secure"
CONFIG_FILE="${SECURE_DIR}/geofence.conf"
SERVICE_NAME="geofence"
SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}.service"
EXAMPLE_CONF="${APP_DIR}/geofence.conf.example"
TEMPLATE_SERVICE="${APP_DIR}/geofence.service"

mkdir -p "$APP_DIR"
chown "$APP_USER:$APP_USER" "$APP_DIR"

# If this script was run from home (or elsewhere), move app files into APP_DIR
if [ "$SCRIPT_DIR" != "$APP_DIR" ]; then
    APP_FILES=(
        GeoFenceClient.py Settings.py WifiCredentials.py MqttCredentials.py
        MqttService.py SqlLite.py ConfigureRaspberry.sh InstallService.sh
        geofence.service geofence.conf.example 50-geofence-networkmanager.rules
        requirements.txt WifiSetupGui.py geofence-wifi-setup.desktop
    )
    echo "Moving app files into $APP_DIR ..."
    for f in "${APP_FILES[@]}"; do
        if [ -e "$SCRIPT_DIR/$f" ] && [ ! -e "$APP_DIR/$f" ]; then
            mv "$SCRIPT_DIR/$f" "$APP_DIR/"
            echo "  moved $f"
        fi
    done
fi

if [ ! -x "$VENV_PYTHON" ]; then
    echo "ERROR: Python venv not found at $VENV_PYTHON"
    echo "Run ConfigureRaspberry.sh first."
    exit 1
fi

if [ ! -f "$APP_DIR/GeoFenceClient.py" ]; then
    echo "ERROR: GeoFenceClient.py not found in $APP_DIR"
    exit 1
fi

if [ ! -f "$TEMPLATE_SERVICE" ]; then
    echo "ERROR: geofence.service not found in $APP_DIR"
    exit 1
fi

mkdir -p "$SECURE_DIR"
chmod 700 "$SECURE_DIR"
chown "$APP_USER:$APP_USER" "$SECURE_DIR"

if [ ! -f "$CONFIG_FILE" ]; then
    if [ -f "$EXAMPLE_CONF" ]; then
        cp "$EXAMPLE_CONF" "$CONFIG_FILE"
    else
        cat > "$CONFIG_FILE" <<'EOF'
{
  "wifi": false,
  "verbose": false,
  "mqtt": false,
  "newcreds": false
}
EOF
    fi
    chmod 600 "$CONFIG_FILE"
    chown "$APP_USER:$APP_USER" "$CONFIG_FILE"
    echo "Created $CONFIG_FILE — edit options before relying on boot start."
else
    echo "Config already exists: $CONFIG_FILE"
fi

echo "Installing systemd unit as $SERVICE_DEST"
# Strip Windows CRLF — systemd treats that as a bad unit file setting
sed -i 's/\r$//' "$TEMPLATE_SERVICE"
sudo cp "$TEMPLATE_SERVICE" "$SERVICE_DEST"
sudo sed -i 's/\r$//' "$SERVICE_DEST"
sudo chmod 644 "$SERVICE_DEST"

if ! sudo systemd-analyze verify "$SERVICE_DEST"; then
    echo "ERROR: unit file failed verification:"
    sudo cat -A "$SERVICE_DEST" | head -40
    exit 1
fi

# Allow geoserver to create/up WiFi connections via nmcli
echo "Granting NetworkManager privileges to $APP_USER ..."
sudo usermod -aG netdev "$APP_USER" || true
POLKIT_SRC="$APP_DIR/50-geofence-networkmanager.rules"
POLKIT_DEST="/etc/polkit-1/rules.d/50-geofence-networkmanager.rules"
if [ -f "$POLKIT_SRC" ]; then
    sed -i 's/\r$//' "$POLKIT_SRC" 2>/dev/null || true
    sudo cp "$POLKIT_SRC" "$POLKIT_DEST"
    sudo chmod 644 "$POLKIT_DEST"
    echo "  installed $POLKIT_DEST"
fi

# Passwordless restart for WiFi Setup desktop app
SUDOERS_FILE="/etc/sudoers.d/geofence-wifi-setup"
echo "Installing sudoers rule for service restart: $SUDOERS_FILE"
echo "$APP_USER ALL=(ALL) NOPASSWD: /bin/systemctl restart $SERVICE_NAME, /bin/systemctl start $SERVICE_NAME, /bin/systemctl stop $SERVICE_NAME, /bin/systemctl status $SERVICE_NAME" \
    | sudo tee "$SUDOERS_FILE" >/dev/null
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -cf "$SUDOERS_FILE" >/dev/null

# Desktop icon for WiFi Setup GUI
DESKTOP_SRC="$APP_DIR/geofence-wifi-setup.desktop"
DESKTOP_DIR="${APP_HOME}/Desktop"
APPS_DIR="${APP_HOME}/.local/share/applications"
mkdir -p "$DESKTOP_DIR" "$APPS_DIR"
if [ -f "$DESKTOP_SRC" ]; then
    sed -i 's/\r$//' "$DESKTOP_SRC" 2>/dev/null || true
    TMP_DESKTOP="$(mktemp)"
    sed \
        -e "s|PLACEHOLDER_PYTHON|${VENV_PYTHON}|g" \
        -e "s|PLACEHOLDER_APP_DIR|${APP_DIR}|g" \
        "$DESKTOP_SRC" > "$TMP_DESKTOP"
    cp "$TMP_DESKTOP" "$DESKTOP_DIR/geofence-wifi-setup.desktop"
    cp "$TMP_DESKTOP" "$APPS_DIR/geofence-wifi-setup.desktop"
    rm -f "$TMP_DESKTOP"
    chmod +x "$DESKTOP_DIR/geofence-wifi-setup.desktop" "$APPS_DIR/geofence-wifi-setup.desktop"
    chown "$APP_USER:$APP_USER" "$DESKTOP_DIR/geofence-wifi-setup.desktop" "$APPS_DIR/geofence-wifi-setup.desktop"
    # Mark trusted on Raspberry Pi OS / LXDE / GNOME so double-click works
    if command -v gio >/dev/null 2>&1; then
        sudo -u "$APP_USER" gio set "$DESKTOP_DIR/geofence-wifi-setup.desktop" metadata::trusted true 2>/dev/null || true
    fi
    echo "  Desktop icon: $DESKTOP_DIR/geofence-wifi-setup.desktop"
else
    echo "WARNING: $DESKTOP_SRC not found — skip desktop icon."
fi

# tkinter for WifiSetupGui (system package; pyenv builds need tk-dev which ConfigureRaspberry installs)
if ! dpkg -s python3-tk &>/dev/null; then
    echo "Installing python3-tk for WiFi Setup GUI..."
    sudo apt install -y python3-tk || true
fi

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"

echo ""
echo "Service installed and enabled for boot."
echo "  App:     $APP_DIR"
echo "  Config:  $CONFIG_FILE"
echo "  Unit:    $SERVICE_DEST"
echo "  WiFi UI: $DESKTOP_DIR/geofence-wifi-setup.desktop"
echo ""
echo "Edit config, then:"
echo "  sudo systemctl start $SERVICE_NAME"
echo "  sudo systemctl status $SERVICE_NAME"
echo "  journalctl -u $SERVICE_NAME -f"
echo ""
echo "NOTE: \"newcreds\": true is one-shot — after WiFi creds are saved it is"
echo "set back to false in $CONFIG_FILE automatically."
echo "Desktop: use \"GeoFence WiFi Setup\" instead of --newcreds."
