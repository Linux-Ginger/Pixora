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
        # Verticaal centreren: vpad lege regels boven de tekst zodat hij
        # visueel in het midden van het icoon staat.
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

# Bootstrap: als chafa of curl nog niet op het systeem staan, eerst
# even die twee kleine pakketten installeren (één sudo-prompt) zodat
# we daarna het kleurlogo kunnen renderen VOOR de zware deps-install.
# sudo cachet de credential, dus de grote apt-stap daarna prompt niet
# nog een keer.
if ! command -v chafa &>/dev/null || ! command -v curl &>/dev/null; then
    echo -e "  ${ORANGE}${LBL_PREP}${NC}"
    sudo apt-get install -y -qq chafa curl 2>/dev/null || true
fi

# Nu renderen — op eerste install na de bootstrap chafa aanwezig, op
# latere installs direct. ASCII-fallback alleen als chafa/curl echt
# niet te krijgen zijn (geen net, locked apt, etc.).
if ! render_real_logo; then
    print_ascii_logo
    echo -e "  ${BOLD}${LBL_BY}${NC}"
    echo ""
fi

# ── Hoofd-deps ── (geen tweede password-prompt — sudo credential
# gecachet door de bootstrap-stap hierboven)
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
    2>/dev/null

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

# ── Icon + .desktop VÓÓR de installer opent ──
# GNOME Shell besluit het window-icoon op het moment dat het window
# open gaat. Als we de .desktop pas IN de installer-python schrijven,
# is Shell al te laat: hij laat het default tandwiel staan. Dus eerst
# hier.
ICONS_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
APPS_DIR="$HOME/.local/share/applications"
mkdir -p "$ICONS_DIR" "$APPS_DIR"
ICON_SRC="$INSTALL_DIR/assets/logos/pixora-icon.svg"
if [ -f "$ICON_SRC" ]; then
    cp -f "$ICON_SRC" "$ICONS_DIR/pixora-icon.svg"
    cp -f "$ICON_SRC" "$ICONS_DIR/com.linuxginger.pixora.installer.svg"
fi
cat > "$APPS_DIR/com.linuxginger.pixora.installer.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pixora Installer
Icon=$ICON_SRC
Exec=python3 $INSTALL_DIR/installer.py
Terminal=false
StartupWMClass=com.linuxginger.pixora.installer
StartupNotify=true
NoDisplay=true
Categories=System;
EOF
# Ook de updater z'n .desktop nu al schrijven — tegen de tijd dat de
# user op "Update" klikt, zit hij al in Shell's cache en is er geen
# tandwiel-flash bij het updater-window-open.
cat > "$APPS_DIR/com.linuxginger.pixora.updater.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Pixora Updater
Icon=$ICON_SRC
Exec=python3 $INSTALL_DIR/viewer/updater.py
Terminal=false
StartupWMClass=com.linuxginger.pixora.updater
StartupNotify=true
NoDisplay=true
Categories=System;
EOF
cp -f "$ICON_SRC" "$ICONS_DIR/com.linuxginger.pixora.updater.svg" 2>/dev/null || true
# Shell's AppInfoMonitor luistert naar mtime-wijzigingen op de apps-dir
# zelf — touchen geeft een extra trigger naast het schrijven zelf.
touch "$APPS_DIR" 2>/dev/null || true
command -v gtk4-update-icon-cache >/dev/null 2>&1 && \
    gtk4-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
# Korte pauze zodat Shell's inotify het .desktop-schrijven kan
# verwerken. Shell swapt het icoon ook nog als het window al open is,
# dus 0.3s volstaat; geen zichtbare gear-flash meer.
sleep 0.3

echo -e "  ${GREEN}${LBL_DONE}${NC}"
echo ""

# ── Installer starten via .desktop zodat GNOME Shell het icoon
# direct koppelt aan het window, zonder eerst even het tandwiel te
# tonen. gtk-launch / gio launch gaan door de AppInfo.launch-flow
# die AppInfoMonitor triggert vóór het window map't. Fallback op
# directe python-start als beide niet aanwezig zijn.
if command -v gtk-launch >/dev/null 2>&1; then
    exec gtk-launch com.linuxginger.pixora.installer
elif command -v gio >/dev/null 2>&1; then
    exec gio launch "$APPS_DIR/com.linuxginger.pixora.installer.desktop"
else
    exec python3 "$INSTALL_DIR/installer.py"
fi
