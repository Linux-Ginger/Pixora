#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — update.sh
#  Non-interactive updater, bedoeld om via pkexec
#  aangeroepen te worden door de GUI-updater.
#  Voor een volledige installatie in terminal: gebruik install.sh.
# ─────────────────────────────────────────────

set -e

REPO_URL="https://github.com/Linux-Ginger/Pixora.git"

# Als we via pkexec/sudo draaien, bepaal het oorspronkelijke user-home
if [ -n "$PKEXEC_UID" ]; then
    TARGET_HOME=$(getent passwd "$PKEXEC_UID" | cut -d: -f6)
elif [ -n "$SUDO_UID" ]; then
    TARGET_HOME=$(getent passwd "$SUDO_UID" | cut -d: -f6)
else
    TARGET_HOME="$HOME"
fi

INSTALL_DIR="$TARGET_HOME/.local/share/pixora"
VERSION_FILE="$TARGET_HOME/.config/pixora/installed_version"

# Check Ubuntu/Debian
if ! command -v apt-get &> /dev/null; then
    echo "Fout: Pixora vereist Ubuntu of Debian (apt-get niet gevonden)."
    exit 1
fi

echo "[1/4] Dependencies controleren…"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    gir1.2-gudev-1.0 \
    git 2>&1 | grep -v -E "^(Reading|Building|Selecting|Preparing|Unpacking|Setting up|Processing)" || true

echo ""
echo "[2/4] Pixora ophalen van GitHub…"
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" fetch origin main 2>&1
    git -C "$INSTALL_DIR" reset --hard origin/main 2>&1
else
    rm -rf "$INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR" 2>&1
fi

echo ""
echo "[3/4] Versie-marker bijwerken…"
mkdir -p "$(dirname "$VERSION_FILE")"
cp -f "$INSTALL_DIR/version.txt" "$VERSION_FILE"

# Fix ownership als we als root draaien
if [ -n "$PKEXEC_UID" ] || [ -n "$SUDO_UID" ]; then
    UID_TO_USE="${PKEXEC_UID:-$SUDO_UID}"
    GID_TO_USE=$(getent passwd "$UID_TO_USE" | cut -d: -f4)
    chown -R "$UID_TO_USE:$GID_TO_USE" "$INSTALL_DIR"
    chown -R "$UID_TO_USE:$GID_TO_USE" "$(dirname "$VERSION_FILE")"
fi

NEW_VERSION=$(cat "$INSTALL_DIR/version.txt")
echo ""
echo "[4/4] Klaar! Pixora is bijgewerkt naar versie $NEW_VERSION."
