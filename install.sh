#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora — install.sh
#  by LinuxGinger
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/Linux-Ginger/Pixora/main/install.sh | bash
# ─────────────────────────────────────────────

set -e

REPO_URL="https://github.com/Linux-Ginger/Pixora.git"
INSTALL_DIR="$HOME/.local/share/pixora"

GREEN='\033[0;32m'
ORANGE='\033[0;33m'
# Bold only, no color: \033[1;97m rendered brownish on some terminals.
BOLD='\033[1m'
NC='\033[0m'

# Language detection via $LANG / $LC_ALL / $LC_MESSAGES
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

# Block-letter "PIXORA" text, used in both fallback and next to chafa icon.
PIXORA_TEXT=(
"██████╗ ██╗██╗  ██╗ ██████╗ ██████╗  █████╗ "
"██╔══██╗██║╚██╗██╔╝██╔═══██╗██╔══██╗██╔══██╗"
"██████╔╝██║ ╚███╔╝ ██║   ██║██████╔╝███████║"
"██╔═══╝ ██║ ██╔██╗ ██║   ██║██╔══██╗██╔══██║"
"██║     ██║██╔╝ ██╗╚██████╔╝██║  ██║██║  ██║"
"╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝"
)

# Fallback when chafa/curl/internet is unavailable.
print_ascii_logo() {
    local line
    for line in "${PIXORA_TEXT[@]}"; do
        printf '%b  %s%b\n' "${BOLD}" "$line" "${NC}"
    done
}

# Real logo render: chafa draws the aperture icon, block-ASCII alongside.
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
    # Icon viewBox 0 0 120 120; 18x9 cells = ~18x18 via half-block render.
    out=$(chafa --size 18x9 "$tmp" 2>/dev/null)
    rm -f "$tmp"
    if [ -z "$out" ]; then return 1; fi

    local -a icon_arr
    mapfile -t icon_arr <<< "$out"
    local icon_rows=${#icon_arr[@]}
    local text_rows=${#PIXORA_TEXT[@]}

    # Width <70: stack vertically, else side-by-side would wrap.
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
        # Vertical centering: vpad blank lines above text.
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

# Bootstrap chafa+curl first so we can render color logo before heavy
# deps install; sudo caches credential so main apt step won't reprompt.
if ! command -v chafa &>/dev/null || ! command -v curl &>/dev/null; then
    echo -e "  ${ORANGE}${LBL_PREP}${NC}"
    sudo apt-get install -y -qq chafa curl 2>/dev/null || true
fi

# Render now; ASCII-fallback only if chafa/curl truly unavailable.
if ! render_real_logo; then
    print_ascii_logo
    echo -e "  ${BOLD}${LBL_BY}${NC}"
    echo ""
fi

# ── Main deps ── (sudo credential cached by bootstrap, no reprompt)
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

# WebKitGTK typelib: try 6.0 first, fall back to 4.1
sudo apt-get install -y -qq gir1.2-webkit-6.0 2>/dev/null || \
sudo apt-get install -y -qq gir1.2-webkit2-4.1 2>/dev/null || true

# ── Fetch repo (always fresh, no cache) ──
echo -e "  ${ORANGE}${LBL_FETCH}${NC}"
if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" fetch -q origin
    git -C "$INSTALL_DIR" reset --hard origin/main -q
else
    rm -rf "$INSTALL_DIR"
    git clone -q "$REPO_URL" "$INSTALL_DIR"
fi

# Compile .po -> .mo; remove old .mo first so stale files don't linger.
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

# ── Icon + .desktop BEFORE installer opens ──
# Shell decides window-icon at map time; writing .desktop from inside
# installer.py is too late and leaves the default gear.
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
# Pre-write updater .desktop too so no gear-flash when user clicks Update.
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
# Shell's AppInfoMonitor watches apps-dir mtime; extra touch triggers it.
touch "$APPS_DIR" 2>/dev/null || true
command -v gtk4-update-icon-cache >/dev/null 2>&1 && \
    gtk4-update-icon-cache -q -t -f "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database -q "$APPS_DIR" 2>/dev/null || true
# Brief pause so Shell's inotify processes the .desktop write; 0.3s avoids gear-flash.
sleep 0.3

echo -e "  ${GREEN}${LBL_DONE}${NC}"
echo ""

# Launch via .desktop so Shell binds icon pre-map (AppInfo.launch flow);
# fall back to direct python if gtk-launch/gio unavailable.
if command -v gtk-launch >/dev/null 2>&1; then
    exec gtk-launch com.linuxginger.pixora.installer
elif command -v gio >/dev/null 2>&1; then
    exec gio launch "$APPS_DIR/com.linuxginger.pixora.installer.desktop"
else
    exec python3 "$INSTALL_DIR/installer.py"
fi
