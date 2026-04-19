#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — updater.sh
#  Draait als root via pkexec. Emit "STEP:<key>:<label>" markers
#  zodat de GUI-updater per stap een spinner/check kan tonen.
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
    case "${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}" in
        nl*|NL*) echo "Fout: Pixora vereist Ubuntu of Debian." ;;
        *)       echo "Error: Pixora requires Ubuntu or Debian." ;;
    esac
    exit 1
fi

step() { echo "STEP:$1:$2"; }
step_done() { echo "STEP:$1:DONE"; }

step apt "Systeem packages controleren"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gudev-1.0 \
    git ifuse libimobiledevice-utils usbmuxd ffmpeg python3-pip \
    gettext >/dev/null 2>&1
step_done apt

step webkit "WebKit typelib installeren"
apt-get install -y -qq gir1.2-webkit-6.0 >/dev/null 2>&1 || \
apt-get install -y -qq gir1.2-webkit2-4.1 >/dev/null 2>&1 || true
step_done webkit

step pip "Python packages installeren"
runuser -u "$TARGET_USER" -- python3 -m pip install -q \
    --break-system-packages \
    Pillow pillow-heif imagehash watchdog >/dev/null 2>&1 || true
step_done pip

step clone "Pixora ophalen van GitHub"
if [ -d "$INSTALL_DIR/.git" ]; then
    runuser -u "$TARGET_USER" -- git -C "$INSTALL_DIR" fetch -q origin >/dev/null 2>&1
    runuser -u "$TARGET_USER" -- git -C "$INSTALL_DIR" reset --hard origin/main -q >/dev/null 2>&1
else
    rm -rf "$INSTALL_DIR"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    chown "$RUN_UID:$TARGET_GID" "$(dirname "$INSTALL_DIR")"
    runuser -u "$TARGET_USER" -- git clone -q "$REPO_URL" "$INSTALL_DIR" >/dev/null 2>&1
fi
step_done clone

step finalize "Configuratie en services"
mkdir -p "$(dirname "$VERSION_FILE")"
cp -f "$INSTALL_DIR/version.txt" "$VERSION_FILE"

# Compileer .po → .mo voor alle vertalingen
if command -v msgfmt >/dev/null 2>&1; then
    for po in "$INSTALL_DIR"/locale/*/LC_MESSAGES/pixora.po; do
        [ -f "$po" ] || continue
        mo="${po%.po}.mo"
        msgfmt -o "$mo" "$po" 2>/dev/null || true
    done
fi

chown -R "$RUN_UID:$TARGET_GID" "$INSTALL_DIR" "$(dirname "$VERSION_FILE")"

if [ -d /etc/apparmor.d ]; then
    cat > /etc/apparmor.d/pixora << 'PROFILE_EOF'
abi <abi/4.0>,
include <tunables/global>

profile pixora flags=(unconfined) {
  userns,
  include if exists <local/pixora>
}
PROFILE_EOF
    systemctl reload apparmor >/dev/null 2>&1 || true
fi

systemctl enable --now usbmuxd >/dev/null 2>&1 || true
step_done finalize
