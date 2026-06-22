#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — uninstall.sh
#  by LinuxGinger
# ─────────────────────────────────────────────

set -e

GREEN='\033[0;32m'
ORANGE='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

# Language detection
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}" in
    nl*|NL*)
        LBL_BY="door LinuxGinger"
        LBL_CONFIRM="Weet je zeker dat je Pixora wilt verwijderen? (j/N) "
        LBL_CANCEL="Verwijdering geannuleerd."
        LBL_DONE="✓ Pixora is verwijderd."
        YES_CHAR="j"
        ;;
    de*|DE*)
        LBL_BY="von LinuxGinger"
        LBL_CONFIRM="Möchtest du Pixora wirklich deinstallieren? (j/N) "
        LBL_CANCEL="Deinstallation abgebrochen."
        LBL_DONE="✓ Pixora wurde deinstalliert."
        YES_CHAR="j"
        ;;
    fr*|FR*)
        LBL_BY="par LinuxGinger"
        LBL_CONFIRM="Êtes-vous sûr de vouloir désinstaller Pixora ? (o/N) "
        LBL_CANCEL="Désinstallation annulée."
        LBL_DONE="✓ Pixora a été désinstallé."
        YES_CHAR="o"
        ;;
    *)
        LBL_BY="by LinuxGinger"
        LBL_CONFIRM="Are you sure you want to uninstall Pixora? (y/N) "
        LBL_CANCEL="Uninstall cancelled."
        LBL_DONE="✓ Pixora has been uninstalled."
        YES_CHAR="y"
        ;;
esac

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
echo -e "  ${BOLD}${LBL_BY}${NC}"
echo ""

read -p "${LBL_CONFIRM}" antwoord

if [[ "${antwoord,,}" != "${YES_CHAR}" ]]; then
    echo "${LBL_CANCEL}"
    exit 0
fi

echo ""

rm -rf "$HOME/.local/share/pixora"
rm -f "$HOME/.local/share/applications/pixora.desktop"
rm -f "$HOME/.local/share/applications/com.linuxginger.pixora.installer.desktop"
rm -f "$HOME/.local/share/applications/com.linuxginger.pixora.updater.desktop"
rm -f "$HOME/.local/bin/pixora"

# Icons written next to the .desktop files.
ICONS_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
rm -f "$ICONS_DIR/pixora-icon.svg" \
      "$ICONS_DIR/com.linuxginger.pixora.installer.svg" \
      "$ICONS_DIR/com.linuxginger.pixora.updater.svg"

# Settings and caches (the photo library itself is never touched).
rm -rf "$HOME/.config/pixora"
rm -rf "$HOME/.cache/pixora"

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$HOME/.local/share/applications"
fi
if command -v gtk4-update-icon-cache &> /dev/null; then
    gtk4-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

echo -e "${GREEN}${BOLD}${LBL_DONE}${NC}"
echo ""
