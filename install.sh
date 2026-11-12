#!/usr/bin/env bash
# Installiert Satellit auf einem Raspberry Pi mit Google AIY Voice HAT (v1).
# Raspberry Pi OS (Bookworm/Trixie). Danach einmal neu starten.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$USER}"

echo "==> Pakete installieren"
sudo apt-get update
sudo apt-get install -y python3-gpiozero alsa-utils ffmpeg espeak-ng
sudo apt-get install -y libttspico-utils \
  || echo "   (pico2wave nicht verfügbar – es wird espeak-ng verwendet)"

echo "==> Voice-HAT-Treiber aktivieren"
CONFIG=/boot/firmware/config.txt
[ -f "$CONFIG" ] || CONFIG=/boot/config.txt
sudo cp "$CONFIG" "$CONFIG.bak-satellit"
sudo sed -i 's/^dtparam=audio=on/dtparam=audio=off/' "$CONFIG"
grep -q '^dtoverlay=googlevoicehat-soundcard' "$CONFIG" \
  || echo 'dtoverlay=googlevoicehat-soundcard' | sudo tee -a "$CONFIG" >/dev/null

echo "==> ALSA-Konfiguration"
[ -f /etc/asound.conf ] && sudo cp /etc/asound.conf /etc/asound.conf.bak-satellit
sudo cp "$DIR/asound.conf" /etc/asound.conf

echo "==> Benutzer $RUN_USER in Gruppen audio/gpio"
sudo usermod -aG audio,gpio "$RUN_USER"

echo "==> Senderliste laden"
python3 "$DIR/satellit.py" --update || echo "   (später erneut – braucht Internet)"

echo "==> systemd-Dienst einrichten"
sed -e "s|@USER@|$RUN_USER|g" -e "s|@DIR@|$DIR|g" \
  "$DIR/satellit.service" | sudo tee /etc/systemd/system/satellit.service >/dev/null
chmod +x "$DIR/satellit.py"
sudo systemctl daemon-reload
sudo systemctl enable satellit.service

echo
echo "Fertig. Jetzt neu starten:  sudo reboot"
echo "Schönere Stimme (optional):  ./install_piper.sh"
