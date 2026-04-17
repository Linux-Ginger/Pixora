#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — install.sh
#  by LinuxGinger
#
#  Gebruik:
#    curl -fsSL https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/install.sh | bash
# ─────────────────────────────────────────────

set -e

REPO_URL="https://github.com/Linux-Ginger/Pixora.git"
INSTALL_DIR="$HOME/.local/share/pixora"

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

# ── Minimale deps ──
echo -e "  ${ORANGE}Voorbereiding…${NC}"
sudo apt-get install -y -qq \
    python3 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    gir1.2-gudev-1.0 \
    git \
    2>/dev/null

# ── Repo ophalen (altijd vers, geen cache) ──
echo -e "  ${ORANGE}Pixora ophalen…${NC}"
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" fetch -q origin
    git -C "$INSTALL_DIR" reset --hard origin/main -q
else
    rm -rf "$INSTALL_DIR"
    git clone -q "$REPO_URL" "$INSTALL_DIR"
fi

echo -e "  ${GREEN}✓ Klaar — installer openen…${NC}"
echo ""

# ── Installer starten vanuit de repo (geen CDN caching) ──
python3 "$INSTALL_DIR/installer.py"
