#!/bin/bash
# Setup script for DodatekEZD – macOS

set -e

VENV_DIR="$HOME/.DodatekEzdVenv"
APP_DIR="$HOME/Library/Application Support/DodatekEZD"
DSS_APP_DIR="$HOME/Library/Application Support/DssWebApp"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/../app"
APP_BUNDLE="/Applications/DodatekEZD.app"
DSS_VERSION="6.3"
DSS_ZIP_URL="https://ec.europa.eu/cefdigital/artifact/repository/esignaturedss/eu/europa/ec/joinup/sd-dss/dss-demo-bundle/${DSS_VERSION}/dss-demo-bundle-${DSS_VERSION}.zip"

echo "=== DodatekEZD Setup for macOS ==="

# ── Check Homebrew ─────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    echo "ERROR: Homebrew not found. Install it from https://brew.sh and re-run."
    exit 1
fi

# ── Step 1: Detect Python version and install matching python-tk ───────────────
echo "[1/7] Detecting Python version..."
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "      Found Python $PYTHON_VERSION"

if brew list "python-tk@${PYTHON_VERSION}" &>/dev/null; then
    echo "      python-tk@${PYTHON_VERSION} already installed, skipping."
else
    brew install "python-tk@${PYTHON_VERSION}"
fi

if brew list podman &>/dev/null; then
    echo "      podman already installed, skipping."
else
    brew install podman
fi

# ── Step 2: Copy app files ─────────────────────────────────────────────────────
echo "[2/7] Copying app files to $APP_DIR..."
mkdir -p "$APP_DIR"
cp -r "$SOURCE_DIR/." "$APP_DIR/"

cat > "$APP_DIR/addin_paths.py" << EOF
addin_path = "$APP_DIR"
python_x86 = "$VENV_DIR"
downloads_folder = "$HOME/.DodatekEzdData/"
log_file = "$HOME/Library/Logs/DodatekEzd.log"
EOF
echo "      ✓ Files copied."
echo "      ✓ addin_paths.py created."

# ── Step 3: Create virtual environment and install packages ────────────────────
echo "[3/7] Creating venv: $VENV_DIR"
python3 -m venv --system-site-packages "$VENV_DIR"

source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install pikepdf zeep python-pkcs11 watchdog psutil cryptography python-docx odfpy striprtf pandas openpyxl xlrd
deactivate
echo "      ✓ Packages installed."

# ── Step 4: Register ezd:// protocol handler via AppleScript bundle ────────────
echo "[4/7] Registering ezd:// protocol handler..."

LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

# Unregister existing ezd:// handler
echo "      Checking for existing ezd:// handler..."
EXISTING_HANDLER=$(
    "$LSREGISTER" -dump 2>/dev/null \
    | grep -B 5 'ezd' \
    | grep 'path:' \
    | awk '{print $2}' \
    | head -1
)
if [ -n "$EXISTING_HANDLER" ] && [ "$EXISTING_HANDLER" != "$APP_BUNDLE" ]; then
    echo "      Unregistering existing handler: $EXISTING_HANDLER"
    "$LSREGISTER" -u "$EXISTING_HANDLER" 2>/dev/null || true
else
    echo "      No existing ezd:// handler found."
fi

[ -d "$APP_BUNDLE" ] && rm -rf "$APP_BUNDLE"

# Compile AppleScript bundle
TMP_AS="/tmp/DodatekEZD-$$.as"
cat > "$TMP_AS" << ASEOF
on open location this_URL
    do shell script "\"${VENV_DIR}/bin/python3\" \"${APP_DIR}/handler.py\" " & quoted form of this_URL & " >> \"${HOME}/Library/Logs/DodatekEzd.log\" 2>&1"
end open location

on run
end run
ASEOF

osacompile -o "$APP_BUNDLE" "$TMP_AS"
rm -f "$TMP_AS"
echo "      ✓ AppleScript bundle compiled."

# Patch Info.plist preserving osacompile structure
python3 - "$APP_BUNDLE" << 'PYEOF'
import plistlib, os, sys
plist_path = os.path.join(sys.argv[1], "Contents", "Info.plist")
with open(plist_path, "rb") as f:
    plist = plistlib.load(f)
