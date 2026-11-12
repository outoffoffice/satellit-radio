#!/usr/bin/env python3
"""
Satellit – Raspberry Pi 3B + Google AIY Voice HAT (v1)

Ein gedachter Satellit wandert ständig über die Erde. Er fliegt in 2–6 Stunden
zu einem zufälligen Ort, dann sofort weiter zum nächsten – nie still.

- Kurz drücken:  Der Satellit schaltet den Radiosender ein, über dem er gerade
                 schwebt (bzw. den nächstgelegenen). Nochmal drücken = Stopp.
- Lang drücken:  "Satellitenmüll": Schnipsel von 5 Sendern, die er auf seiner
                 Bahn der letzten Stunden eingesammelt hat – verrauscht,
                 überlappend, wie aus dem Orbit. Nochmal drücken = Stopp.

Senderdaten: https://www.radio-browser.info (offen, mit Koordinaten).

Ohne Knopf testen:
  python3 satellit.py --wo       # wo ist der Satellit, welche Sender sind nah?
  python3 satellit.py --radio    # Sender abspielen (Strg+C = Stopp)
  python3 satellit.py --muell    # Müll einsammeln und abspielen
  python3 satellit.py --update   # Senderliste neu laden
"""

import argparse
import array
import datetime as dt
import heapq
import json
import logging
import math
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.request
import wave
from pathlib import Path

# --------------------------------------------------------------------------
# Einstellungen
# --------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("SAT_DATA", Path.home() / "satellit"))

LEG_MIN_H = 2.0            # so lange braucht der Satellit mindestens zum nächsten Ziel
LEG_MAX_H = 6.0            # … und höchstens
NEARBY_KM = 30             # Sender, die höchstens so viel weiter weg sind als der
                           # nächste, gelten als "gleich nah" – daraus wird gelost
RADIO_MAX_MIN = 60         # Radio geht nach so vielen Minuten von selbst aus (0 = nie)
RADIO_VOLUME = 1.0

ANNOUNCE = True            # vor dem Sender ansagen, wo der Satellit ist
ANNOUNCE_VOLUME = 0.3

GARBAGE_SOURCES = 5        # aus so vielen Sendern wird Müll gesammelt
GARBAGE_TRACK_H = 3        # … entlang der Bahn der letzten 3 Stunden
SNIPPET_MIN_S = 2          # Länge eines Schnipsels
SNIPPET_MAX_S = 7
GARBAGE_PAUSE_MEAN_H = 3.0 # im Mittel alle 3 h wird neuer Müll gesammelt (unregelmäßig)
GARBAGE_PAUSE_MIN_S = 10 * 60
GARBAGE_KEEP = 200         # so viele Müll-Aufnahmen werden aufgehoben
GARBAGE_RATE = 22050

HOLD_S = 1.2               # ab so langem Drücken gilt es als "lang"
BUTTON_PIN = 23            # AIY Voice HAT v1
LED_PIN = 25

STATIONS_MAX_AGE_D = 7     # Senderliste wöchentlich erneuern
API_SERVERS = ["de1.api.radio-browser.info", "de2.api.radio-browser.info",
               "fi1.api.radio-browser.info"]
USER_AGENT = "satellit-radio/1.0"

PIPER_DIR = Path(__file__).resolve().parent / "piper"
PIPER_VOICE = "de_DE-thorsten-medium"
PIPER_LENGTH_SCALE = 1.1

WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag",
              "Freitag", "Samstag", "Sonntag"]

STATIONS_FILE = DATA_DIR / "sender.json"
ORBIT_FILE = DATA_DIR / "bahn.json"
GARBAGE_DIR = DATA_DIR / "muell"
TMP_DIR = DATA_DIR / "tmp"
EARTH_KM = 6371.0

log = logging.getLogger("satellit")


# --------------------------------------------------------------------------
# Geometrie
# --------------------------------------------------------------------------
def to_vec(lat, lon):
    la, lo = math.radians(lat), math.radians(lon)
    return (math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la))


def from_vec(v):
    x, y, z = v
    return (math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x)))


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def dist_km(a, b):
    return EARTH_KM * math.acos(max(-1.0, min(1.0, dot(to_vec(*a), to_vec(*b)))))


