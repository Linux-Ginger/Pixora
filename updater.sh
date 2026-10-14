#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — updater.sh
#  Runs as root via pkexec. Emits "STEP:<key>:<label>" markers so the
#  GUI updater can show per-step spinner/check state.
# ─────────────────────────────────────────────

set -e

REPO_URL="https://github.com/Linux-Ginger/Pixora.git"

# Detect user-home (we run as root via pkexec)
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

# Sanity-check: refuse if no real home, else rm -rf could hit a root-dir.
if [ -z "$TARGET_HOME" ] || [ "$TARGET_HOME" = "/" ] || [ ! -d "$TARGET_HOME" ]; then
    echo "Fout: kan home-directory niet bepalen voor UID=$RUN_UID" >&2
    exit 1
fi
case "$TARGET_HOME" in
    /home/*|/root|/Users/*) ;;
    *) echo "Fout: onverwachte home-directory $TARGET_HOME — weigert uit voorzorg" >&2; exit 1 ;;
esac

INSTALL_DIR="$TARGET_HOME/.local/share/pixora"
VERSION_FILE="$TARGET_HOME/.config/pixora/installed_version"

if ! command -v apt-get &> /dev/null; then
    case "${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}" in
        nl*|NL*) echo "Fout: Pixora vereist Ubuntu of Debian." ;;
        de*|DE*) echo "Fehler: Pixora benötigt Ubuntu oder Debian." ;;
        fr*|FR*) echo "Erreur : Pixora nécessite Ubuntu ou Debian." ;;
        *)       echo "Error: Pixora requires Ubuntu or Debian." ;;
    esac
    exit 1
fi

step() { echo "STEP:$1:$2"; }
step_done() { echo "STEP:$1:DONE"; }

# Run as the real user, with their own HOME (pkexec's environment points
# HOME at /root, which made pip silently install nowhere).
as_user() { runuser -u "$TARGET_USER" -- env HOME="$TARGET_HOME" "$@"; }

step clone "Fetching Pixora from GitHub"
command -v git >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null 2>&1
if [ ! -d "$INSTALL_DIR/.git" ]; then
    # No checkout yet: move any old copy aside instead of deleting it (it may
    # hold user data, e.g. edit originals from older versions).
    if [ -e "$INSTALL_DIR" ]; then
        mv "$INSTALL_DIR" "$INSTALL_DIR.old-$(date +%s)"
    fi
    mkdir -p "$(dirname "$INSTALL_DIR")"
    chown "$RUN_UID:$TARGET_GID" "$(dirname "$INSTALL_DIR")"
    as_user git clone -q "$REPO_URL" "$INSTALL_DIR" >/dev/null 2>&1
fi
as_user git -C "$INSTALL_DIR" fetch -q --tags --force origin >/dev/null 2>&1
# Install the newest published release (what "Check for updates" compares
# against); only fall back to main while no release exists yet.
TARGET_REF="$(as_user git -C "$INSTALL_DIR" tag -l 'v*' --sort=-v:refname | head -n1)"
[ -n "$TARGET_REF" ] || TARGET_REF="origin/main"
as_user git -C "$INSTALL_DIR" reset --hard "$TARGET_REF" -q >/dev/null 2>&1
step_done clone

step deps "Installing dependencies"
if [ -f "$INSTALL_DIR/deps/install-system-deps.sh" ]; then
    bash "$INSTALL_DIR/deps/install-system-deps.sh" >/dev/null 2>&1 || \
        echo "STEP:deps:some system packages failed to install"
fi
PY_PKGS="Pillow pillow-heif imagehash watchdog"
if [ -f "$INSTALL_DIR/deps/python-packages.txt" ]; then
    PY_PKGS="$(grep -v '^\s*#' "$INSTALL_DIR/deps/python-packages.txt" | grep -v '^\s*$' | tr '\n' ' ')"
fi
# shellcheck disable=SC2086
as_user python3 -m pip install -q --user --break-system-packages $PY_PKGS \
    >/dev/null 2>&1 || echo "STEP:deps:pip failed"
step_done deps

step finalize "Configuration and services"
mkdir -p "$(dirname "$VERSION_FILE")"
# Version lives in the code (viewer/version.py), not a separate version.txt.
APP_VER="$(grep -oP '__version__\s*=\s*"\K[^"]+' "$INSTALL_DIR/viewer/version.py" 2>/dev/null || true)"
printf '%s' "$APP_VER" > "$VERSION_FILE"

# Compile .po -> .mo; remove old .mo first so we don't silently fall back
# to a stale compilation when msgfmt fails. Errors on stdout for UI-log.
if command -v msgfmt >/dev/null 2>&1; then
    for po in "$INSTALL_DIR"/locale/*/LC_MESSAGES/pixora.po; do
        [ -f "$po" ] || continue
        mo="${po%.po}.mo"
        rm -f "$mo"
        if ! msgfmt -o "$mo" "$po"; then
            echo "STEP:finalize:msgfmt FAILED for $po"
        fi
        if [ ! -f "$mo" ]; then
            echo "STEP:finalize:missing .mo after compile: $mo"
        fi
    done
