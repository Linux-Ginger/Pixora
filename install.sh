#!/bin/bash

# Pixora Installer
# by LinuxGinger
# https://pixora.linuxginger.com

set -e

REPO="https://raw.githubusercontent.com/Linux-Ginger/pixora/main"
INSTALL_DIR="$HOME/.local/share/pixora"
BIN_DIR="$HOME/.local/bin"

echo ""
echo "  ██████╗ ██╗██╗  ██╗ ██████╗ ██████╗  █████╗ "
echo "  ██╔══██╗██║╚██╗██╔╝██╔═══██╗██╔══██╗██╔══██╗"
echo "  ██████╔╝██║ ╚███╔╝ ██║   ██║██████╔╝███████║"
echo "  ██╔═══╝ ██║ ██╔██╗ ██║   ██║██╔══██╗██╔══██║"
echo "  ██║     ██║██╔╝ ██╗╚██████╔╝██║  ██║██║  ██║"
echo "  ╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝"
echo "  by LinuxGinger"
echo ""
echo "  Installing Pixora..."
echo ""

# Check Ubuntu
if ! command -v apt &> /dev/null; then
    echo "  Error: Pixora requires Ubuntu/Debian."
    exit 1
fi

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "  Error: Python 3 is required."
    exit 1
fi

echo "  [1/4] Installing system dependencies..."
sudo apt update -qq
sudo apt install -y -qq \
    python3-pip \
    python3-venv \
    ifuse \
    libimobiledevice-utils \
    usbmuxd \
    2>/dev/null

echo "  [2/4] Installing Python packages..."
pip3 install --quiet \
    PyQt6 \
    Pillow \
    imagehash \
    2>/dev/null

echo "  [3/4] Setting up Pixora..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$BIN_DIR"

# TODO: Download Pixora files here once available

echo "  [4/4] Done!"
echo ""
echo "  Pixora has been installed successfully."
echo "  Run 'pixora' to get started."
echo ""

# Close terminal and launch Pixora GUI installer
# TODO: Launch PyQt6 setup wizard here