plist["CFBundleName"]               = "DodatekEZD"
plist["CFBundleDisplayName"]        = "DodatekEZD"
plist["CFBundleIdentifier"]         = "pl.dodatekEZD.handler"
plist["CFBundleSignature"]          = "aplt"
plist["LSUIElement"]                = False
plist["OSAAppletShowStartupScreen"] = False
plist["CFBundleURLTypes"] = [{
    "CFBundleURLName":    "ezd handler",
    "CFBundleURLSchemes": ["ezd"],
}]
with open(plist_path, "wb") as f:
    plistlib.dump(plist, f)
print("      ✓ Info.plist updated.")
PYEOF

# Remove quarantine
xattr -cr "$APP_BUNDLE" 2>/dev/null || true

# Ad-hoc sign
codesign --force --deep --sign - "$APP_BUNDLE" && \
    echo "      ✓ App signed (ad-hoc)." || \
    echo "      ⚠  codesign failed — may still work."

"$LSREGISTER" -f "$APP_BUNDLE"
echo "      ✓ ezd:// handler registered."

# ── Step 5: Download DSS bundle ZIP to ~/Downloads ────────────────────────────
echo "[5/7] Downloading DSS demo bundle..."
DSS_ZIP_CACHE="$HOME/Downloads/dss-demo-bundle-${DSS_VERSION}.zip"

if [ -f "$DSS_ZIP_CACHE" ]; then
    echo "      ✓ Already downloaded: $DSS_ZIP_CACHE – skipping."
else
    curl -L "$DSS_ZIP_URL" -o "$DSS_ZIP_CACHE"
    echo "      ✓ Downloaded to $DSS_ZIP_CACHE"
fi

# ── Step 6: Install DSS to $DSS_APP_DIR and build image ───────────────────────
echo "[6/7] Installing DSS to $DSS_APP_DIR and building container image..."
mkdir -p "$DSS_APP_DIR"
cp "$DSS_ZIP_CACHE" "$DSS_APP_DIR/"

# Clean up previous TSP config to avoid stale placeholder/file coexistence
rm -f "$DSS_APP_DIR/tsp-config.xml" "$DSS_APP_DIR/tsp-config.xml.placeholder"

TSP_CONFIG="$HOME/Downloads/tsp-config.xml"
if [ -f "$TSP_CONFIG" ]; then
    cp "$TSP_CONFIG" "$DSS_APP_DIR/"
    echo "      ✓ TSP config found – will be included in image."
else
    touch "$DSS_APP_DIR/tsp-config.xml.placeholder"
    echo "      ✓ No TSP config found – using DSS default."
fi

cp "$SCRIPT_DIR/../dss/Dockerfile" "$DSS_APP_DIR/"
echo "      ✓ Files installed to $DSS_APP_DIR"

# Remove previous image to avoid accumulating untagged layers
podman rmi "dss:${DSS_VERSION}" 2>/dev/null || true

# Ensure Podman machine is initialized and running
if ! podman machine list --format '{{.Name}}' 2>/dev/null | grep -q .; then
    echo "      Initializing Podman machine..."
    podman machine init
fi

if ! podman info &>/dev/null; then
    echo "      Starting Podman machine..."
    podman machine start || true
fi
echo "      ✓ Podman machine running."

podman build -t "dss:${DSS_VERSION}" "$DSS_APP_DIR"
echo "      ✓ Image dss:${DSS_VERSION} built."

# Remove ZIP from build context – no longer needed
rm -f "$DSS_APP_DIR/dss-demo-bundle-${DSS_VERSION}.zip"
echo "      ✓ Build context cleaned up."

# ── Step 7: Run DSS container ─────────────────────────────────────────────────
echo "[7/7] Starting DSS container..."
podman rm -f dss 2>/dev/null || true
podman run -d -p 8080:8080 --name dss "dss:${DSS_VERSION}"
echo "      ✓ Container 'dss' running on http://localhost:8080"

echo ""
echo "✓ Setup complete!"
echo ""
