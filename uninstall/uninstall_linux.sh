#!/bin/bash
# Uninstall script for DodatekEZD – Linux (Debian, Ubuntu, Fedora, RHEL)

set -e

VENV_DIR="$HOME/.DodatekEzdVenv"
APP_DIR="$HOME/.local/share/DodatekEZD"
DSS_APP_DIR="$HOME/.local/share/DssWebApp"
DESKTOP_FILE="$HOME/.local/share/applications/ezd-handler.desktop"

echo "=== DodatekEZD Uninstall for Linux ==="

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

# ── Step 3: Remove ezd:// protocol handler ────────────────────────────────────
if [ -f "$DESKTOP_FILE" ]; then
    echo "[3/5] Removing ezd:// protocol handler..."
    rm -f "$DESKTOP_FILE"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    echo "      ✓ Handler removed."
else
    echo "[3/5] Desktop file not found, skipping."
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
read -p "Remove python3-tk system package as well? (y/N): " REMOVE_TK
if [[ "$REMOVE_TK" =~ ^[Yy]$ ]]; then
    if command -v apt &>/dev/null; then
        sudo apt remove -y python3-tk
    elif command -v dnf &>/dev/null; then
        sudo dnf remove -y python3-tkinter
    fi
    echo "✓ python3-tk removed."
fi

read -p "Remove podman as well? (y/N): " REMOVE_PODMAN
if [[ "$REMOVE_PODMAN" =~ ^[Yy]$ ]]; then
    if command -v apt &>/dev/null; then
        sudo apt remove -y podman
    elif command -v dnf &>/dev/null; then
        sudo dnf remove -y podman
    fi
    echo "✓ podman removed."
fi

read -p "Remove unzip as well? (y/N): " REMOVE_UNZIP
if [[ "$REMOVE_UNZIP" =~ ^[Yy]$ ]]; then
    if command -v apt &>/dev/null; then
        sudo apt remove -y unzip
    elif command -v dnf &>/dev/null; then
        sudo dnf remove -y unzip
    fi
    echo "✓ unzip removed."
fi

echo ""
echo "✓ Uninstall complete!"
