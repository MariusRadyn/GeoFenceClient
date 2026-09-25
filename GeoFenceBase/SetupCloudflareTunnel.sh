#!/usr/bin/env bash
# Setup Cloudflare Tunnel for GeoFence MQTT WebSockets (Flutter HTTPS web app).
#
# Browser connects:  wss://<hostname>/mqtt   (Cloudflare cert on :443)
# Tunnel forwards:   http://127.0.0.1:9001   (Mosquitto plain WebSockets)
#
# Prerequisites:
#   1) Domain on Cloudflare DNS (e.g. trinityglobal.co.za)
#   2) This Pi can reach the internet
#   3) Mosquitto listening on 9001 (python MqttCredentials.py --setup)
#
# Usage:
#   ./SetupCloudflareTunnel.sh --hostname geobase-farm1.mqtt.trinityglobal.co.za
#   ./SetupCloudflareTunnel.sh --hostname=geobase-farm1.mqtt.trinityglobal.co.za
#   ./SetupCloudflareTunnel.sh geobase-farm1.mqtt.trinityglobal.co.za
#   ./SetupCloudflareTunnel.sh --auto
#   ./SetupCloudflareTunnel.sh --auto --domain mqtt.trinityglobal.co.za
#
# After setup, the hostname is saved to ~/Secure/mqtt_wss_host.txt and pushed
# to Firestore clients/{bluetoothName}.mqttWssHost for the web app.

set -euo pipefail

HOSTNAME="${HOSTNAME:-}"
HOSTNAME="${MQTT_WSS_HOST:-$HOSTNAME}"
TUNNEL_NAME="geofence-mqtt"
# Used when auto-building from Bluetooth name: <slug>.<DEFAULT_DOMAIN>
DEFAULT_DOMAIN="${MQTT_WSS_DOMAIN:-mqtt.trinityglobal.co.za}"

APP_USER="${SUDO_USER:-$USER}"
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
  APP_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
  APP_USER="$SUDO_USER"
else
  APP_HOME="$HOME"
fi
SECURE_DIR="$APP_HOME/Secure"
HOST_FILE="$SECURE_DIR/mqtt_wss_host.txt"
CF_DIR="$APP_HOME/.cloudflared"
CONFIG_FILE="$CF_DIR/config.yml"

usage() {
  cat <<EOF
Setup Cloudflare Tunnel for GeoFence MQTT WebSockets.

Usage:
  $0 --hostname geobase-farm1.mqtt.trinityglobal.co.za
  $0 --hostname=geobase-farm1.mqtt.trinityglobal.co.za
  $0 geobase-farm1.mqtt.trinityglobal.co.za
  HOSTNAME=geobase-farm1.mqtt.trinityglobal.co.za $0

  # Auto from Bluetooth alias + domain:
  $0 --auto
  $0 --auto --domain mqtt.trinityglobal.co.za

Options:
  --hostname HOST   Public DNS name (Cloudflare zone)
  --tunnel-name N   Tunnel name (default: geofence-mqtt)
  --auto            Build hostname from Bluetooth alias
  --domain DOMAIN   Parent domain for --auto (default: $DEFAULT_DOMAIN)
  -h, --help        Show this help
EOF
  exit 1
}

slugify() {
  echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g'
}

bt_alias() {
  bluetoothctl show 2>/dev/null | awk -F': ' '/Alias:/ {print $2; exit}' || true
}

