# Satellit

Ein gedachter Satellit kreist über der Erde und hört Radio.

Er fliegt in 2–6 Stunden zu einem zufälligen Ort und von dort sofort weiter zum nächsten, ohne
anzuhalten. Ein Knopf an der Box schaltet den Radiosender ein, über dem er gerade schwebt. Unterwegs
sammelt er außerdem Schnipsel von Sendern unter seiner Bahn ein und mischt daraus „Satellitenmüll“:
verrauscht, überlappend, wie aus dem Orbit empfangen.

Gebaut für einen **Raspberry Pi 3B** mit **Google AIY Voice HAT (v1)** (Lautsprecher, Mikrofon,
beleuchteter Knopf). Die Senderdaten kommen von [radio-browser.info](https://www.radio-browser.info),
einer offenen Senderdatenbank mit Koordinaten (rund 10.000 Sender mit Ortsangabe).

## Bedienung

| Aktion | Was passiert |
|---|---|
| **Kurz drücken** | Ansage, wo der Satellit ist („Über Monastir, Tunisia. Radio Tunisie Monastir.“), dann läuft der nächstgelegene Sender. Ist er über dem Meer: „Funkkontakt über 1200 Kilometer …“. Antwortet ein Sender nicht: „Kein Signal.“ und der nächste wird versucht. |
| **Lang drücken** (≥ 1,2 s) | Der neueste Satellitenmüll, mit Ansage, wo er eingesammelt wurde. |
| **Während der Wiedergabe drücken** | Stopp. |

**LED:** schnelles Blinken = sucht Signal · pulsiert = Wiedergabe · ganz schwaches Glimmen = sammelt
gerade Müll · dreimal Blinken = Start.

## So funktioniert es

- **Die Bahn** besteht aus Etappen von 2–6 Stunden. Das nächste Ziel wird ausgelost: erst ein
  zufälliges Land, dann ein Ort mit Sender darin. So schwebt der Satellit nicht fast nur über Europa
  und den USA, wo die meisten Sender sind. Die Bahn wird gespeichert und läuft nach einem Neustart weiter.
- **Der Sender** ist der nächstgelegene zur aktuellen Position. Liegen mehrere fast gleich nah
  (innerhalb von 30 km), wird gelost.
- **Satellitenmüll** wird unregelmäßig gesammelt, im Mittel alle 3 Stunden: je 2–7 Sekunden von
  5 Sendern entlang der Bahn der letzten 3 Stunden. Die Schnipsel werden überlappend mit Rauschen,
  Knacksen und Kurzwellen-Schwund gemischt.
- **Sprachausgabe** offline mit [Piper](https://github.com/rhasspy/piper), sonst `pico2wave` bzw.
  `espeak-ng`. Stürzt Piper bei einem fremden Namen ab, springt die Ersatzstimme ein.

## Installation (auf dem Pi)

Voraussetzung: Raspberry Pi OS, Internet (WLAN oder Kabel).

```bash
git clone <dieses-repo> ~/satellit-radio
cd ~/satellit-radio
./install.sh          # Pakete, Voice-HAT-Treiber, ALSA, Senderliste, systemd-Dienst
./install_piper.sh    # optional: schönere Stimme (lädt ca. 200 MB)
sudo reboot
```

Nach dem Neustart läuft die Box automatisch (Dienst `satellit`).

## Ohne Knopf ausprobieren

```bash
python3 satellit.py --wo       # Wo ist der Satellit, wohin fliegt er, welche Sender sind nah?
python3 satellit.py --radio    # Sender abspielen (Strg+C = Stopp)
python3 satellit.py --muell    # Müll einsammeln und abspielen
python3 satellit.py --update   # Senderliste neu laden
```

Das geht auch am Mac (braucht `ffmpeg`, z. B. per Homebrew). Dort wird über die Mac-Lautsprecher abgespielt.

## Lautstärke

```bash
amixer set Master 60%     # Gesamtlautstärke
sudo alsactl store        # über Neustart behalten
```

Einzeln für Sender und Ansage: `RADIO_VOLUME` und `ANNOUNCE_VOLUME` in `satellit.py`.

## Dateien

Alles liegt in `~/satellit/` (änderbar mit der Umgebungsvariable `SAT_DATA`):

| Pfad | Inhalt |
|---|---|
| `sender.json` | Senderliste, wird wöchentlich erneuert |
| `bahn.json` | Die Etappen der Umlaufbahn |
| `muell/JJJJ-MM-TT_HH-MM-SS.wav` | Satellitenmüll |
| `muell/JJJJ-MM-TT_HH-MM-SS.json` | Welche Sender und Orte darin stecken |

Es werden die neuesten 200 Müll-Aufnahmen aufgehoben (je ca. 0,5–1,5 MB).

## Einstellungen

Oben in `satellit.py`:

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `LEG_MIN_H` / `LEG_MAX_H` | 2 / 6 h | Flugdauer zum nächsten Ziel |
| `NEARBY_KM` | 30 | Sender, die höchstens so viel weiter weg sind als der nächste, gelten als „gleich nah“ |
| `RADIO_MAX_MIN` | 60 | Radio geht nach so vielen Minuten von selbst aus (0 = nie) |
| `RADIO_VOLUME` | 1.0 | Lautstärke der Sender |
| `ANNOUNCE` | an | Ansage vor dem Sender |
| `ANNOUNCE_VOLUME` | 0.3 | Lautstärke der Ansage |
| `GARBAGE_SOURCES` | 5 | Aus so vielen Sendern wird Müll gesammelt |
| `GARBAGE_TRACK_H` | 3 h | … entlang dieses Stücks der Bahn |
| `SNIPPET_MIN_S` / `SNIPPET_MAX_S` | 2 / 7 s | Länge eines Schnipsels |
| `GARBAGE_PAUSE_MEAN_H` | 3 h | Mittlere Pause zwischen zwei Sammlungen (unregelmäßig) |
| `GARBAGE_KEEP` | 200 | So viele Müll-Aufnahmen werden aufgehoben |
| `HOLD_S` | 1,2 s | Ab so langem Drücken gilt es als „lang“ |
| `PIPER_VOICE` | de_DE-thorsten-medium | Stimme ([Hörproben](https://rhasspy.github.io/piper-samples/)) |

Nach Änderungen: `sudo systemctl restart satellit`

## Log

```bash
journalctl -u satellit -f
```

## Hinweise

- **Uhrzeit:** Der Pi 3B hat keine batteriegepufferte Uhr und braucht beim Start Netz, sonst stimmt
  die Position des Satelliten nicht.
- **Senderdaten:** Manche Sender in radio-browser.info haben falsche Koordinaten. Ab und zu meldet
  sich also ein Sender von einem Ort, an dem er gar nicht ist. Die Ländernamen sind englisch, so stehen
  sie in der Datenbank.
- **Voice HAT v2 / Voice Bonnet:** andere Pins und anderer Treiber. Dieses Projekt ist für den v1-HAT.

## Lizenz

MIT – siehe [LICENSE](LICENSE). Gilt für den Code in diesem Repo. Die Radiostreams gehören den
jeweiligen Sendern; Piper, ffmpeg und die Senderdaten von radio-browser.info haben eigene Lizenzen und
sind nicht Teil dieses Repos.
