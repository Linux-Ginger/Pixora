#!/bin/bash

# ─────────────────────────────────────────────
#  Pixora Installer
#  by LinuxGinger
#  https://pixora.linuxginger.com
# ─────────────────────────────────────────────

set -e

REPO_URL="https://raw.githubusercontent.com/Linux-Ginger/pixora/main"
INSTALL_DIR="$HOME/.local/share/pixora"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"

# ── Kleuren voor terminal output ──
RED='\033[0;31m'
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
echo -e "  ${BOLD}by LinuxGinger${NC}"
echo ""
echo -e "  ${ORANGE}Pixora installeren...${NC}"
echo ""

# ── Check Ubuntu/Debian ──
if ! command -v apt &> /dev/null; then
    echo -e "  ${RED}Fout: Pixora vereist Ubuntu of Debian.${NC}"
    exit 1
fi

# ── Check Python 3 ──
if ! command -v python3 &> /dev/null; then
    echo -e "  ${RED}Fout: Python 3 is vereist.${NC}"
    exit 1
fi

# ── Stap 1: Systeem dependencies ──
echo -e "  ${BOLD}[1/5]${NC} Systeem packages installeren..."
sudo apt update -qq
sudo apt install -y -qq \
    python3-pip \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    ifuse \
    libimobiledevice-utils \
    usbmuxd \
    ffmpeg \
    git \
    2>/dev/null
echo -e "  ${GREEN}✓ Systeem packages geïnstalleerd${NC}"

# ── Stap 2: Python packages ──
echo -e "  ${BOLD}[2/5]${NC} Python packages installeren..."
pip3 install --quiet --break-system-packages \
    Pillow \
    imagehash \
    2>/dev/null
echo -e "  ${GREEN}✓ Python packages geïnstalleerd${NC}"

# ── Stap 3: Pixora downloaden ──
echo -e "  ${BOLD}[3/5]${NC} Pixora downloaden..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$DESKTOP_DIR"

# Clone of update repo
if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR" && git pull -q
else
    git clone -q https://github.com/Linux-Ginger/pixora.git "$INSTALL_DIR"
fi
echo -e "  ${GREEN}✓ Pixora gedownload${NC}"

# ── Stap 4: Launcher aanmaken ──
echo -e "  ${BOLD}[4/5]${NC} Launcher aanmaken..."

# Bin launcher
cat > "$BIN_DIR/pixora" << EOF
#!/bin/bash
python3 $INSTALL_DIR/viewer/main.py
EOF
chmod +x "$BIN_DIR/pixora"

# .desktop bestand voor app menu
cat > "$DESKTOP_DIR/pixora.desktop" << EOF
[Desktop Entry]
Name=Pixora
Comment=Photo & Video manager by LinuxGinger
Exec=$BIN_DIR/pixora
Icon=$INSTALL_DIR/docs/pixora-logo-dark.png
Terminal=false
Type=Application
Categories=Graphics;Photography;
StartupNotify=true
EOF

echo -e "  ${GREEN}✓ Launcher aangemaakt${NC}"

# ── Stap 5: Opstarten ──
echo -e "  ${BOLD}[5/5]${NC} Pixora starten..."
echo ""
echo -e "  ${GREEN}${BOLD}Installatie voltooid!${NC}"
echo ""
echo -e "  Pixora is geïnstalleerd. Je kunt het starten via:"
echo -e "  ${BOLD}pixora${NC}"
echo -e "  of via het applicatiemenu."
echo ""

# Terminal sluiten en Pixora starten
sleep 1
python3 "$INSTALL_DIR/viewer/main.py" &
exit 0