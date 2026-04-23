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
# Voor de PIXORA-banner: puur bold, geen kleurcode. Inherit de terminal-
# default foreground (wit op dark, zwart op light). \033[1;97m gaf op
# sommige terminals een bruin/geel-achtige render.
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

# Block-letter "PIXORA" text in wit. Wordt zowel in de fallback als
# naast het chafa-icoon gebruikt.
PIXORA_TEXT=(
"██████╗ ██╗██╗  ██╗ ██████╗ ██████╗  █████╗ "
"██╔══██╗██║╚██╗██╔╝██╔═══██╗██╔══██╗██╔══██╗"
"██████╔╝██║ ╚███╔╝ ██║   ██║██████╔╝███████║"
"██╔═══╝ ██║ ██╔██╗ ██║   ██║██╔══██╗██╔══██║"
"██║     ██║██╔╝ ██╗╚██████╔╝██║  ██║██║  ██║"
"╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝"
)

# Fallback voor wanneer chafa/curl/internet er niet is.
print_ascii_logo() {
    local line
    for line in "${PIXORA_TEXT[@]}"; do
        printf '%b  %s%b\n' "${BOLD}" "$line" "${NC}"
    done
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

# Echte logo rendering. Chafa tekent alleen het aperture-icoon
# (kleurige bloem); daarnaast zetten we de "PIXORA" block-ASCII,
# line-by-line verticaal gecentreerd tegen het icoon.
render_real_logo() {
    if ! command -v chafa &>/dev/null; then return 1; fi
    if ! command -v curl   &>/dev/null; then return 1; fi
    local tmp
    tmp=$(mktemp --suffix=.svg 2>/dev/null) || return 1
    local logo_url="https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/assets/logos/pixora-icon.svg"
    if ! curl -fsSL "$logo_url" -o "$tmp" 2>/dev/null || [ ! -s "$tmp" ]; then
        rm -f "$tmp"
        return 1
    fi
    local out
    # Icon viewBox 0 0 120 120 (1:1). 18x9 cellen = ~18×18 logische
    # pixels bij half-block rendering — genoeg detail voor de bloem.
    out=$(chafa --size 18x9 "$tmp" 2>/dev/null)
    rm -f "$tmp"
    if [ -z "$out" ]; then return 1; fi

    local -a icon_arr
    mapfile -t icon_arr <<< "$out"
    local icon_rows=${#icon_arr[@]}
    local text_rows=${#PIXORA_TEXT[@]}

    # Terminal-breedte check: icoon ~18 + gap 2 + tekst ~45 + margin 2 ≈ 67.
    # Onder die grens printen we stacked — side-by-side zou wrappen.
    local term_w
    term_w=$(tput cols 2>/dev/null || echo 80)
    clear
    echo ""
    if [ "$term_w" -lt 70 ]; then
        local line
        for line in "${icon_arr[@]}"; do
            printf '  %s\n' "$line"
        done
        echo ""
        print_ascii_logo
    else
        # Verticaal centreren: offset = hoeveel blank boven text om 'm onder
        # het icoon hoogte-te-lijnen te krijgen.
        local vpad=$(( (icon_rows - text_rows) / 2 ))
        local max=$icon_rows
        [ "$text_rows" -gt "$max" ] && max=$text_rows
        local i left ti right
        for ((i=0; i<max; i++)); do
            left="${icon_arr[$i]:-}"
            ti=$(( i - vpad ))
            if [ "$ti" -ge 0 ] && [ "$ti" -lt "$text_rows" ]; then
                right="${PIXORA_TEXT[$ti]}"
                printf '  %s  %b%s%b\n' "$left" "$BOLD" "$right" "$NC"
            else
                printf '  %s\n' "$left"
            fi
        done
    fi
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