def slerp(a, b, f):
    va, vb = to_vec(*a), to_vec(*b)
    om = math.acos(max(-1.0, min(1.0, dot(va, vb))))
    if om < 1e-6:
        return a
    s = math.sin(om)
    wa, wb = math.sin((1 - f) * om) / s, math.sin(f * om) / s
    return from_vec(tuple(wa * p + wb * q for p, q in zip(va, vb)))


def km_text(d):
    d = round(d, -1) if d < 1000 else round(d, -2)
    return f"{int(d)} Kilometer"


# --------------------------------------------------------------------------
# Senderliste
# --------------------------------------------------------------------------
def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def download_stations():
    query = "has_geo_info=true&hidebroken=true&limit=100000"
    data = None
    for host in API_SERVERS:
        try:
            req = urllib.request.Request(f"https://{host}/json/stations/search?{query}",
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
            break
        except Exception as e:
            log.warning("Senderliste von %s fehlgeschlagen: %s", host, e)
    if data is None:
        raise RuntimeError("Keine Senderliste erreichbar")
    out = []
    for s in data:
        lat, lon = s.get("geo_lat"), s.get("geo_long")
        url = s.get("url_resolved") or s.get("url")
        if lat is None or lon is None or not url:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            continue
        out.append({"n": clean(s.get("name")), "u": url, "c": clean(s.get("country")),
                    "s": "" if re.search(r"https?:|www\.", s.get("state") or "") else clean(s.get("state")), "lat": lat, "lon": lon})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False))
    tmp.rename(STATIONS_FILE)
    log.info("Senderliste: %d Sender mit Koordinaten", len(out))


class Stations:
    def __init__(self):
        self.lock = threading.Lock()
        self.items = []
        self.vecs = []
        self.load()

    def load(self):
        if not STATIONS_FILE.exists():
            return
        items = json.loads(STATIONS_FILE.read_text())
        vecs = [to_vec(s["lat"], s["lon"]) for s in items]
        with self.lock:
            self.items, self.vecs = items, vecs

    def stale(self):
        if not STATIONS_FILE.exists():
            return True
        return time.time() - STATIONS_FILE.stat().st_mtime > STATIONS_MAX_AGE_D * 86400

    def __len__(self):
        return len(self.items)

    def nearest(self, pos, k):
        """Die k nächsten Sender als Liste von (km, sender)."""
        v = to_vec(*pos)
        with self.lock:
            best = heapq.nlargest(k, range(len(self.vecs)), key=lambda i: dot(v, self.vecs[i]))
            items, vecs = self.items, self.vecs
        return [(EARTH_KM * math.acos(max(-1.0, min(1.0, dot(v, vecs[i])))), items[i])
                for i in best]

    def random_place(self):
        """Zufälliges Ziel: erst ein zufälliges Land, dann ein Sender darin –
        sonst schwebt der Satellit fast nur über Europa und den USA."""
        with self.lock:
            items = self.items
        if not items:
            return (random.uniform(-60, 70), random.uniform(-180, 180))
        country = random.choice(sorted({s["c"] for s in items}))
        s = random.choice([s for s in items if s["c"] == country])
        return (s["lat"], s["lon"])


# --------------------------------------------------------------------------
# Umlaufbahn
# --------------------------------------------------------------------------
class Orbit:
    """Die Bahn besteht aus Etappen: von A nach B in 2–6 Stunden, dann weiter."""

    def __init__(self, stations):
        self.stations = stations
        self.lock = threading.Lock()
        self.legs = []

    def _leg(self, start, t0):
        dur = random.uniform(LEG_MIN_H, LEG_MAX_H) * 3600
        return {"from": list(start), "to": list(self.stations.random_place()),
                "t0": t0, "t1": t0 + dur}

    def _update(self, now):
        # Immer aus der Datei lesen, damit Dienst und "--wo" dieselbe Bahn sehen.
        try:
            self.legs = json.loads(ORBIT_FILE.read_text())
        except Exception:
            self.legs = []
        changed = False
        if not self.legs or now - self.legs[-1]["t1"] > 2 * 86400:
            # Neuer Satellit: so tun, als flöge er schon seit 12 Stunden,
            # damit er eine Vergangenheit hat (für den Müll).
            self.legs = [self._leg(self.stations.random_place(), now - 12 * 3600)]
            changed = True
        while self.legs[-1]["t1"] <= now:
            last = self.legs[-1]
            self.legs.append(self._leg(last["to"], last["t1"]))
            changed = True
        if changed:
            self.legs = self.legs[-12:]
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = ORBIT_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.legs))
            tmp.rename(ORBIT_FILE)

    def position(self, t=None):
        with self.lock:
            now = time.time()
            self._update(now)
            t = now if t is None else t
            for leg in reversed(self.legs):
                if t >= leg["t0"]:
                    f = min(1.0, (t - leg["t0"]) / (leg["t1"] - leg["t0"]))
                    return slerp(tuple(leg["from"]), tuple(leg["to"]), f)
            return tuple(self.legs[0]["from"])

    def current_leg(self):
        with self.lock:
            self._update(time.time())
            return dict(self.legs[-1])


