"""Microphone recording, UI sound effects and text-to-speech."""
import base64
import collections
import io
import logging
import math
import subprocess
import threading
import time
import wave
import winsound

import numpy as np
import sounddevice as sd

from .config import DATA_DIR

RATE = 16000
NO_WINDOW = subprocess.CREATE_NO_WINDOW
log = logging.getLogger("jancy")


def to_wav(frames):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(np.concatenate(frames).tobytes())
    return buf.getvalue()


def _level(rms):
    return min(1.0, math.log10(rms + 1) / math.log10(3000))


def list_microphones():
    """Input device names for the default host API (avoids MME/WASAPI duplicates)."""
    try:
        default_in = sd.default.device[0]
        hostapi = sd.query_devices(default_in)["hostapi"] if default_in is not None and default_in >= 0 else 0
        names = []
        for d in sd.query_devices():
            if d["max_input_channels"] > 0 and d["hostapi"] == hostapi and d["name"] not in names:
                names.append(d["name"])
        return names
    except Exception:
        return []


def _device_index(name):
    if not name:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d["max_input_channels"] > 0:
            return i
    return None


class Recorder:
    def __init__(self):
        self.stream = None
        self.frames = []
        self.level = 0.0   # 0..1, for the waveform UI
        self.peak = 0.0

    def start(self, device_name=""):
        self.frames, self.level, self.peak = [], 0.0, 0.0
        self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=640,
                                     device=_device_index(device_name), callback=self._callback)
        self.stream.start()

    def _callback(self, indata, frames, t, status):
        self.frames.append(indata.copy())
        rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        self.peak = max(self.peak, rms)
        self.level = _level(rms)

    def _close(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.level = 0.0

    def stop(self):
        """Returns (wav_bytes, seconds, peak_rms)."""
        self._close()
        if not self.frames:
            return b"", 0.0, 0.0
        seconds = sum(len(f) for f in self.frames) / RATE
        return to_wav(self.frames), seconds, self.peak

    def cancel(self):
        self._close()
        self.frames = []


class WakeListener(threading.Thread):
    """Always-on microphone: detects spoken phrases (voice activity) and hands them to a callback."""

    BLOCK = 480                # 30 ms
    SILENCE_END = 0.8          # seconds of quiet that ends a phrase
    MIN_SPEECH = 0.25          # seconds of actual loud audio required
    MAX_PHRASE = 15

    def __init__(self, on_phrase, is_paused, device_name=""):
        super().__init__(daemon=True)
        self.on_phrase = on_phrase
        self.is_paused = is_paused
        self.device_name = device_name
        self.level = 0.0
        self.running = True

    def run(self):
        while self.running:
            try:
                with sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=self.BLOCK,
                                    device=_device_index(self.device_name)) as stream:
                    log.info("wake listener: microphone open")
                    self._loop(stream)
            except Exception as e:
                log.warning("wake listener: microphone error: %s", e)
                time.sleep(3)

    def _loop(self, stream):
        bs = self.BLOCK / RATE
        noise = None
        preroll = collections.deque(maxlen=int(0.35 / bs))
        frames, recording, silent, loud_blocks = [], False, 0, 0
        while self.running:
            data = stream.read(self.BLOCK)[0].copy()
            rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))
            self.level = _level(rms)
            if self.is_paused():
                frames, recording = [], False
                preroll.clear()
                continue
            if noise is None:
                noise = rms
            loud = rms > max(noise * 3.5, 120.0)
            if not recording:
                if not loud:
                    noise = noise * 0.97 + rms * 0.03
                preroll.append(data)
                if loud:
                    recording, frames, silent, loud_blocks = True, list(preroll), 0, 1
                continue
            frames.append(data)
            if loud:
                silent, loud_blocks = 0, loud_blocks + 1
            else:
                silent += 1
            duration = len(frames) * bs
            if silent * bs >= self.SILENCE_END or duration >= self.MAX_PHRASE:
                recording = False
                preroll.clear()
                if loud_blocks * bs >= self.MIN_SPEECH:
                    self.on_phrase(to_wav(frames), duration)

    def stop(self):
        self.running = False


