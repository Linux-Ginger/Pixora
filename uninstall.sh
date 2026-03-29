#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — uninstall.sh
#  by LinuxGinger
# ─────────────────────────────────────────────

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

read -p "Weet je zeker dat je Pixora wilt verwijderen? (j/N) " antwoord

if [[ "$antwoord" != "j" && "$antwoord" != "J" ]]; then
    echo "Verwijdering geannuleerd."
    exit 0
fi

echo ""

rm -rf "$HOME/.local/share/pixora"
rm -f "$HOME/.local/share/applications/pixora.desktop"
rm -f "$HOME/.local/bin/pixora"

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$HOME/.local/share/applications"
fi

echo -e "${GREEN}${BOLD}✓ Pixora is verwijderd.${NC}"
echo ""
