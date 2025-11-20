#!/bin/bash
# ------------------------------------------
# Idempotent script to install pyenv, Python 3.10, and a virtualenv
# Can be run multiple times safely
# ------------------------------------------

set -e  # Exit on any error

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

# ---------- 5. Install Python 3.10 ----------
if pyenv versions --bare | grep -q "^3.10.16\$"; then
    echo "Python 3.10.16 already installed."
else
    echo "Installing Python 3.10.16..."
    pyenv install 3.10.16
fi

pyenv global 3.10.16

# ---------- 6. Create virtual environment ----------
VENV_DIR="$HOME/venv310"
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
pip install --upgrade pip

# ---------- 9. Install firebase-admin ----------
if pip show firebase-admin &>/dev/null; then
    echo "firebase-admin already installed."
else
    echo "Installing firebase-admin..."
    pip install firebase-admin
fi

# ---------- 10. Install MQQT ----------
echo "Installing MQQT"

sudo apt update
sudo apt install mosquitto mosquitto-clients
sudo systemctl enable mosquitto
pip install paho-mqtt
echo -e "\nlistener 1883 0.0.0.0\nallow_anonymous true" | sudo tee -a /etc/mosquitto/mosquitto.conf
sudo systemctl restart mosquitto

# ---------- 11. Install Bleak ----------
echo "Installing Bleak"
python3 -m pip install bleak

# ---------- 12. Install Firebase ----------
echo "Installing Bleak"
python3 -m pip install firebase_admin

# ---------- 13. Set Bluetooth Name ----------
echo "Set Bluetooth Name"
MAC=$(hciconfig hci0 | grep "BD Address" | awk '{print $3}')
MAC_CLEAN=${MAC//:/}
BLE_NAME="geoclient_${MAC_CLEAN}"
echo "$BLE_NAME"
sudo bluetoothctl system-alias "$BLE_NAME" 


echo "Python version in venv:"
python --version
echo "Pip version:"
pip --version
echo "Setup complete! Virtual environment ready at $VENV_DIR"
