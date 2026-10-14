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
        LBL_KEPT="originelen van bewerkte foto's — bewaard; verwijder zelf als je ze niet meer nodig hebt."
        YES_CHAR="j"
        ;;
    de*|DE*)
        LBL_BY="von LinuxGinger"
        LBL_CONFIRM="Möchtest du Pixora wirklich deinstallieren? (j/N) "
        LBL_CANCEL="Deinstallation abgebrochen."
        LBL_DONE="✓ Pixora wurde deinstalliert."
        LBL_KEPT="Originale bearbeiteter Fotos — behalten; lösche den Ordner selbst, wenn du sie nicht mehr brauchst."
        YES_CHAR="j"
        ;;
    fr*|FR*)
        LBL_BY="par LinuxGinger"
        LBL_CONFIRM="Êtes-vous sûr de vouloir désinstaller Pixora ? (o/N) "
        LBL_CANCEL="Désinstallation annulée."
        LBL_DONE="✓ Pixora a été désinstallé."
        LBL_KEPT="originaux des photos retouchées — conservés ; supprimez le dossier vous-même si vous n'en avez plus besoin."
        YES_CHAR="o"
        ;;
    *)
        LBL_BY="by LinuxGinger"
        LBL_CONFIRM="Are you sure you want to uninstall Pixora? (y/N) "
        LBL_CANCEL="Uninstall cancelled."
        LBL_DONE="✓ Pixora has been uninstalled."
        LBL_KEPT="originals of photos you edited — kept; delete the folder yourself if you no longer need them."
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

# Read from the terminal, not stdin: with `curl … | bash` stdin is this
# script itself, so a plain `read` would swallow the next line of code.
read -r -p "${LBL_CONFIRM}" antwoord < /dev/tty

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

# System files: the map's AppArmor profile and the rule that lets Pixora
# restart usbmuxd (needs your password once).
if [ -e /etc/apparmor.d/pixora ]; then
    sudo rm -f /etc/apparmor.d/pixora < /dev/tty && \
        sudo systemctl reload apparmor >/dev/null 2>&1 || true
fi
if [ -e /etc/polkit-1/rules.d/50-pixora-usbmuxd.rules ]; then
    sudo rm -f /etc/polkit-1/rules.d/50-pixora-usbmuxd.rules < /dev/tty || true
fi

# Untouched originals of photos you edited are personal data: kept.
if [ -d "$HOME/.local/share/pixora-originals" ]; then
    echo "  $HOME/.local/share/pixora-originals"
    echo "  ↑ ${LBL_KEPT}"
fi

if command -v update-desktop-database &> /dev/null; then
    update-desktop-database "$HOME/.local/share/applications"
fi
if command -v gtk4-update-icon-cache &> /dev/null; then
    gtk4-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

echo -e "${GREEN}${BOLD}${LBL_DONE}${NC}"
echo ""