# ---------------------------------------------------------------- Sound effects

def _tone(path, freqs, dur=0.07, volume=0.18):
    samples = []
    for f in freqs:
        n = int(RATE * dur)
        t = np.arange(n) / RATE
        env = np.minimum(1, np.minimum(t / 0.008, (dur - t) / 0.03))
        samples.append(np.sin(2 * np.pi * f * t) * env * volume)
    data = (np.concatenate(samples) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


class Sounds:
    def __init__(self):
        self.dir = DATA_DIR / "sounds"
        self.files = {"start": (660, 990), "stop": (990, 660), "error": (300, 220)}
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            for name, freqs in self.files.items():
                path = self.dir / f"{name}.wav"
                if not path.exists():
                    _tone(path, freqs)
        except Exception:
            pass

    def play(self, name):
        try:
            winsound.PlaySound(str(self.dir / f"{name}.wav"),
                               winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass


# ---------------------------------------------------------------- Text-to-speech

_TTS_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 1
$reader = New-Object IO.StreamReader([Console]::OpenStandardInput())
$task = $reader.ReadLineAsync()
$was = $false
while ($true) {
  if ($task.IsCompleted) {
    $l = $task.Result
    if ($l -eq $null) { break }
    if ($l.StartsWith('V:')) { try { $s.SelectVoice($l.Substring(2)) } catch {} }
    elseif ($l -eq 'X') { $s.SpeakAsyncCancelAll() }
    elseif ($l.StartsWith('S:')) {
      $s.SpeakAsyncCancelAll()
      $t = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($l.Substring(2)))
      [void]$s.SpeakAsync($t)
    }
    $task = $reader.ReadLineAsync()
  }
  $now = $s.State -eq 'Speaking'
  if ($was -and -not $now) { [Console]::Out.WriteLine('D') }
  $was = $now
  Start-Sleep -Milliseconds 40
}
"""


class Speaker:
    """Windows built-in speech, kept warm in a background PowerShell process.
    The process prints 'D' whenever it finishes speaking, so we know when the mic can listen again."""

    def __init__(self):
        self.proc = None
        self.voice = None
        self.lock = threading.Lock()
        self.speaking = False
        self.deadline = 0.0

    def _send(self, line):
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                encoded = base64.b64encode(_TTS_SCRIPT.encode("utf-16-le")).decode()
                self.proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=NO_WINDOW,
                )
                threading.Thread(target=self._read_events, args=(self.proc,), daemon=True).start()
                if self.voice:
                    self.proc.stdin.write(f"V:{self.voice}\n".encode())
            self.proc.stdin.write((line + "\n").encode())
            self.proc.stdin.flush()

    def _read_events(self, proc):
        for line in proc.stdout:
            if line.strip() == b"D":
                self.speaking = False

    def set_voice(self, voice):
        self.voice = voice
        if self.proc and self.proc.poll() is None:
            self._send(f"V:{voice}")

    def say(self, text):
        if text.strip():
            self.speaking = True
            # safety net in case the 'done' event is missed: ~2 words/sec + startup
            self.deadline = time.time() + 3 + len(text.split()) / 2.0
            self._send("S:" + base64.b64encode(text.encode("utf-8")).decode())

    def is_speaking(self):
        return self.speaking and time.time() < self.deadline

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self._send("X")

    def warm_up(self):
        threading.Thread(target=lambda: self._send("X"), daemon=True).start()

    def shutdown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()


def list_voices():
    script = ("Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer)"
              ".GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                             text=True, timeout=20, creationflags=NO_WINDOW).stdout
        return [v.strip() for v in out.splitlines() if v.strip()]
    except Exception:
        return []
