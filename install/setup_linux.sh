#!/bin/bash
# Setup script for DodatekEZD – Linux (Debian, Ubuntu, Fedora, RHEL)

set -e

VENV_DIR="$HOME/.DodatekEzdVenv"
APP_DIR="$HOME/.local/share/DodatekEZD"
DSS_APP_DIR="$HOME/.local/share/DssWebApp"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/../app"
DESKTOP_FILE="$HOME/.local/share/applications/ezd-handler.desktop"
DSS_VERSION="6.3"
DSS_ZIP_URL="https://ec.europa.eu/cefdigital/artifact/repository/esignaturedss/eu/europa/ec/joinup/sd-dss/dss-demo-bundle/${DSS_VERSION}/dss-demo-bundle-${DSS_VERSION}.zip"

echo "=== DodatekEZD Setup for Linux ==="

# ── Step 1: Detect distro and install python3-tk ──────────────────────────────
if command -v apt &>/dev/null; then
    echo "[1/7] Detected Debian/Ubuntu – installing python3-tk via apt..."
    sudo apt update -qq
    sudo apt install -y python3-tk python3-venv unzip podman

elif command -v dnf &>/dev/null; then
    echo "[1/7] Detected Fedora/RHEL – installing python3-tkinter via dnf..."
    sudo dnf install -y python3-tkinter python3-virtualenv unzip podman

else
    echo "ERROR: Unsupported package manager. Install python3-tk manually, then re-run."
    exit 1
fi

# ── Step 2: Copy app files ─────────────────────────────────────────────────────
echo "[2/7] Copying app files to $APP_DIR..."
mkdir -p "$APP_DIR"
cp -r "$SOURCE_DIR/." "$APP_DIR/"

cat > "$APP_DIR/addin_paths.py" << EOF
addin_path = "$APP_DIR"
python_x86 = "$VENV_DIR"
downloads_folder = "$HOME/.DodatekEzdData/"
log_file = "$HOME/.cache/DodatekEzd.log"
EOF
echo "      ✓ Files copied."
echo "      ✓ addin_paths.py created."

# ── Step 3: Create virtual environment and install packages ────────────────────
echo "[3/7] Creating venv: $VENV_DIR"
python3 -m venv --system-site-packages "$VENV_DIR"

source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install pikepdf zeep python-pkcs11 watchdog
deactivate
echo "      ✓ Packages installed."

# ── Step 4: Register ezd:// protocol handler ──────────────────────────────────
echo "[4/7] Registering ezd:// protocol handler..."
mkdir -p "$(dirname "$DESKTOP_FILE")"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Name=DodatekEZD
GenericName=DodatekEZD
Comment=Handle URL Scheme ezd:
Exec=$VENV_DIR/bin/python3 $APP_DIR/handler.py %u
Terminal=false
Type=Application
MimeType=x-scheme-handler/ezd;
Icon=application-x-executable
Categories=Utility;
EOF

xdg-mime default ezd-handler.desktop x-scheme-handler/ezd
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo "      ✓ ezd:// handler registered."

# ── Step 5: Download DSS bundle ZIP to ~/Downloads ────────────────────────────
echo "[5/7] Downloading DSS demo bundle..."
DSS_ZIP_CACHE="$HOME/Downloads/dss-demo-bundle-${DSS_VERSION}.zip"

if [ -f "$DSS_ZIP_CACHE" ]; then
    echo "      ✓ Already downloaded: $DSS_ZIP_CACHE – skipping."
elif command -v curl &>/dev/null; then
    curl -L "$DSS_ZIP_URL" -o "$DSS_ZIP_CACHE"
    echo "      ✓ Downloaded to $DSS_ZIP_CACHE"
elif command -v wget &>/dev/null; then
    wget -q "$DSS_ZIP_URL" -O "$DSS_ZIP_CACHE"
    echo "      ✓ Downloaded to $DSS_ZIP_CACHE"
else
    echo "ERROR: Neither curl nor wget found. Install one and re-run."
    exit 1
fi

# ── Step 6: Install DSS to $DSS_APP_DIR and build image ───────────────────────
echo "[6/7] Installing DSS to $DSS_APP_DIR and building container image..."
mkdir -p "$DSS_APP_DIR"
cp "$DSS_ZIP_CACHE" "$DSS_APP_DIR/"

TSP_CONFIG="$HOME/Downloads/tsp-config.xml"
if [ -f "$TSP_CONFIG" ]; then
    cp "$TSP_CONFIG" "$DSS_APP_DIR/"
fi

cp "$SCRIPT_DIR/../dss/Dockerfile" "$DSS_APP_DIR/"
echo "      ✓ Files installed to $DSS_APP_DIR"

podman build -t "dss:${DSS_VERSION}" "$DSS_APP_DIR"
echo "      ✓ Image dss:${DSS_VERSION} built."

# ── Step 7: Run DSS container ─────────────────────────────────────────────────
echo "[7/7] Starting DSS container..."
podman run -d -p 8080:8080 --name dss "dss:${DSS_VERSION}"
echo "      ✓ Container 'dss' running on http://localhost:8080"

echo ""
echo "✓ Setup complete!"
echo ""
echo "To activate the environment manually, run:"
echo "  source $VENV_DIR/bin/activate"
