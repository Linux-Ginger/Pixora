#!/bin/bash
# Pixora AppArmor fix voor Ubuntu 24.04+
# Installeert een per-app profile zodat WebKit (kaart-weergave) werkt,
# zonder system-wide AppArmor te relaxen.

set -e

case "${LC_ALL:-${LC_MESSAGES:-${LANG:-en}}}" in
    nl*|NL*) LANG_CODE=nl ;;
    de*|DE*) LANG_CODE=de ;;
    fr*|FR*) LANG_CODE=fr ;;
    *)       LANG_CODE=en ;;
esac

case "$LANG_CODE" in
    nl) echo "→ AppArmor-profile installeren (vraagt sudo-wachtwoord)…" ;;
    de) echo "→ AppArmor-Profil wird installiert (sudo-Passwort erforderlich)…" ;;
    fr) echo "→ Installation du profil AppArmor (mot de passe sudo requis)…" ;;
    *)  echo "→ Installing AppArmor profile (sudo password required)…" ;;
esac
sudo tee /etc/apparmor.d/pixora > /dev/null << 'EOF'
abi <abi/4.0>,
include <tunables/global>

profile pixora flags=(unconfined) {
  userns,
  include if exists <local/pixora>
}
EOF

sudo systemctl reload apparmor
case "$LANG_CODE" in
    nl) echo "  ✓ Profile geïnstalleerd"
        echo "→ Pixora launcher bijwerken…" ;;
    de) echo "  ✓ Profil installiert"
        echo "→ Pixora-Launcher wird aktualisiert…" ;;
    fr) echo "  ✓ Profil installé"
        echo "→ Mise à jour du lanceur Pixora…" ;;
    *)  echo "  ✓ Profile installed"
        echo "→ Updating Pixora launcher…" ;;
esac
cat > "$HOME/.local/bin/pixora" << EOF
#!/bin/bash
if command -v aa-exec >/dev/null 2>&1 && [ -r /etc/apparmor.d/pixora ]; then
  exec aa-exec -p pixora -- python3 \$HOME/.local/share/pixora/viewer/main.py "\$@"
else
  exec python3 \$HOME/.local/share/pixora/viewer/main.py "\$@"
fi
EOF
chmod +x "$HOME/.local/bin/pixora"
case "$LANG_CODE" in
    nl) echo "  ✓ Launcher bijgewerkt"
        echo ""
        echo "Klaar. Start Pixora nu met:  ~/.local/bin/pixora" ;;
    de) echo "  ✓ Launcher aktualisiert"
        echo ""
        echo "Fertig. Starte Pixora jetzt mit:  ~/.local/bin/pixora" ;;
    fr) echo "  ✓ Lanceur mis à jour"
        echo ""
        echo "Terminé. Démarrez Pixora avec :  ~/.local/bin/pixora" ;;
    *)  echo "  ✓ Launcher updated"
        echo ""
        echo "Done. Start Pixora now with:  ~/.local/bin/pixora" ;;
esac
