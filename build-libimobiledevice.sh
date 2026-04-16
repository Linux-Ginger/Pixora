#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — build-libimobiledevice.sh
#  Bouwt de nieuwste libimobiledevice suite van source
#  voor Ubuntu 24.04 (vereist voor iOS 17+)
#  by LinuxGinger
# ─────────────────────────────────────────────

set -e

GREEN='\033[0;32m'
ORANGE='\033[0;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

PREFIX=/usr/local
BUILD_DIR=$(mktemp -d /tmp/libimobiledevice-build.XXXXXX)
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PREFIX/lib/x86_64-linux-gnu/pkgconfig:$PKG_CONFIG_PATH"
export LD_LIBRARY_PATH="$PREFIX/lib:$LD_LIBRARY_PATH"

echo ""
echo -e "${ORANGE}${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${ORANGE}${BOLD}  libimobiledevice builder voor Pixora${NC}"
echo -e "${ORANGE}${BOLD}═══════════════════════════════════════════════${NC}"
echo ""

# ── Stap 1: Build-dependencies installeren ──
echo -e "${ORANGE}[1/9] Build-dependencies installeren…${NC}"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    build-essential \
    pkg-config \
    git \
    autoconf \
    automake \
    libtool-bin \
    libusb-1.0-0-dev \
    libssl-dev \
    libfuse3-dev \
    libcurl4-openssl-dev \
    udev \
    2>/dev/null
echo -e "${GREEN}  ✓ Dependencies geïnstalleerd${NC}"

# ── Stop bestaande usbmuxd ──
echo -e "${ORANGE}  Bestaande usbmuxd stoppen…${NC}"
sudo systemctl stop usbmuxd 2>/dev/null || true

cd "$BUILD_DIR"

build_lib() {
    local name=$1
    local step=$2
    local total=9

    echo ""
    echo -e "${ORANGE}[$step/$total] $name bouwen…${NC}"

    if [ -d "$name" ]; then
        rm -rf "$name"
    fi

    git clone --depth 1 "https://github.com/libimobiledevice/$name.git" 2>/dev/null
    cd "$name"

    ./autogen.sh --prefix="$PREFIX" > /dev/null 2>&1
    make -j$(nproc) > /dev/null 2>&1
    sudo make install > /dev/null 2>&1

    cd "$BUILD_DIR"
    echo -e "${GREEN}  ✓ $name geïnstalleerd${NC}"
}

# ── Stap 2-8: Libraries bouwen in juiste volgorde ──
build_lib "libplist"                2
build_lib "libimobiledevice-glue"   3
build_lib "libusbmuxd"              4
build_lib "libtatsu"                5
build_lib "libimobiledevice"        6
build_lib "usbmuxd"                 7
build_lib "ifuse"                   8

# ── Stap 9: Opruimen en activeren ──
echo ""
echo -e "${ORANGE}[9/9] Activeren…${NC}"
sudo ldconfig
sudo systemctl daemon-reload 2>/dev/null || true
sudo systemctl restart usbmuxd 2>/dev/null || true

# Opruimen build-bestanden
rm -rf "$BUILD_DIR"

# Versie tonen
echo ""
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  Klaar!${NC}"
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════${NC}"
echo ""
echo -e "  idevice_id versie: ${BOLD}$(idevice_id --version 2>&1 | head -1)${NC}"
echo -e "  ifuse versie:      ${BOLD}$(ifuse --version 2>&1 | head -1)${NC}"
echo ""
echo -e "  Sluit je iPhone aan en test met: ${BOLD}idevice_id -l${NC}"
echo ""
