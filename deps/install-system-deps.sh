#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — deps/install-system-deps.sh
#  Installs the system packages Pixora needs. Run as root
#  (sudo from install.sh, pkexec from the installer/updater).
#  by LinuxGinger
# ─────────────────────────────────────────────
#
# Usage: install-system-deps.sh [--check]
#   --check   only list missing required packages (no root needed), exit 1 if any

set -e

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

read_list() {
    grep -v '^\s*#' "$1" | grep -v '^\s*$' | tr -d ' \t'
}

is_installed() {
    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed"
}

REQUIRED="$(read_list "$HERE/apt-packages.txt")"
OPTIONAL="$(read_list "$HERE/apt-optional.txt")"

if [ "$1" = "--check" ]; then
    missing=""
    for p in $REQUIRED; do
        is_installed "$p" || missing="$missing $p"
    done
    if ! is_installed gir1.2-webkit-6.0 && ! is_installed gir1.2-webkit2-4.1; then
        missing="$missing gir1.2-webkit-6.0"
    fi
    if [ -n "$missing" ]; then
        echo "missing:$missing"
        exit 1
    fi
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "install-system-deps.sh must run as root" >&2
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# Refresh the package lists once, so a fresh system finds everything.
apt-get update -qq >/dev/null 2>&1 || true

# shellcheck disable=SC2086
apt-get install -y -qq $REQUIRED

for p in $OPTIONAL; do
    apt-get install -y -qq "$p" >/dev/null 2>&1 || true
done

# Map view: WebKitGTK typelib — 6.0 (GTK 4) preferred, 4.1 as fallback.
apt-get install -y -qq gir1.2-webkit-6.0 >/dev/null 2>&1 || \
    apt-get install -y -qq gir1.2-webkit2-4.1 >/dev/null 2>&1 || true

# usbmuxd is normally started by udev when an iPhone is plugged in;
# enabling it is harmless where the unit is "static".
systemctl enable --now usbmuxd >/dev/null 2>&1 || true

# Ubuntu 24.04+ blocks unprivileged user namespaces through AppArmor, which
# WebKit's sandbox (the map) needs. Grant it to Pixora only — never loosen the
# global policy. Skipped where AppArmor 4 isn't there (the profile needs it).
if [ -d /etc/apparmor.d ] && [ -e /etc/apparmor.d/abi/4.0 ]; then
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

exit 0