AUTO=0
while [ $# -gt 0 ]; do
  case "$1" in
    --hostname=*)
      HOSTNAME="${1#--hostname=}"
      shift
      ;;
    --hostname)
      if [ $# -lt 2 ]; then
        echo "ERROR: --hostname needs a value"
        usage
      fi
      HOSTNAME="$2"
      shift 2
      ;;
    --tunnel-name=*)
      TUNNEL_NAME="${1#--tunnel-name=}"
      shift
      ;;
    --tunnel-name)
      if [ $# -lt 2 ]; then
        echo "ERROR: --tunnel-name needs a value"
        usage
      fi
      TUNNEL_NAME="$2"
      shift 2
      ;;
    --domain=*)
      DEFAULT_DOMAIN="${1#--domain=}"
      shift
      ;;
    --domain)
      if [ $# -lt 2 ]; then
        echo "ERROR: --domain needs a value"
        usage
      fi
      DEFAULT_DOMAIN="$2"
      shift 2
      ;;
    --auto)
      AUTO=1
      shift
      ;;
    -h|--help)
      usage
      ;;
    -*)
      echo "Unknown arg: $1"
      usage
      ;;
    *)
      # Positional hostname
      if [ -z "$HOSTNAME" ]; then
        HOSTNAME="$1"
      else
        echo "Unknown arg: $1"
        usage
      fi
      shift
      ;;
  esac
done

# Re-read saved host if still empty
if [ -z "$HOSTNAME" ] && [ -f "$HOST_FILE" ]; then
  HOSTNAME="$(tr -d ' \t\r\n' < "$HOST_FILE" || true)"
  if [ -n "$HOSTNAME" ]; then
    echo "Using existing host from $HOST_FILE: $HOSTNAME"
  fi
fi

if [ -z "$HOSTNAME" ] || [ "$AUTO" -eq 1 ]; then
  ALIAS="$(bt_alias)"
  SLUG="$(slugify "${ALIAS:-geobase}")"
  if [ -z "$SLUG" ]; then
    SLUG="geobase"
  fi
  if [ -z "$HOSTNAME" ] || [ "$AUTO" -eq 1 ]; then
    HOSTNAME="${SLUG}.${DEFAULT_DOMAIN}"
    echo "Auto hostname from Bluetooth '${ALIAS:-unknown}': $HOSTNAME"
  fi
fi

HOSTNAME="$(echo "$HOSTNAME" | tr -d '[:space:]')"
HOSTNAME="${HOSTNAME#https://}"
HOSTNAME="${HOSTNAME#http://}"
HOSTNAME="${HOSTNAME#wss://}"
HOSTNAME="${HOSTNAME#ws://}"
HOSTNAME="${HOSTNAME%%/*}"
HOSTNAME="${HOSTNAME%:443}"

if [ -z "$HOSTNAME" ]; then
  echo "ERROR: hostname is empty"
  echo "Pass one explicitly, e.g.:"
  echo "  $0 --hostname geobase-farm1.mqtt.trinityglobal.co.za"
  echo "Or: $0 --auto --domain mqtt.trinityglobal.co.za"
  exit 1
fi

# Basic hostname sanity
if ! echo "$HOSTNAME" | grep -Eq '^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$'; then
  echo "ERROR: invalid hostname: $HOSTNAME"
  exit 1
fi

echo "=== GeoFence Cloudflare MQTT tunnel ==="
echo "User:     $APP_USER"
echo "Home:     $APP_HOME"
echo "Hostname: $HOSTNAME"
echo "Tunnel:   $TUNNEL_NAME"
echo

# ---------- Install cloudflared ----------
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Installing cloudflared..."
  ARCH="$(uname -m)"
  case "$ARCH" in
    aarch64|arm64) CF_ARCH="arm64" ;;
    armv7l|armhf)  CF_ARCH="arm" ;;
    x86_64|amd64)  CF_ARCH="amd64" ;;
    *) echo "ERROR: unsupported arch $ARCH"; exit 1 ;;
  esac
  TMP="$(mktemp -d)"
  curl -fsSL \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${CF_ARCH}" \
    -o "$TMP/cloudflared"
  chmod +x "$TMP/cloudflared"
  sudo mv "$TMP/cloudflared" /usr/local/bin/cloudflared
  rm -rf "$TMP"
fi
cloudflared --version

# ---------- Login (once per Cloudflare account) ----------
if [ ! -f "$CF_DIR/cert.pem" ]; then
  echo
  echo "Cloudflare login required (opens a browser URL)."
  echo "Run as $APP_USER (not root) if this fails."
  sudo -u "$APP_USER" mkdir -p "$CF_DIR"
  sudo -u "$APP_USER" cloudflared tunnel login
fi

