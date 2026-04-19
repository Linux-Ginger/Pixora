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

case "${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}" in
    nl*|NL*)
        LBL_TITLE="libimobiledevice builder voor Pixora"
        LBL_DEPS="Build-dependencies installeren…"
        LBL_DEPS_OK="Dependencies geïnstalleerd"
        LBL_STOP="Bestaande usbmuxd stoppen…"
        LBL_BUILD="bouwen…"
        LBL_INSTALLED="geïnstalleerd"
        LBL_ACTIVATE="Activeren…"
        LBL_DONE="Klaar!"
        LBL_VER_IDEV="idevice_id versie:"
        LBL_VER_IFUSE="ifuse versie:     "
        LBL_TEST="Sluit je iPhone aan en test met:"
        ;;
    de*|DE*)
        LBL_TITLE="libimobiledevice Builder für Pixora"
        LBL_DEPS="Build-Abhängigkeiten werden installiert…"
        LBL_DEPS_OK="Abhängigkeiten installiert"
        LBL_STOP="Bestehenden usbmuxd stoppen…"
        LBL_BUILD="wird gebaut…"
        LBL_INSTALLED="installiert"
        LBL_ACTIVATE="Aktivieren…"
        LBL_DONE="Fertig!"
        LBL_VER_IDEV="idevice_id Version:"
        LBL_VER_IFUSE="ifuse Version:     "
        LBL_TEST="Schließe dein iPhone an und teste mit:"
        ;;
    fr*|FR*)
        LBL_TITLE="constructeur libimobiledevice pour Pixora"
        LBL_DEPS="Installation des dépendances de build…"
        LBL_DEPS_OK="Dépendances installées"
        LBL_STOP="Arrêt de usbmuxd existant…"
        LBL_BUILD="compilation…"
        LBL_INSTALLED="installé"
        LBL_ACTIVATE="Activation…"
        LBL_DONE="Terminé !"
        LBL_VER_IDEV="version idevice_id :"
        LBL_VER_IFUSE="version ifuse :     "
        LBL_TEST="Connectez votre iPhone et testez avec :"
        ;;
    *)
        LBL_TITLE="libimobiledevice builder for Pixora"
        LBL_DEPS="Installing build dependencies…"
        LBL_DEPS_OK="Dependencies installed"
        LBL_STOP="Stopping existing usbmuxd…"
        LBL_BUILD="building…"
        LBL_INSTALLED="installed"
        LBL_ACTIVATE="Activating…"
        LBL_DONE="Done!"
        LBL_VER_IDEV="idevice_id version:"
        LBL_VER_IFUSE="ifuse version:     "
        LBL_TEST="Connect your iPhone and test with:"
        ;;
esac

echo ""
echo -e "${ORANGE}${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${ORANGE}${BOLD}  ${LBL_TITLE}${NC}"
echo -e "${ORANGE}${BOLD}═══════════════════════════════════════════════${NC}"
echo ""

# ── Stap 1: Build-dependencies installeren ──
echo -e "${ORANGE}[1/9] ${LBL_DEPS}${NC}"
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
echo -e "${GREEN}  ✓ ${LBL_DEPS_OK}${NC}"

# ── Stop bestaande usbmuxd ──
echo -e "${ORANGE}  ${LBL_STOP}${NC}"
sudo systemctl stop usbmuxd 2>/dev/null || true

cd "$BUILD_DIR"

build_lib() {
    local name=$1
    local step=$2
    local total=9

    echo ""
    echo -e "${ORANGE}[$step/$total] $name ${LBL_BUILD}${NC}"

    if [ -d "$name" ]; then
        rm -rf "$name"
    fi

    git clone --depth 1 "https://github.com/libimobiledevice/$name.git" 2>/dev/null
    cd "$name"

    ./autogen.sh --prefix="$PREFIX" > /dev/null 2>&1
    make -j$(nproc) > /dev/null 2>&1
    sudo make install > /dev/null 2>&1

    cd "$BUILD_DIR"
    echo -e "${GREEN}  ✓ $name ${LBL_INSTALLED}${NC}"
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
echo -e "${ORANGE}[9/9] ${LBL_ACTIVATE}${NC}"
sudo ldconfig
sudo systemctl daemon-reload 2>/dev/null || true
sudo systemctl restart usbmuxd 2>/dev/null || true

# Opruimen build-bestanden
rm -rf "$BUILD_DIR"

# Versie tonen
echo ""
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}  ${LBL_DONE}${NC}"
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════${NC}"
echo ""
echo -e "  ${LBL_VER_IDEV} ${BOLD}$(idevice_id --version 2>&1 | head -1)${NC}"
echo -e "  ${LBL_VER_IFUSE} ${BOLD}$(ifuse --version 2>&1 | head -1)${NC}"
echo ""
echo -e "  ${LBL_TEST} ${BOLD}idevice_id -l${NC}"
echo ""
