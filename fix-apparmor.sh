#!/bin/bash
# Pixora AppArmor fix voor Ubuntu 24.04+
# Installeert een per-app profile zodat WebKit (kaart-weergave) werkt,
# zonder system-wide AppArmor te relaxen.

set -e

echo "→ AppArmor-profile installeren (vraagt sudo-wachtwoord)…"
sudo tee /etc/apparmor.d/pixora > /dev/null << 'EOF'
abi <abi/4.0>,
include <tunables/global>

profile pixora flags=(unconfined) {
  userns,
  include if exists <local/pixora>
}
EOF

sudo systemctl reload apparmor
echo "  ✓ Profile geïnstalleerd"

echo "→ Pixora launcher bijwerken…"
cat > "$HOME/.local/bin/pixora" << EOF
#!/bin/bash
if command -v aa-exec >/dev/null 2>&1 && [ -r /etc/apparmor.d/pixora ]; then
  exec aa-exec -p pixora -- python3 \$HOME/.local/share/pixora/viewer/main.py "\$@"
else
  exec python3 \$HOME/.local/share/pixora/viewer/main.py "\$@"
fi
EOF
chmod +x "$HOME/.local/bin/pixora"
echo "  ✓ Launcher bijgewerkt"

echo ""
echo "Klaar. Start Pixora nu met:  ~/.local/bin/pixora"
