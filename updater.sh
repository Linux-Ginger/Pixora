#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — updater.sh
#  Draait als root via pkexec (GUI-updater). Doet alles wat install.sh
#  doet: apt-deps, git-pull, python pip packages, ownership-fix.
#  Geen ASCII-art of terminal-interactie — output gaat naar de GUI log.
# ─────────────────────────────────────────────

set -e

REPO_URL="https://github.com/Linux-Ginger/Pixora.git"

# User-home detecteren (omdat we als root draaien via pkexec)
if [ -n "$PKEXEC_UID" ]; then
    RUN_UID="$PKEXEC_UID"
elif [ -n "$SUDO_UID" ]; then
    RUN_UID="$SUDO_UID"
else
    RUN_UID="$(id -u)"
fi
TARGET_USER="$(getent passwd "$RUN_UID" | cut -d: -f1)"
TARGET_HOME="$(getent passwd "$RUN_UID" | cut -d: -f6)"
TARGET_GID="$(getent passwd "$RUN_UID" | cut -d: -f4)"

INSTALL_DIR="$TARGET_HOME/.local/share/pixora"
VERSION_FILE="$TARGET_HOME/.config/pixora/installed_version"

if ! command -v apt-get &> /dev/null; then
    echo "Fout: Pixora vereist Ubuntu of Debian."
    exit 1
fi

echo "[1/5] Systeem-dependencies controleren…"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    gir1.2-gudev-1.0 \
    git \
    ifuse \
    libimobiledevice-utils \
    usbmuxd \
    ffmpeg \
    python3-pip

echo "[2/5] WebKit typelib (voor kaart-weergave)…"
apt-get install -y -qq gir1.2-webkit-6.0 2>/dev/null || \
apt-get install -y -qq gir1.2-webkit2-4.1 2>/dev/null || true

echo "[3/5] Python packages (Pillow, imagehash, watchdog)…"
runuser -u "$TARGET_USER" -- python3 -m pip install -q \
    --break-system-packages \
    Pillow pillow-heif imagehash watchdog || true

echo "[4/5] Pixora ophalen van GitHub…"
if [ -d "$INSTALL_DIR/.git" ]; then
    runuser -u "$TARGET_USER" -- git -C "$INSTALL_DIR" fetch -q origin
    runuser -u "$TARGET_USER" -- git -C "$INSTALL_DIR" reset --hard origin/main -q
else
    rm -rf "$INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    chown "$RUN_UID:$TARGET_GID" "$(dirname "$INSTALL_DIR")"
    runuser -u "$TARGET_USER" -- git clone -q "$REPO_URL" "$INSTALL_DIR"
fi

echo "[5/5] Versie-marker + AppArmor profile + usbmuxd-service…"
mkdir -p "$(dirname "$VERSION_FILE")"
cp -f "$INSTALL_DIR/version.txt" "$VERSION_FILE"
chown -R "$RUN_UID:$TARGET_GID" "$INSTALL_DIR" "$(dirname "$VERSION_FILE")"

# AppArmor profile (voor WebKit sandbox op Ubuntu 24.04+)
if [ -d /etc/apparmor.d ]; then
    cat > /etc/apparmor.d/pixora << 'PROFILE_EOF'
abi <abi/4.0>,
include <tunables/global>

profile pixora flags=(unconfined) {
  userns,
  include if exists <local/pixora>
}
PROFILE_EOF
    systemctl reload apparmor 2>/dev/null || true
fi

# usbmuxd-service actief voor iPhone-import
systemctl enable --now usbmuxd 2>/dev/null || true

NEW_VERSION="$(cat "$INSTALL_DIR/version.txt")"
echo ""
echo "Klaar — Pixora is bijgewerkt naar versie $NEW_VERSION."
