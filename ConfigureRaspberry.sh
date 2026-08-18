#!/bin/bash
# ------------------------------------------
# Idempotent script to install pyenv, Python 3.12, and a virtualenv
# Can be run multiple times safely
# ------------------------------------------

set -e  # Exit on any error

PYTHON_VERSION="3.12.12"

# Where this script lives (canonical app location when files are already there)
ORIGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORIGIN_DIR="${ORIGIN_DIR//$'\r'/}"

# Real user home (not /root when mistakenly run with sudo)
if [ -n "${SUDO_USER:-}" ] && [ "$(id -u)" -eq 0 ]; then
    APP_USER="$SUDO_USER"
else
    APP_USER="$(id -un)"
fi
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
APP_HOME="${APP_HOME:-$HOME}"
APP_HOME="${APP_HOME//$'\r'/}"

VENV_DIR="${APP_HOME}/venv312"

# Prefer the folder this script is in if the app is already there
if [ -f "$ORIGIN_DIR/InstallService.sh" ] || [ -f "$ORIGIN_DIR/GeoFenceClient.py" ]; then
    APP_DIR="$ORIGIN_DIR"
else
    APP_DIR="${APP_HOME}/GeoFenceBase"
fi

echo "User: $APP_USER"
echo "Home: $APP_HOME"
echo "App:  $APP_DIR"

# ---------- 0. App directory ----------
mkdir -p "$APP_DIR"
APP_FILES=(
    GeoFenceClient.py
    Settings.py
    WifiCredentials.py
    MqttCredentials.py
    MqttService.py
    SqlLite.py
    ConfigureRaspberry.sh
    InstallService.sh
    geofence.service
    geofence.conf.example
    50-geofence-networkmanager.rules
)

# Always gather missing app files into APP_DIR (from script dir and/or user home)
echo "Ensuring app files are in $APP_DIR ..."
for f in "${APP_FILES[@]}"; do
    if [ -e "$APP_DIR/$f" ]; then
        continue
    fi
    for src in "$ORIGIN_DIR/$f" "$APP_HOME/$f"; do
        if [ -e "$src" ] && [ "$src" != "$APP_DIR/$f" ]; then
            mv "$src" "$APP_DIR/"
            echo "  moved $f ← $src"
            break
        fi
    done
done
if [ ! -e "$APP_DIR/Debug" ]; then
    for src in "$ORIGIN_DIR/Debug" "$APP_HOME/Debug"; do
        if [ -d "$src" ] && [ "$src" != "$APP_DIR/Debug" ]; then
            mv "$src" "$APP_DIR/"
            echo "  moved Debug/ ← $src"
            break
        fi
    done
fi

SCRIPT_DIR="$APP_DIR"

# ---------- 1. Install dependencies ----------
echo "Installing dependencies..."
sudo apt update

DEPENDENCIES=(build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
libsqlite3-dev wget curl llvm libncurses5-dev libncursesw5-dev xz-utils tk-dev \
libffi-dev liblzma-dev git python3-venv)

for pkg in "${DEPENDENCIES[@]}"; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        echo "Installing $pkg..."
        sudo apt install -y "$pkg"
    else
        echo "$pkg already installed."
    fi
done

# ---------- 2. Install pyenv ----------
if [ -d "$HOME/.pyenv" ]; then
    echo "pyenv already installed."
else
    echo "Installing pyenv..."
    curl https://pyenv.run | bash
fi

# ---------- 3. Update ~/.bashrc ----------
BASHRC="$HOME/.bashrc"
if ! grep -q 'pyenv init' "$BASHRC"; then
    echo 'export PATH="$HOME/.pyenv/bin:$PATH"' >> "$BASHRC"
    echo 'eval "$(pyenv init --path)"' >> "$BASHRC"
    echo 'eval "$(pyenv virtualenv-init -)"' >> "$BASHRC"
    echo ".bashrc updated with pyenv."
else
    echo ".bashrc already configured for pyenv."
fi

# ---------- 4. Load pyenv into current session ----------
export PATH="$HOME/.pyenv/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv virtualenv-init -)"

# ---------- 5. Install Python 3.12 ----------
if pyenv versions --bare | grep -q "^${PYTHON_VERSION}\$"; then
    echo "Python ${PYTHON_VERSION} already installed."
else
    echo "Installing Python ${PYTHON_VERSION}..."
    pyenv install "$PYTHON_VERSION"
fi

pyenv global "$PYTHON_VERSION"

# ---------- 6. Create virtual environment ----------
if [ -d "$VENV_DIR" ]; then
    echo "Virtual environment already exists at $VENV_DIR"
else
    echo "Creating virtual environment at $VENV_DIR..."
    python -m venv "$VENV_DIR"
fi

# ---------- 7. Activate virtual environment and upgrade pip ----------
echo "Activating virtual environment..."
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"

# ---------- 8. Upgrade pip ----------
echo "Upgrading pip..."
"$VENV_DIR/bin/pip" install --upgrade pip

# ---------- 9. Install Python packages into this venv ----------
REQ_FILE="$APP_DIR/requirements.txt"
if [ -f "$REQ_FILE" ]; then
    echo "Installing from $REQ_FILE ..."
    "$VENV_DIR/bin/pip" install -r "$REQ_FILE"
