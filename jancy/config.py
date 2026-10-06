"""Settings and history storage (in %APPDATA%\\Jancy). The API key is encrypted with Windows DPAPI."""
import base64
import ctypes
import json
import os
import threading
import time
from ctypes import wintypes
from pathlib import Path

APP_NAME = "Jancy"
APP_VERSION = "2.0.0"
CONFIG_VERSION = 2
DATA_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
CONFIG_PATH = DATA_DIR / "config.json"
HISTORY_PATH = DATA_DIR / "history.json"

LISTEN_MODES = {
    "always": "Always listening (just talk)",
    "wake": "Wake word (say “Hey Jancy” first)",
}

WIDGET_POSITIONS = {
    "bottom-center": "Bottom center", "bottom-right": "Bottom right", "bottom-left": "Bottom left",
    "top-center": "Top center", "top-right": "Top right", "top-left": "Top left",
}

LANGUAGES = {
    "en": "English", "": "Auto-detect", "ta": "Tamil", "hi": "Hindi", "te": "Telugu",
    "ml": "Malayalam", "kn": "Kannada", "bn": "Bengali", "mr": "Marathi", "gu": "Gujarati",
    "ur": "Urdu", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "ar": "Arabic", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
}

STT_MODELS = ["whisper-large-v3-turbo", "whisper-large-v3"]
CHAT_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "api_key_enc": "",
    "listen_mode": "always",
    "muted": False,
    "language": "en",
    "microphone": "",
    "widget_position": "bottom-center",
    "show_widget": True,
    "speak_replies": True,
    "voice": "Microsoft Zira Desktop",
    "start_with_windows": True,
    "sounds": True,
    "stt_model": STT_MODELS[0],
    "chat_model": CHAT_MODELS[0],
    "welcomed": False,
}


# ---------------------------------------------------------------- DPAPI

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


# ---------------------------------------------------------------- Config

class Config:
    def __init__(self):
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        saved = {}
        try:
            saved = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            pass
        if saved and saved.get("config_version", 1) < CONFIG_VERSION:
            # v1 was a hotkey dictation app; keep only what still applies.
            saved = {k: v for k, v in saved.items()
                     if k in ("api_key_enc", "microphone", "speak_replies", "voice", "sounds", "welcomed")}
        self.data.update(saved)
        self.data["config_version"] = CONFIG_VERSION

    def get(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value
        self.save()

    def save(self):
        with self._lock:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), "utf-8")
            tmp.replace(CONFIG_PATH)

    @property
    def api_key(self) -> str:
        enc = self.get("api_key_enc")
        if not enc:
            return ""
        try:
            return _dpapi(base64.b64decode(enc), protect=False).decode()
        except Exception:
            return ""

    def set_api_key(self, key: str):
        key = key.strip()
        self.set("api_key_enc", base64.b64encode(_dpapi(key.encode(), protect=True)).decode() if key else "")


# ---------------------------------------------------------------- History

class History:
    MAX = 1000

    def __init__(self):
        self._lock = threading.Lock()
        try:
            self.items = json.loads(HISTORY_PATH.read_text("utf-8"))
        except (OSError, ValueError):
            self.items = []

    def add(self, text, reply=""):
        with self._lock:
            self.items.append({"ts": time.time(), "mode": "command", "text": text, "reply": reply})
            self.items = self.items[-self.MAX:]
            self._save()

    def clear(self):
        with self._lock:
            self.items = []
            self._save()

    def _save(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False), "utf-8")
        tmp.replace(HISTORY_PATH)

    def stats(self):
        today = time.strftime("%Y-%m-%d")
        commands = [i for i in self.items if i["mode"] == "command"]
        return {
            "total": len(commands),
            "today": sum(1 for i in commands if time.strftime("%Y-%m-%d", time.localtime(i["ts"])) == today),
        }