# ---------- Create tunnel if missing ----------
TUNNEL_LIST="$(sudo -u "$APP_USER" cloudflared tunnel list 2>/dev/null || true)"
if ! echo "$TUNNEL_LIST" | grep -qw "$TUNNEL_NAME"; then
  echo "Creating tunnel: $TUNNEL_NAME"
  sudo -u "$APP_USER" cloudflared tunnel create "$TUNNEL_NAME"
else
  echo "Tunnel already exists: $TUNNEL_NAME"
fi

TUNNEL_ID="$(sudo -u "$APP_USER" cloudflared tunnel list | awk -v n="$TUNNEL_NAME" '$2==n {print $1; exit}')"
if [ -z "$TUNNEL_ID" ]; then
  echo "ERROR: could not resolve tunnel id for $TUNNEL_NAME"
  sudo -u "$APP_USER" cloudflared tunnel list
  exit 1
fi
echo "Tunnel ID: $TUNNEL_ID"

CRED_FILE="$CF_DIR/${TUNNEL_ID}.json"
if [ ! -f "$CRED_FILE" ]; then
  echo "ERROR: missing credentials file: $CRED_FILE"
  exit 1
fi

# ---------- DNS route ----------
echo "Routing DNS $HOSTNAME → tunnel $TUNNEL_NAME"
sudo -u "$APP_USER" cloudflared tunnel route dns --overwrite-dns "$TUNNEL_NAME" "$HOSTNAME" \
  || sudo -u "$APP_USER" cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME"

# ---------- Write config (proxy to local Mosquitto WS :9001) ----------
sudo -u "$APP_USER" mkdir -p "$CF_DIR" "$SECURE_DIR"
chmod 700 "$SECURE_DIR" 2>/dev/null || true

sudo -u "$APP_USER" tee "$CONFIG_FILE" >/dev/null <<EOF
# GeoFence MQTT via Cloudflare Tunnel
# Browser: wss://${HOSTNAME}/mqtt
# Origin:  Mosquitto WebSockets on 127.0.0.1:9001
tunnel: ${TUNNEL_ID}
credentials-file: ${CRED_FILE}

ingress:
  - hostname: ${HOSTNAME}
    service: http://127.0.0.1:9001
  - service: http_status:404
EOF

echo "$HOSTNAME" | sudo -u "$APP_USER" tee "$HOST_FILE" >/dev/null
chmod 600 "$HOST_FILE" 2>/dev/null || true
echo "Saved hostname → $HOST_FILE"

# ---------- systemd service ----------
SERVICE_FILE="/etc/systemd/system/cloudflared-geofence.service"
sudo tee "$SERVICE_FILE" >/dev/null <<EOF
[Unit]
Description=Cloudflare Tunnel (GeoFence MQTT WSS)
After=network-online.target mosquitto.service
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
ExecStart=/usr/local/bin/cloudflared --config ${CONFIG_FILE} tunnel run ${TUNNEL_ID}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable cloudflared-geofence.service
sudo systemctl restart cloudflared-geofence.service
sleep 2
sudo systemctl --no-pager --full status cloudflared-geofence.service || true

# ---------- Push hostname to Firestore (best-effort) ----------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR/MqttCredentials.py" ]; then
  echo "Pushing mqttWssHost to Firestore..."
  if [ -x "$APP_HOME/venv312/bin/python" ]; then
    PY="$APP_HOME/venv312/bin/python"
  else
    PY="python3"
  fi
  sudo -u "$APP_USER" "$PY" "$SCRIPT_DIR/MqttCredentials.py" --push-wss-host || true
fi

echo
echo "=== Done ==="
echo "Public MQTT WSS:  wss://${HOSTNAME}/mqtt"
echo "Local origin:     ws://127.0.0.1:9001/mqtt"
echo
echo "Web app (HTTPS): tap Request IP Address, then Connect — it will use"
echo "  ${HOSTNAME}  (no certificate warning)."
echo
echo "Android app still uses LAN IP on TCP 1883."
echo
echo "Check tunnel:  sudo journalctl -u cloudflared-geofence -f"
echo "Check host:    cat $HOST_FILE"