# --------------------------------------------------------------------------
# Sprache und Ton
# --------------------------------------------------------------------------
def tts_engine():
    if (PIPER_DIR / "piper" / "piper").exists() and \
            (PIPER_DIR / "voices" / f"{PIPER_VOICE}.onnx").exists():
        return "piper"
    return "pico" if shutil.which("pico2wave") else "espeak"


def tts_to_wav(text, out):
    engine = tts_engine()
    try:
        _tts(engine, text, out)
    except Exception:
        # Piper stürzt bei manchen fremden Namen ab -> Ersatzstimme
        if engine == "piper":
            log.info("Piper fehlgeschlagen, nehme Ersatzstimme")
            _tts("pico" if shutil.which("pico2wave") else "espeak", text, out)
        else:
            raise


def _tts(engine, text, out):
    if engine == "piper":
        cmd = ["nice", "-n", "5", str(PIPER_DIR / "piper" / "piper"),
               "--model", str(PIPER_DIR / "voices" / f"{PIPER_VOICE}.onnx"),
               "--output_file", str(out), "--length_scale", str(PIPER_LENGTH_SCALE),
               "--sentence_silence", "0.3"]
        subprocess.run(cmd, input=text.encode(), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif engine == "pico":
        subprocess.run(["pico2wave", "-l", "de-DE", "-w", str(out), text], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.run(["espeak-ng", "-v", "de", "-s", "150", "-w", str(out), text],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def audio_out_args():
    if sys.platform == "darwin":        # nur zum Testen am Mac
        return ["-f", "audiotoolbox", "-"]
    return ["-f", "alsa", "default"]


def place_text(st):
    return ", ".join(p for p in (st["s"], st["c"]) if p) or "unbekannt"


def radio_text(pos, km, st):
    if km < 150:
        return f"Über {place_text(st)}. {st['n']}."
    return f"Funkkontakt über {km_text(km)}. {place_text(st)}. {st['n']}."


# --------------------------------------------------------------------------
# Satellitenmüll
# --------------------------------------------------------------------------
def record_snippet(url, seconds, out):
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-rw_timeout", "15000000", "-i", url, "-t", str(seconds),
           "-ac", "1", "-ar", str(GARBAGE_RATE), "-c:a", "pcm_s16le", str(out)]
    try:
        subprocess.run(cmd, timeout=seconds + 30, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as e:
        log.info("Schnipsel fehlgeschlagen (%s): %s", url, getattr(e, "stderr", e))
        return False
    try:
        with wave.open(str(out), "rb") as w:
            return w.getnframes() > GARBAGE_RATE    # mindestens 1 s
    except Exception:
        return False


def read_samples(path):
    with wave.open(str(path), "rb") as w:
        return array.array("h", w.readframes(w.getnframes()))


def mix_garbage(clips, out):
    """Schnipsel überlappend aneinanderhängen, mit Rauschen, Knacksen und
    Kurzwellen-Schwund."""
    rate = GARBAGE_RATE
    total = sum(len(c) for c in clips) + rate * (2 * len(clips) + 2)
    buf = array.array("f", [0.0]) * total
    pos = int(rate * random.uniform(0.3, 0.8))
    end = pos
    for n_clip, s in enumerate(clips):
        n = len(s)
        gain = random.uniform(0.45, 0.95) / 32768
        fade = max(1, min(int(rate * random.uniform(0.05, 0.5)), n // 3))
        w = 2 * math.pi * random.uniform(0.08, 0.7) / rate      # Schwund-Tempo
        depth = random.uniform(0.15, 0.7)
        ph = random.uniform(0, 2 * math.pi)
        sin = math.sin
        for i in range(n):
            env = 1.0 - depth * (0.5 + 0.5 * sin(w * i + ph))
            if i < fade:
                env *= i / fade
            elif i > n - fade:
                env *= (n - i) / fade
            buf[pos + i] += s[i] * gain * env
        end = max(end, pos + n)
        # Rauschstoß am Übergang
        burst = int(rate * random.uniform(0.15, 0.7))
        amp = random.uniform(0.04, 0.14)
        b0 = max(0, pos + n - burst // 2)
        for i in range(b0, min(total, b0 + burst)):
            buf[i] += random.uniform(-amp, amp)
        end = max(end, min(total, b0 + burst))
        # nächster Schnipsel: überlappt (bis 1,2 s) oder kleine Lücke (bis 0,6 s)
        pos = max(0, pos + n - int(rate * random.uniform(-0.6, 1.2)))
    end = min(total, end + int(rate * 0.4))
    # durchgehendes Rauschen und Knacksen, dann begrenzen
    hiss = random.uniform(0.006, 0.02)
    crackle = 3.0 / rate
    samples = array.array("h", bytes(2 * end))
    rnd = random.random
    for i in range(end):
        x = buf[i] + (rnd() * 2 - 1) * hiss
        if rnd() < crackle:
            x += (rnd() * 2 - 1) * 0.5
        x = math.tanh(x * 1.2)
        samples[i] = int(x * 32000)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


# --------------------------------------------------------------------------
# LED-Ersatz ohne Hardware
# --------------------------------------------------------------------------
class NoLed:
    value = 0

    def on(self): pass
    def off(self): pass
    def blink(self, *a, **k): pass
    def pulse(self, *a, **k): pass


# --------------------------------------------------------------------------
# Der Satellit
# --------------------------------------------------------------------------
class Satellite:
    def __init__(self, hardware=True):
        for d in (DATA_DIR, GARBAGE_DIR, TMP_DIR):
            d.mkdir(parents=True, exist_ok=True)
        self.stations = Stations()
        self.orbit = Orbit(self.stations)
        self.stop = threading.Event()
        self.lock = threading.Lock()     # schützt die Felder darunter
        self.busy = False
        self.abort = False
        self.proc = None
        self.collecting = False
        self.held = False
        self.collect_lock = threading.Lock()
        if hardware:
            from gpiozero import Button, PWMLED
            self.led = PWMLED(LED_PIN)
            self.button = Button(BUTTON_PIN, bounce_time=0.05, hold_time=HOLD_S)
            self.button.when_held = self.on_held
            self.button.when_released = self.on_released
        else:
            self.led = NoLed()

    # ---------------- Knopf ----------------
    def on_held(self):
        self.held = True
        with self.lock:
            if self.busy:
                self._stop_playback()
                return
        self.start("muell")

    def on_released(self):
        if self.held:
            self.held = False
            return
        with self.lock:
            if self.busy:
                self._stop_playback()
                return
        self.start("radio")

    def _stop_playback(self):
        # mit self.lock aufrufen
        self.abort = True
        if self.proc:
            self.proc.terminate()

    def start(self, kind, wait=False):
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.abort = False
        t = threading.Thread(target=self._session, args=(kind,), daemon=True)
        t.start()
        if wait:
            t.join()

    # ---------------- Wiedergabe ----------------
    def _session(self, kind):
        try:
            self.led.blink(on_time=0.08, off_time=0.08)    # "sucht Signal"
            if not len(self.stations):
                self._say("Keine Senderliste.")
            elif kind == "radio":
                self._radio()
            else:
                self._garbage()
        except Exception:
            log.exception("Fehler bei der Wiedergabe")
        finally:
            with self.lock:
                self.busy = False
                self.proc = None
            self._led_idle()

    def _aborted(self):
        with self.lock:
            return self.abort or self.stop.is_set()

    def _play(self, src, volume, stream=False, max_s=0):
        """Spielt Datei oder Stream ab. Gibt die Laufzeit in Sekunden zurück."""
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"]
        if stream:
            cmd += ["-rw_timeout", "15000000", "-reconnect", "1",
                    "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]
        cmd += ["-i", str(src)]
        if max_s:
            cmd += ["-t", str(max_s)]
        cmd += ["-af", f"volume={volume}", "-ac", "2", "-ar", "48000"] + audio_out_args()
        with self.lock:
            if self.abort or self.stop.is_set():
                return 0.0
            self.led.pulse(fade_in_time=0.6, fade_out_time=0.6)
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            proc = self.proc
        t0 = time.monotonic()
        _, err = proc.communicate()
        ran = time.monotonic() - t0
        with self.lock:
            self.proc = None
            if proc.returncode not in (0, -signal.SIGTERM) and not self.abort:
                log.warning("ffmpeg rc=%s nach %.1f s: %s", proc.returncode, ran,
                            err.decode(errors="replace").strip()[-300:])
        return ran

    def _say(self, text):
        if not ANNOUNCE or self._aborted():
            return
        log.info("Ansage: %s", text)
        wav = TMP_DIR / f"ansage_{threading.get_ident()}.wav"
        try:
            tts_to_wav(text, wav)
            self._play(wav, ANNOUNCE_VOLUME)
        finally:
            wav.unlink(missing_ok=True)

    def _radio(self):
        pos = self.orbit.position()
        near = self.stations.nearest(pos, 40)
        dmin = near[0][0]
        same = [x for x in near if x[0] <= dmin + NEARBY_KM]
        random.shuffle(same)
        candidates = same + [x for x in near if x[0] > dmin + NEARBY_KM]
        log.info("Satellit bei %.2f, %.2f", *pos)
        for km, st in candidates[:5]:
            if self._aborted():
                return
            self._say(radio_text(pos, km, st))
            log.info("Sender: %s (%s, %.0f km) %s", st["n"], place_text(st), km, st["u"])
            ran = self._play(st["u"], RADIO_VOLUME, stream=True, max_s=RADIO_MAX_MIN * 60)
            if self._aborted() or ran > 10:
                return
            self._say("Kein Signal.")
        self._say("Funkstille.")

    def _garbage(self):
        files = sorted(GARBAGE_DIR.glob("*.wav"))
        if not files:
            self._say("Noch kein Müll im Orbit. Ich sammle.")
            if self._aborted():
                return
            path = self.collect_garbage()
            if not path:
                self._say("Funkstille.")
                return
        else:
            path = files[-1]
        info = {}
        try:
            info = json.loads(path.with_suffix(".json").read_text())
        except Exception:
            pass
        when = dt.datetime.fromtimestamp(info.get("zeit", path.stat().st_mtime))
        orte = []
        for o in info.get("orte", []):
            if o not in orte:
                orte.append(o)
        text = f"Satellitenmüll, {WOCHENTAGE[when.weekday()]} {when.hour} Uhr."
        if orte:
            text += " Eingesammelt über " + ", ".join(orte) + "."
        self._say(text)
        if not self.stop.wait(0.4):
            self._play(path, 1.0)

    # ---------------- Müll sammeln ----------------
    def collect_garbage(self):
        if not len(self.stations):
            return None
        with self.collect_lock:
            with self.lock:
                self.collecting = True
            self._led_idle()
            try:
                return self._collect()
            finally:
                with self.lock:
                    self.collecting = False
                self._led_idle()

    def _collect(self):
        now = time.time()
        n = GARBAGE_SOURCES
        points = [self.orbit.position(now - GARBAGE_TRACK_H * 3600 * i / max(1, n - 1))
                  for i in range(n)]
        used = set()
        results = [None] * n
        stamp = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d_%H-%M-%S")

        pools = []
        for p in points:
            pool = [st for _, st in self.stations.nearest(p, 20) if st["u"] not in used]
            pool = random.sample(pool[:8], len(pool[:8])) + pool[8:]
            if pool:
                used.add(pool[0]["u"])
            pools.append(pool)

        def worker(i):
            for st in pools[i][:3]:
                tmp = TMP_DIR / f"{stamp}_{i}.wav"
                secs = random.randint(SNIPPET_MIN_S, SNIPPET_MAX_S)
                if record_snippet(st["u"], secs, tmp):
                    results[i] = (tmp, st)
                    return
                tmp.unlink(missing_ok=True)

        threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        got = [r for r in results if r]
        if len(got) < 2:
            log.warning("Müll sammeln: nur %d Sender erreichbar", len(got))
            for tmp, _ in got:
                tmp.unlink(missing_ok=True)
            return None

        out = GARBAGE_DIR / f"{stamp}.wav"
        t0 = time.monotonic()
        mix_garbage([read_samples(tmp) for tmp, _ in got], out)
        for tmp, _ in got:
            tmp.unlink(missing_ok=True)
        meta = {"zeit": now, "orte": [st["c"] or place_text(st) for _, st in got],
                "sender": [{"name": st["n"], "ort": place_text(st), "url": st["u"]}
                           for _, st in got]}
        out.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
        log.info("Müll gesammelt (%.1f s gemischt): %s – %s", time.monotonic() - t0,
                 out.name, ", ".join(st["n"] for _, st in got))
        for old in sorted(GARBAGE_DIR.glob("*.wav"))[:-GARBAGE_KEEP]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
        return out

    def garbage_loop(self):
        first = not any(GARBAGE_DIR.glob("*.wav"))
        while not self.stop.is_set():
            if first:
                pause, first = 120, False
            else:
                pause = max(GARBAGE_PAUSE_MIN_S,
                            random.expovariate(1.0 / (GARBAGE_PAUSE_MEAN_H * 3600)))
            log.info("Nächster Müll in %.1f h", pause / 3600)
            if self.stop.wait(pause):
                return
            try:
                self.collect_garbage()
            except Exception:
                log.exception("Müll sammeln fehlgeschlagen")

    # ---------------- LED ----------------
    def _led_idle(self):
        with self.lock:
            if self.busy:
                return
            if self.collecting:
                self.led.value = 0.08     # ganz schwaches Glimmen = sammelt
            else:
                self.led.off()

    # ---------------- Ablauf ----------------
    def stations_loop(self):
        while not self.stop.is_set():
            if self.stations.stale():
                try:
                    download_stations()
                    self.stations.load()
                except Exception as e:
                    log.warning("Senderliste nicht aktualisiert: %s", e)
                    if self.stop.wait(1800):
                        return
                    continue
            if self.stop.wait(6 * 3600):
                return

    def shutdown(self, *_):
        log.info("Beende …")
        self.stop.set()
        with self.lock:
            self._stop_playback()

    def run(self):
        signal.signal(signal.SIGTERM, self.shutdown)
        signal.signal(signal.SIGINT, self.shutdown)
        self.led.blink(on_time=0.15, off_time=0.15, n=3, background=True)
        log.info("Bereit. %d Sender, Stimme: %s", len(self.stations), tts_engine())
        for target in (self.stations_loop, self.garbage_loop):
            threading.Thread(target=target, daemon=True).start()
        self.stop.wait()
        self.led.off()


# --------------------------------------------------------------------------
def where(sat):
    pos = sat.orbit.position()
    leg = sat.orbit.current_leg()
    rest = (leg["t1"] - time.time()) / 3600
    print(f"Satellit:  {pos[0]:.2f}, {pos[1]:.2f}")
    print(f"Ziel:      {leg['to'][0]:.2f}, {leg['to'][1]:.2f}  (noch {rest:.1f} h, "
          f"{dist_km(pos, tuple(leg['to'])):.0f} km)")
    print("Nächste Sender:")
    for km, st in sat.stations.nearest(pos, 8):
        print(f"  {km:6.0f} km  {st['n']}  ({place_text(st)})")


def main():
    ap = argparse.ArgumentParser(description="Satellit-Radio")
    ap.add_argument("--wo", action="store_true", help="Position und nahe Sender zeigen")
    ap.add_argument("--radio", action="store_true", help="Sender abspielen (ohne Knopf)")
    ap.add_argument("--muell", action="store_true", help="Müll sammeln und abspielen")
    ap.add_argument("--update", action="store_true", help="Senderliste neu laden")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.update:
        download_stations()
    if not (args.wo or args.radio or args.muell):
        if args.update:
            return
        sat = Satellite(hardware=True)
        if not len(sat.stations):
            try:
                download_stations()
                sat.stations.load()
            except Exception as e:
                log.warning("Senderliste nicht geladen: %s", e)
        sat.run()
        return

    sat = Satellite(hardware=False)
    if not len(sat.stations):
        download_stations()
        sat.stations.load()
    signal.signal(signal.SIGINT, sat.shutdown)
    if args.wo:
        where(sat)
    if args.muell:
        sat.collect_garbage()
        sat.start("muell", wait=True)
    elif args.radio:
        sat.start("radio", wait=True)


if __name__ == "__main__":
    main()
