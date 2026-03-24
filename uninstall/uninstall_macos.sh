#!/bin/bash
# Uninstall script for DodatekEZD – macOS

set -e

VENV_DIR="$HOME/.DodatekEzdVenv"
APP_DIR="$HOME/Library/Application Support/DodatekEZD"
DSS_APP_DIR="$HOME/Library/Application Support/DssWebApp"
APP_BUNDLE="/Applications/DodatekEZD.app"

echo "=== DodatekEZD Uninstall for macOS ==="

# ── Step 1: Remove venv ────────────────────────────────────────────────────────
if [ -d "$VENV_DIR" ]; then
    echo "[1/5] Removing venv: $VENV_DIR"
    rm -rf "$VENV_DIR"
    echo "      ✓ Removed."
else
    echo "[1/5] Venv not found at $VENV_DIR, skipping."
fi

# ── Step 2: Remove app files ───────────────────────────────────────────────────
if [ -d "$APP_DIR" ]; then
    echo "[2/5] Removing app files: $APP_DIR"
    rm -rf "$APP_DIR"
    echo "      ✓ Removed."
else
    echo "[2/5] App folder not found at $APP_DIR, skipping."
fi

# ── Step 3: Remove .app bundle and unregister handler ─────────────────────────
if [ -d "$APP_BUNDLE" ]; then
    echo "[3/5] Removing DodatekEZD.app and unregistering ezd:// handler..."
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister         -u "$APP_BUNDLE" 2>/dev/null || true
    rm -rf "$APP_BUNDLE"
    echo "      ✓ Handler removed."
else
    echo "[3/5] DodatekEZD.app not found, skipping."
fi

# ── Step 4: Remove downloads_folder ──────────────────────────────────────────
if [ -d "$HOME/.DodatekEzdData" ]; then
    echo "[4/6] Removing downloads folder: $HOME/.DodatekEzdData"
    rm -rf "$HOME/.DodatekEzdData"
    echo "      ✓ Removed."
else
    echo "[4/6] Downloads folder not found, skipping."
fi

# ── Step 5: Remove DSS app dir ────────────────────────────────────────────────
if [ -d "$DSS_APP_DIR" ]; then
    echo "[4/5] Removing DSS app dir: $DSS_APP_DIR"
    rm -rf "$DSS_APP_DIR"
    echo "      ✓ Removed."
else
    echo "[5/6] DSS app dir not found, skipping."
fi

# ── Step 5: Remove DSS container and image ────────────────────────────────────
echo "[6/6] Stopping and removing DSS container and image..."
if command -v podman &>/dev/null; then
    podman stop dss 2>/dev/null && echo "      ✓ Container stopped." || echo "      Container 'dss' not running."
    podman rm   dss 2>/dev/null && echo "      ✓ Container removed." || echo "      Container 'dss' not found."
    podman rmi  dss:6.3 2>/dev/null && echo "      ✓ Image dss:6.3 removed." || echo "      Image 'dss:6.3' not found."
else
    echo "      podman not found – skipping container cleanup."
fi

echo ""
read -p "Remove python-tk via Homebrew as well? (y/N): " REMOVE_TK
if [[ "$REMOVE_TK" =~ ^[Yy]$ ]]; then
    if command -v brew &>/dev/null; then
        PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if brew list "python-tk@${PYTHON_VERSION}" &>/dev/null; then
            brew uninstall "python-tk@${PYTHON_VERSION}"
            echo "✓ python-tk@${PYTHON_VERSION} removed."
        fi
    fi
fi

read -p "Remove podman via Homebrew as well? (y/N): " REMOVE_PODMAN
if [[ "$REMOVE_PODMAN" =~ ^[Yy]$ ]]; then
    if command -v brew &>/dev/null; then
        if brew list podman &>/dev/null; then
            brew uninstall podman
            echo "✓ podman removed."
        else
            echo "      podman not found via Homebrew, skipping."
        fi
    fi
fi

echo ""
echo "✓ Uninstall complete!"