else
    echo "Installing firebase-admin, bleak, paho-mqtt, cryptography ..."
    "$VENV_DIR/bin/pip" install firebase-admin bleak paho-mqtt cryptography
fi
"$VENV_DIR/bin/python" -c "import firebase_admin, bleak, paho.mqtt.client; print('venv packages OK:', firebase_admin.__file__)"

# ---------- 10. Install MQTT (Mosquitto) with authentication ----------
echo "Installing MQTT (Mosquitto)..."

sudo apt update
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable mosquitto

# Secure dir (matches GeoFenceClient ~/Secure)
mkdir -p "$APP_HOME/Secure"
chmod 700 "$APP_HOME/Secure"

# Enable username/password auth; disables anonymous access
MQTT_CREDS_SCRIPT="$APP_DIR/MqttCredentials.py"
if [ -f "$MQTT_CREDS_SCRIPT" ]; then
    echo "Running MqttCredentials.py --setup ..."
    python "$MQTT_CREDS_SCRIPT" --setup
    echo "MQTT broker configured with authentication (allow_anonymous false)."
    echo "Credentials file: $APP_HOME/Secure/mqtt_credentials.json"
else
    echo "ERROR: MqttCredentials.py not found at $MQTT_CREDS_SCRIPT"
    echo "Place app files in $APP_DIR and re-run."
    exit 1
fi
echo "Optional: restrict LAN access with:"
echo "  sudo ufw allow from 192.168.0.0/16 to any port 1883"
echo "  sudo ufw allow from 192.168.0.0/16 to any port 9001 comment 'MQTT WebSockets'"

# ---------- 13. Set Bluetooth Name ----------
echo "Set Bluetooth Name"
MAC=$(hciconfig hci0 | grep "BD Address" | awk '{print $3}')
MAC_CLEAN=${MAC//:/}
BLE_NAME="geobase_${MAC_CLEAN}"
echo "$BLE_NAME"
sudo bluetoothctl system-alias "$BLE_NAME" 

# ---------- 14. Make secure Dir ----------
mkdir -p "$APP_HOME/Secure"
chmod 700 "$APP_HOME/Secure"

# ---------- 14b. NetworkManager privileges (nmcli wifi verify/connect) ----------
echo "Granting NetworkManager privileges to $APP_USER ..."
sudo usermod -aG netdev "$APP_USER" || true
POLKIT_SRC="$APP_DIR/50-geofence-networkmanager.rules"
POLKIT_DEST="/etc/polkit-1/rules.d/50-geofence-networkmanager.rules"
if [ -f "$POLKIT_SRC" ]; then
    sed -i 's/\r$//' "$POLKIT_SRC" 2>/dev/null || true
    sudo cp "$POLKIT_SRC" "$POLKIT_DEST"
    sudo chmod 644 "$POLKIT_DEST"
    echo "  installed $POLKIT_DEST"
else
    echo "WARNING: $POLKIT_SRC not found — WiFi nmcli may fail with insufficient privileges."
fi

# ---------- 15. Install GeoFence systemd service (start on boot) ----------
INSTALL_SERVICE=""
for cand in "$APP_DIR/InstallService.sh" "$ORIGIN_DIR/InstallService.sh" "$APP_HOME/InstallService.sh"; do
    if [ -f "$cand" ]; then
        INSTALL_SERVICE="$cand"
        break
    fi
done
if [ -n "$INSTALL_SERVICE" ]; then
    # Keep the canonical copy under APP_DIR
    if [ "$INSTALL_SERVICE" != "$APP_DIR/InstallService.sh" ]; then
        mv "$INSTALL_SERVICE" "$APP_DIR/InstallService.sh"
        INSTALL_SERVICE="$APP_DIR/InstallService.sh"
        echo "  moved InstallService.sh → $APP_DIR"
    fi
    echo "Installing GeoFence systemd service..."
    # WinSCP/Windows uploads often use CRLF; that breaks the #! shebang
    # ("cannot execute: required file not found"). Strip CR and run via bash.
    sed -i 's/\r$//' "$INSTALL_SERVICE" "$APP_DIR/ConfigureRaspberry.sh" 2>/dev/null || true
    chmod +x "$INSTALL_SERVICE"
    bash "$INSTALL_SERVICE"
else
    echo "WARNING: InstallService.sh not found — skip boot service install."
    echo "  looked in: $APP_DIR"
    echo "             $ORIGIN_DIR"
    echo "             $APP_HOME"
    ls -la "$APP_DIR" || true
    echo "Later: place InstallService.sh in $APP_DIR and run it."
fi

echo "Python version in venv:"
python --version
echo "Pip version:"
pip --version
echo "Setup complete!"
echo "  App dir: $APP_DIR"
echo "  Venv:    $VENV_DIR"
if [ -d "$APP_HOME/venv310" ]; then
    echo "Note: old ~/venv310 is unused; you can remove it after confirming the service works:"
    echo "  rm -rf $APP_HOME/venv310"
fi
echo "Edit $APP_HOME/Secure/geofence.conf then: sudo systemctl start geofence"
