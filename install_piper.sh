#!/usr/bin/env bash
# Installiert die Piper-Sprachausgabe (offline, neuronal) und deutsche Stimmen.
# Aufruf:  ./install_piper.sh                 -> Standardstimmen
#          ./install_piper.sh de_DE-kerstin-low  -> nur diese Stimme
# Hörproben: https://rhasspy.github.io/piper-samples/
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)/piper"
mkdir -p "$DIR/voices"

case "$(uname -m)" in
  aarch64) ARCH=aarch64 ;;
  armv7l|armv6l) ARCH=armv7l ;;
  *) echo "Unbekannte Architektur: $(uname -m)"; exit 1 ;;
esac

if [ ! -x "$DIR/piper/piper" ]; then
  echo "==> Piper herunterladen ($ARCH)"
  curl -fL "https://github.com/rhasspy/piper/releases/download/2023.11.14-2/piper_linux_${ARCH}.tar.gz" \
    | tar -xz -C "$DIR"
fi

VOICES=("$@")
[ ${#VOICES[@]} -eq 0 ] && VOICES=(de_DE-thorsten-medium de_DE-kerstin-low de_DE-ramona-low)

for v in "${VOICES[@]}"; do
  # de_DE-thorsten-medium -> de/de_DE/thorsten/medium
  IFS='-' read -r lang name quality <<<"$v"
  base="https://huggingface.co/rhasspy/piper-voices/resolve/main/${lang%%_*}/${lang}/${name}/${quality}/${v}"
  if [ ! -f "$DIR/voices/$v.onnx" ]; then
    echo "==> Stimme $v herunterladen"
    curl -fL -o "$DIR/voices/$v.onnx" "$base.onnx"
    curl -fL -o "$DIR/voices/$v.onnx.json" "$base.onnx.json"
  fi
done

echo
echo "==> Probe:"
for v in "${VOICES[@]}"; do
  echo "   $v"
  echo "Mittwoch, 12. November 2026, 14 Uhr 32. Länge: 12 Sekunden." \
    | "$DIR/piper/piper" --model "$DIR/voices/$v.onnx" --output_file /tmp/probe.wav 2>/dev/null
  aplay -q /tmp/probe.wav || echo "   (Wiedergabe fehlgeschlagen – erst neu starten?)"
  sleep 1
done
echo
echo "Fertig. Stimme in satellit.py bei PIPER_VOICE eintragen, dann:"
echo "  sudo systemctl restart satellit"
