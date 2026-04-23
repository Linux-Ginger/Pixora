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

# Taal-detectie — volg $LANG / $LC_ALL / $LC_MESSAGES
case "${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}" in
    nl*|NL*)
        LBL_BY="door LinuxGinger"
        LBL_NEED="Fout: Pixora vereist Ubuntu of Debian."
        LBL_PREP="Voorbereiding…"
        LBL_FETCH="Pixora ophalen…"
        LBL_DONE="✓ Klaar — installer openen…"
        ;;
    de*|DE*)
        LBL_BY="von LinuxGinger"
        LBL_NEED="Fehler: Pixora benötigt Ubuntu oder Debian."
        LBL_PREP="Vorbereitung…"
        LBL_FETCH="Pixora wird abgerufen…"
        LBL_DONE="✓ Fertig — Installer wird geöffnet…"
        ;;
    fr*|FR*)
        LBL_BY="par LinuxGinger"
        LBL_NEED="Erreur : Pixora nécessite Ubuntu ou Debian."
        LBL_PREP="Préparation…"
        LBL_FETCH="Récupération de Pixora…"
        LBL_DONE="✓ Terminé — ouverture de l'installeur…"
        ;;
    *)
        LBL_BY="by LinuxGinger"
        LBL_NEED="Error: Pixora requires Ubuntu or Debian."
        LBL_PREP="Preparing…"
        LBL_FETCH="Fetching Pixora…"
        LBL_DONE="✓ Done — opening installer…"
        ;;
esac

clear
echo ""

# ── Check Ubuntu/Debian ──
if ! command -v apt &> /dev/null; then
    echo "${LBL_NEED}"
    exit 1
fi

# Fallback ASCII art for wanneer chafa er niet is / geen net / etc.
print_ascii_logo() {
    echo -e "${ORANGE}${BOLD}"
    echo "  ██████╗ ██╗██╗  ██╗ ██████╗ ██████╗  █████╗ "
    echo "  ██╔══██╗██║╚██╗██╔╝██╔═══██╗██╔══██╗██╔══██╗"
    echo "  ██████╔╝██║ ╚███╔╝ ██║   ██║██████╔╝███████║"
    echo "  ██╔═══╝ ██║ ██╔██╗ ██║   ██║██╔══██╗██╔══██║"
    echo "  ██║     ██║██╔╝ ██╗╚██████╔╝██║  ██║██║  ██║"
    echo "  ╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝"
    echo -e "${NC}"
}

# Eerst ASCII art tonen zodat de user direct feedback heeft. Na deps-
# install kunnen we 'm vervangen door het échte SVG-logo via chafa.
print_ascii_logo
echo -e "  ${BOLD}${LBL_BY}${NC}"
echo ""

# ── Minimale deps ── (chafa meegenomen zodat we straks het echte logo
# kunnen renderen)
echo -e "  ${ORANGE}${LBL_PREP}${NC}"
sudo apt-get install -y -qq \
    python3 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    gir1.2-gudev-1.0 \
    git \
    gettext \
    chafa \
    curl \
    2>/dev/null

# Echte logo rendering. curl haalt de SVG op (install.sh via one-liner
# loopt vóór de git-clone, dus nog geen local file), chafa zet hem om
# naar Unicode+ANSI color-blocks.
render_real_logo() {
    if ! command -v chafa &>/dev/null; then return 1; fi
    if ! command -v curl   &>/dev/null; then return 1; fi
    local tmp
    tmp=$(mktemp --suffix=.svg 2>/dev/null) || return 1
    local logo_url="https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/assets/logos/pixora-logo-light.svg"
    if ! curl -fsSL "$logo_url" -o "$tmp" 2>/dev/null || [ ! -s "$tmp" ]; then
        rm -f "$tmp"
        return 1
    fi
    local out
    out=$(chafa --size 44x13 "$tmp" 2>/dev/null)
    rm -f "$tmp"
    if [ -z "$out" ]; then return 1; fi
    clear
    echo ""
    echo "$out"
    echo ""
    echo -e "  ${BOLD}${LBL_BY}${NC}"
    echo ""
    return 0
}

render_real_logo || true

# WebKitGTK typelib — probeer nieuwste (6.0) eerst, valt terug op 4.1
sudo apt-get install -y -qq gir1.2-webkit-6.0 2>/dev/null || \
sudo apt-get install -y -qq gir1.2-webkit2-4.1 2>/dev/null || true

# ── Repo ophalen (altijd vers, geen cache) ──
echo -e "  ${ORANGE}${LBL_FETCH}${NC}"
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" fetch -q origin
    git -C "$INSTALL_DIR" reset --hard origin/main -q
else
    rm -rf "$INSTALL_DIR"
    git clone -q "$REPO_URL" "$INSTALL_DIR"
fi

# Compileer .po → .mo voor alle vertalingen.
# Oude .mo eerst verwijderen + errors zichtbaar houden (geen 2>/dev/null)
if command -v msgfmt >/dev/null 2>&1; then
    for po in "$INSTALL_DIR"/locale/*/LC_MESSAGES/pixora.po; do
        [ -f "$po" ] || continue
        mo="${po%.po}.mo"
        rm -f "$mo"
        if ! msgfmt -o "$mo" "$po"; then
            echo "  ⚠ msgfmt failed for $po"
        fi
    done
else
    echo "  ⚠ msgfmt not installed — translations will fall back to source strings"
fi

echo -e "  ${GREEN}${LBL_DONE}${NC}"
echo ""

# ── Installer starten vanuit de repo (geen CDN caching) ──
python3 "$INSTALL_DIR/installer.py"