else
    echo "STEP:finalize:msgfmt not installed — translations will fall back to source strings"
fi

chown -R "$RUN_UID:$TARGET_GID" "$INSTALL_DIR" "$(dirname "$VERSION_FILE")"

# Regenerate .desktop so Icon= path tracks asset-dir renames over time.
DESKTOP_DIR="$TARGET_HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/pixora.desktop"
LAUNCHER="$TARGET_HOME/.local/bin/pixora"
ICON_FILE="$INSTALL_DIR/assets/logos/pixora-icon.svg"

# Regenerate launcher so existing installs pick up the GSK crash-respawn logic.
if [ -f "$LAUNCHER" ]; then
    mkdir -p "$(dirname "$LAUNCHER")"
    cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/bin/bash
MAIN=$INSTALL_DIR/viewer/main.py
GSK_SENTINEL="\${HOME}/.cache/pixora/.gsk_pending"
run_pixora() {
  if command -v aa-exec >/dev/null 2>&1 && [ -r /etc/apparmor.d/pixora ]; then
    aa-exec -p pixora -- python3 "\$MAIN" "\$@"
  else
    python3 "\$MAIN" "\$@"
  fi
}
run_pixora "\$@"
rc=\$?
if [ \$rc -ne 0 ] && [ -f "\$GSK_SENTINEL" ]; then
  run_pixora "\$@"
  rc=\$?
fi
exit \$rc
LAUNCHER_EOF
    chmod 0755 "$LAUNCHER"
    chown "$RUN_UID:$TARGET_GID" "$LAUNCHER"
fi

if [ -f "$ICON_FILE" ] && [ -f "$LAUNCHER" ]; then
    mkdir -p "$DESKTOP_DIR"
    cat > "$DESKTOP_FILE" <<DESKTOP_EOF
[Desktop Entry]
Name=Pixora
GenericName=Photo & Video Manager
GenericName[nl]=Foto & Video Manager
GenericName[de]=Foto- & Video-Manager
GenericName[fr]=Gestionnaire de photos et vidéos
Comment=Photo & video manager by LinuxGinger
Comment[nl]=Foto & video manager door LinuxGinger
Comment[de]=Foto- & Video-Manager von LinuxGinger
Comment[fr]=Gestionnaire de photos et vidéos par LinuxGinger
Exec=$LAUNCHER
Icon=$ICON_FILE
Terminal=false
Type=Application
Categories=Graphics;Photography;
StartupWMClass=com.linuxginger.pixora
DESKTOP_EOF
    chown "$RUN_UID:$TARGET_GID" "$DESKTOP_FILE"
    # Refresh cache so GNOME picks up new icon without logout
    runuser -u "$TARGET_USER" -- update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

step_done finalize
