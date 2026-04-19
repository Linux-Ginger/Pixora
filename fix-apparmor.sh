#!/bin/bash
# Pixora AppArmor fix voor Ubuntu 24.04+
# Installeert een per-app profile zodat WebKit (kaart-weergave) werkt,
# zonder system-wide AppArmor te relaxen.

set -e

case "${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}" in
    nl*|NL*) NL=1 ;;
    *)       NL=0 ;;
esac

if [ "$NL" = "1" ]; then
    echo "→ AppArmor-profile installeren (vraagt sudo-wachtwoord)…"
else
    echo "→ Installing AppArmor profile (sudo password required)…"
fi
sudo tee /etc/apparmor.d/pixora > /dev/null << 'EOF'
abi <abi/4.0>,
include <tunables/global>

profile pixora flags=(unconfined) {
  userns,
  include if exists <local/pixora>
}
EOF

sudo systemctl reload apparmor
if [ "$NL" = "1" ]; then
    echo "  ✓ Profile geïnstalleerd"
    echo "→ Pixora launcher bijwerken…"
else
    echo "  ✓ Profile installed"
    echo "→ Updating Pixora launcher…"
fi
cat > "$HOME/.local/bin/pixora" << EOF
#!/bin/bash
if command -v aa-exec >/dev/null 2>&1 && [ -r /etc/apparmor.d/pixora ]; then
  exec aa-exec -p pixora -- python3 \$HOME/.local/share/pixora/viewer/main.py "\$@"
else
  exec python3 \$HOME/.local/share/pixora/viewer/main.py "\$@"
fi
EOF
chmod +x "$HOME/.local/bin/pixora"
if [ "$NL" = "1" ]; then
    echo "  ✓ Launcher bijgewerkt"
    echo ""
    echo "Klaar. Start Pixora nu met:  ~/.local/bin/pixora"
else
    echo "  ✓ Launcher updated"
    echo ""
    echo "Done. Start Pixora now with:  ~/.local/bin/pixora"
fi
