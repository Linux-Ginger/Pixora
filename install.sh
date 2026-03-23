#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — install.sh
#  by LinuxGinger
#
#  Gebruik:
#    curl -fsSL https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/install.sh | bash
# ─────────────────────────────────────────────

set -e

INSTALLER_URL="https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/installer.py"
TMP_INSTALLER="/tmp/pixora_installer.py"

# ── Kleuren ──
GREEN='\033[0;32m'
ORANGE='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

clear
echo ""
echo -e "${ORANGE}${BOLD}"
echo "  ██████╗ ██╗██╗  ██╗ ██████╗ ██████╗  █████╗ "
echo "  ██╔══██╗██║╚██╗██╔╝██╔═══██╗██╔══██╗██╔══██╗"
echo "  ██████╔╝██║ ╚███╔╝ ██║   ██║██████╔╝███████║"
echo "  ██╔═══╝ ██║ ██╔██╗ ██║   ██║██╔══██╗██╔══██║"
echo "  ██║     ██║██╔╝ ██╗╚██████╔╝██║  ██║██║  ██║"
echo "  ╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝"
echo -e "${NC}"
echo -e "  ${BOLD}door LinuxGinger${NC}"
echo ""

# ── Check Ubuntu/Debian ──
if ! command -v apt &> /dev/null; then
    echo "Fout: Pixora vereist Ubuntu of Debian."
    exit 1
fi

# ── Minimale deps voor de installer-GUI ──
echo -e "  ${ORANGE}Voorbereiding…${NC}"
sudo apt-get install -y -qq \
    python3 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    curl \
    git \
    2>/dev/null

echo -e "  ${GREEN}✓ Klaar — installer openen…${NC}"
echo ""

# ── Installer downloaden en starten ──
curl -fsSL "$INSTALLER_URL" -o "$TMP_INSTALLER"
python3 "$TMP_INSTALLER"
