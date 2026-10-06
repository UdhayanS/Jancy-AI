"""Desktop AI voice assistant: talk to it (or type) and it controls your PC.

Speech-to-text: Groq Whisper. Brain: Groq LLM with tool calling. Voice replies: Windows speech.

Say or type things like:
  - "open notepad and type hello world"
  - "open chrome and search for weather in chennai"
  - "open youtube and search lofi music"
  - "press ctrl+t" / "scroll down" / "close this window"

Safety: move the mouse to any screen corner to abort keyboard automation (pyautogui failsafe).
"""
import collections
import io
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
import webbrowser
import winreg
from tkinter import scrolledtext

import numpy as np
import pyautogui
import pyperclip
import sounddevice as sd

API_URL = "https://api.groq.com/openai/v1/chat/completions"
STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "openai/gpt-oss-120b"
STT_MODEL = "whisper-large-v3-turbo"
STT_LANGUAGE = "en"      # language you speak in; None = auto-detect
WAKE_WORD = ""           # e.g. "jarvis" to only react when addressed; "" = react to everything
TTS_VOICE = "Microsoft Zira Desktop"
MAX_STEPS = 15

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

SYSTEM_PROMPT = """You are a desktop assistant running on the user's Windows PC. You control the \
computer with the provided tools. When the user asks you to do something, DO it with tools \
instead of explaining how.

The user usually talks to you by voice, so their message is a speech transcript and may contain \
recognition mistakes; interpret it sensibly (e.g. "open crome" means Chrome). Your replies are read \
aloud, so write plain conversational text: no markdown, lists, emojis or URLs.

Guidelines:
- After opening an application or website, call wait (1-3 seconds) before typing into it.
- type_text and press_keys act on whichever window is currently focused.
- To search inside a site, prefer building the search URL directly, e.g.
  YouTube: https://www.youtube.com/results?search_query=...
  Google:  use search_web.
- New tab: press_keys "ctrl+t". Close window: "alt+f4". Address bar: "ctrl+l".
- You cannot see the screen or click with the mouse; use keyboard shortcuts instead.
- Keep final replies short (one or two sentences) and say what you did."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_application",
            "description": "Open an installed application by name, e.g. 'notepad', 'chrome', 'calculator', 'spotify', 'file explorer', 'settings'.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_website",
            "description": "Open a URL in a web browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "browser": {"type": "string", "enum": ["default", "chrome", "edge", "firefox"]},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search Google for a query in a web browser.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "browser": {"type": "string", "enum": ["default", "chrome", "edge", "firefox"]},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text into the currently focused window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "press_enter": {"type": "boolean", "description": "Press Enter after typing."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_keys",
            "description": "Press a key or key combination, e.g. 'enter', 'ctrl+t', 'alt+f4', 'win+d', 'ctrl+shift+n'.",
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "string"}},
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Scroll the focused window up or down.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {"type": "string", "enum": ["up", "down"]},
                    "amount": {"type": "integer", "description": "Number of scroll steps (default 5)."},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait a number of seconds (max 10) for apps or pages to load.",
            "parameters": {
                "type": "object",
                "properties": {"seconds": {"type": "number"}},
                "required": ["seconds"],
            },
        },
    },
]


def load_env(path):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


# ---------------------------------------------------------------- PC actions

def load_start_apps():
    """Map of Start-menu app name -> AppID (covers normal and Microsoft Store apps)."""
    cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, encoding="utf-8", timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout
        data = json.loads(out) if out.strip() else []
    except Exception:
        return {}
    if isinstance(data, dict):
        data = [data]
    return {a["Name"]: a["AppID"] for a in data if a.get("Name") and a.get("AppID")}


START_APPS = {}
APP_ALIASES = {
    "chrome": "google chrome",
    "explorer": "file explorer",
    "files": "file explorer",
    "word": "word",
    "vscode": "visual studio code",
    "vs code": "visual studio code",
    "edge": "microsoft edge",
}
EXE_FALLBACKS = {
    "cmd": "cmd", "command prompt": "cmd", "terminal": "wt", "powershell": "powershell",
    "notepad": "notepad", "calculator": "calc", "paint": "mspaint", "file explorer": "explorer",
    "task manager": "taskmgr", "control panel": "control",
}


def find_start_app(name):
    name = APP_ALIASES.get(name, name)
    best, best_score = None, None
    for app_name in START_APPS:
        lower = app_name.lower()
        if lower == name:
            score = 0
        elif lower.startswith(name):
            score = 1
        elif name in lower.split():
            score = 2
        elif name in lower:
            score = 3
        else:
            continue
        key = (score, len(lower))
        if best_score is None or key < best_score:
            best, best_score = app_name, key
    return best


def open_application(name):
    query = name.strip().lower()
    if query in ("settings", "windows settings"):
        os.startfile("ms-settings:")
        return "Opened Settings."
    app = find_start_app(query)
    if app:
        os.startfile("shell:AppsFolder\\" + START_APPS[app])
        return f"Opened {app}."
    exe = EXE_FALLBACKS.get(query, query)
    if shutil.which(exe):
        subprocess.Popen([exe], creationflags=subprocess.CREATE_NEW_CONSOLE if exe in ("cmd", "powershell") else 0)
        return f"Opened {exe}."
    return f"Could not find an application named '{name}'."


BROWSER_EXES = {"chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe"}


def find_browser(browser):
    exe = BROWSER_EXES.get(browser)
    if not exe:
        return None
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub) as key:
                path = winreg.QueryValue(key, None)
                if path and os.path.exists(path.strip('"')):
                    return path.strip('"')
        except OSError:
            pass
    return None


def open_website(url, browser="default"):
    if "://" not in url:
        url = "https://" + url
    path = find_browser(browser) if browser != "default" else None
    if path:
        subprocess.Popen([path, url])
        return f"Opened {url} in {browser}."
    webbrowser.open(url)
    note = f" ({browser} not found, used default browser)" if browser not in ("default", None) else ""
    return f"Opened {url}{note}."


def search_web(query, browser="default"):
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    open_website(url, browser)
    return f"Searched Google for '{query}'."


def type_text(text, press_enter=False):
    APP.hide_for_keyboard()
    if text.isascii():
        pyautogui.write(text, interval=0.01)
    else:
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
    if press_enter:
        pyautogui.press("enter")
    return "Typed the text." + (" Pressed Enter." if press_enter else "")


KEY_ALIASES = {"control": "ctrl", "windows": "win", "escape": "esc", "return": "enter",
               "cmd": "win", "del": "delete", "pgdn": "pagedown", "pgup": "pageup"}


def press_keys(keys):
    parts = [KEY_ALIASES.get(k.strip().lower(), k.strip().lower()) for k in keys.split("+")]
    bad = [k for k in parts if k not in pyautogui.KEYBOARD_KEYS]
    if bad:
        return f"Unknown key(s): {', '.join(bad)}"
    APP.hide_for_keyboard()
    pyautogui.hotkey(*parts)
    return f"Pressed {keys}."


def scroll(direction, amount=5):
    APP.hide_for_keyboard()
    clicks = max(1, min(int(amount or 5), 50)) * 120
    pyautogui.scroll(clicks if direction == "up" else -clicks)
    return f"Scrolled {direction}."


def wait(seconds):
    seconds = max(0, min(float(seconds), 10))
    time.sleep(seconds)
    return f"Waited {seconds:g} seconds."


TOOL_FUNCS = {
    "open_application": open_application,
    "open_website": open_website,
    "search_web": search_web,
    "type_text": type_text,
    "press_keys": press_keys,
    "scroll": scroll,
    "wait": wait,
}


# ---------------------------------------------------------------- Groq API

def call_groq(messages, api_key):
    body = json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS, "tool_choice": "auto"}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "desktop-assistant/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)["choices"][0]["message"]


def transcribe(wav_bytes, api_key):
    boundary = uuid.uuid4().hex
    fields = {"model": STT_MODEL, "response_format": "json", "temperature": "0"}
    if STT_LANGUAGE:
        fields["language"] = STT_LANGUAGE
    body = b""
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\n'
             f"Content-Type: audio/wav\r\n\r\n").encode() + wav_bytes + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        STT_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "desktop-assistant/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp).get("text", "").strip()


# Whisper tends to "hear" these in silence/noise; ignore them.
HALLUCINATIONS = {"", "you", "thank you", "thanks", "thank you for watching", "thanks for watching",
                  "bye", "okay", "ok", "so", "uh", "um", "hmm"}


def is_noise(text):
    return re.sub(r"[^a-z ]", "", text.lower()).strip() in HALLUCINATIONS


# ---------------------------------------------------------------- Voice

class Speaker:
    """Speaks text with the built-in Windows voice (offline)."""

    def __init__(self):
        self.proc = None

    def say(self, text):
        text = re.sub(r"[*_`#>]|https?://\S+", "", text).strip()
        if not text:
            return
        script = (
            "[Console]::InputEncoding=[Text.Encoding]::UTF8;"
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"try {{ $s.SelectVoice('{TTS_VOICE}') }} catch {{}};"
            "$s.Rate=1; $s.Speak([Console]::In.ReadToEnd())"
        )
        self.proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", script],
            stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.proc.communicate(text.encode("utf-8"))
        self.proc = None

    def stop(self):
        if self.proc:
            self.proc.kill()


class Listener(threading.Thread):
    """Listens to the microphone and hands each spoken phrase (as WAV bytes) to a callback."""

    RATE = 16000
    BLOCK = 480                # 30 ms
    SILENCE_END = 0.9          # seconds of silence that ends a phrase
    MIN_PHRASE = 0.4           # ignore shorter blips
    MAX_PHRASE = 20

    def __init__(self, on_phrase, on_state, is_paused):
        super().__init__(daemon=True)
        self.on_phrase = on_phrase
        self.on_state = on_state
        self.is_paused = is_paused
        self.running = True

    def run(self):
        block_sec = self.BLOCK / self.RATE
        try:
            with sd.InputStream(samplerate=self.RATE, channels=1, dtype="int16", blocksize=self.BLOCK) as stream:
                self.on_state("Calibrating microphone... stay quiet")
                levels = [self.rms(stream.read(self.BLOCK)[0]) for _ in range(int(0.8 / block_sec))]
                threshold = max(float(np.median(levels)) * 3.0, 150.0)

                preroll = collections.deque(maxlen=int(0.3 / block_sec))
                frames, silent, recording, was_paused = [], 0, False, True
                while self.running:
                    data = stream.read(self.BLOCK)[0].copy()
                    if self.is_paused():
                        frames, silent, recording, was_paused = [], 0, False, True
                        preroll.clear()
                        continue
                    if was_paused:
                        was_paused = False
                        self.on_state("Listening... speak now")
                    loud = self.rms(data) > threshold
                    if not recording:
                        preroll.append(data)
                        if loud:
                            recording, frames, silent = True, list(preroll), 0
                            self.on_state("Hearing you...")
                        continue
                    frames.append(data)
                    silent = 0 if loud else silent + 1
                    duration = len(frames) * block_sec
                    if silent * block_sec >= self.SILENCE_END or duration >= self.MAX_PHRASE:
                        recording = False
                        preroll.clear()
                        if duration - silent * block_sec >= self.MIN_PHRASE:
                            self.on_phrase(self.to_wav(frames))
                            was_paused = True
                        else:
                            self.on_state("Listening... speak now")
        except Exception as e:
            self.on_state(f"Microphone error: {e}")

    @staticmethod
    def rms(data):
        return float(np.sqrt(np.mean(data.astype(np.float32) ** 2)))

    def to_wav(self, frames):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.RATE)
            w.writeframes(np.concatenate(frames).tobytes())
        return buf.getvalue()

    def stop(self):
        self.running = False


# ---------------------------------------------------------------- UI

class App:
    def __init__(self, root, api_key):
        self.root = root
        self.api_key = api_key
        self.ui_queue = queue.Queue()
        self.busy = False
        self.hidden = False
        self.listener = None
        self.speaker = Speaker()
        self.voice_replies = tk.BooleanVar(value=True)
        self.speak_on = True  # plain copy of voice_replies, safe to read from worker threads
        self.voice_replies.trace_add("write", lambda *_: setattr(self, "speak_on", self.voice_replies.get()))
        self.reset_history()

        root.title("Voice Assistant")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.geometry("520x620")
        root.configure(bg="#1e1f24")

        self.log = scrolledtext.ScrolledText(
            root, wrap="word", state="disabled", bg="#1e1f24", fg="#e6e6e6",
            font=("Segoe UI", 11), relief="flat", padx=12, pady=12, insertbackground="#e6e6e6",
        )
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("you", foreground="#7cc4ff", font=("Segoe UI", 11, "bold"))
        self.log.tag_config("bot", foreground="#9be59b", font=("Segoe UI", 11, "bold"))
        self.log.tag_config("action", foreground="#c9a85a", font=("Consolas", 10))
        self.log.tag_config("error", foreground="#ff7b7b")

        bottom = tk.Frame(root, bg="#2a2c33")
        bottom.pack(fill="x")
        top_row = tk.Frame(bottom, bg="#2a2c33")
        top_row.pack(fill="x", padx=10, pady=(8, 0))
        self.mic_btn = tk.Button(top_row, text="ðŸŽ¤  Start listening", command=self.toggle_listening,
                                 bg="#16a34a", fg="white", relief="flat", padx=12, font=("Segoe UI", 10, "bold"))
        self.mic_btn.pack(side="left")
        tk.Button(top_row, text="Stop talking", command=self.speaker.stop, bg="#4b4f58", fg="white",
                  relief="flat", padx=10).pack(side="left", padx=(6, 0))
        tk.Checkbutton(top_row, text="Speak replies", variable=self.voice_replies, bg="#2a2c33", fg="#e6e6e6",
                       selectcolor="#3a3d45", activebackground="#2a2c33", activeforeground="white"
                       ).pack(side="left", padx=(10, 0))
        self.status = tk.Label(bottom, text="", bg="#2a2c33", fg="#9aa0a6", font=("Segoe UI", 10), anchor="w")
        self.status.pack(fill="x", padx=10, pady=(6, 0))
        row = tk.Frame(bottom, bg="#2a2c33")
        row.pack(fill="x", padx=10, pady=8)
        self.entry = tk.Entry(row, font=("Segoe UI", 12), bg="#3a3d45", fg="white",
                              insertbackground="white", relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.entry.bind("<Return>", lambda _: self.send())
        tk.Button(row, text="Send", command=self.send, bg="#3b82f6", fg="white",
                  relief="flat", padx=14).pack(side="left", padx=(8, 0))
        tk.Button(row, text="Clear", command=self.clear, bg="#4b4f58", fg="white",
                  relief="flat", padx=10).pack(side="left", padx=(6, 0))
        self.entry.focus_set()

        hint = f'Say "{WAKE_WORD}, ..." then your command' if WAKE_WORD else "Just speak your command"
        self.write("Bot: ", "bot")
        self.write(f"Hi! {hint}, e.g. \"open chrome and search for cricket score\". You can also type.\n\n")
        self.set_status("Loading app list...")
        threading.Thread(target=self.load_apps, daemon=True).start()
        self.poll_queue()

    # --- thread-safe UI helpers
    def poll_queue(self):
        while not self.ui_queue.empty():
            fn, done = self.ui_queue.get()
            fn()
            if done:
                done.set()
        self.root.after(50, self.poll_queue)

    def ui(self, fn, wait=False):
        done = threading.Event() if wait else None
        self.ui_queue.put((fn, done))
        if done:
            done.wait(5)

    def write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.configure(state="disabled")
        self.log.see("end")

    def set_status(self, text):
        self.status.configure(text=text)

    def hide_for_keyboard(self):
        """Minimize this window so keystrokes go to the app behind it."""
        if not self.hidden:
            self.ui(self.root.iconify, wait=True)
            self.hidden = True
            time.sleep(0.6)

    # --- actions
    def load_apps(self):
        START_APPS.update(load_start_apps())
        self.ui(self.toggle_listening)

    # --- voice
    def toggle_listening(self):
        if self.listener:
            self.listener.stop()
            self.listener = None
            self.mic_btn.configure(text="ðŸŽ¤  Start listening", bg="#16a34a")
            self.set_status("Mic off. Type a command or click Start listening.")
            return
        self.listener = Listener(
            on_phrase=self.on_phrase,
            on_state=lambda s: self.ui(lambda: self.set_status(s)),
            is_paused=lambda: self.busy,
        )
        self.listener.start()
        self.mic_btn.configure(text="ðŸ”´  Stop listening", bg="#dc2626")

    def on_phrase(self, wav):
        """Called from the listener thread with one spoken phrase."""
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self.handle_voice, args=(wav,), daemon=True).start()

    def handle_voice(self, wav):
        self.ui(lambda: self.set_status("Transcribing..."))
        try:
            text = transcribe(wav, self.api_key)
        except Exception as e:
            err = f"Speech recognition failed: {e}"
            self.ui(lambda: self.write(err + "\n\n", "error"))
            self.ui(self.finish)
            return
        if WAKE_WORD:
            match = re.search(rf"\b{re.escape(WAKE_WORD)}\b[\s,.!?]*", text, re.I)
            text = text[match.end():].strip() if match else ""
        if is_noise(text):
            self.ui(self.finish)
            return
        self.ui(lambda: (self.write("You ðŸŽ¤: ", "you"), self.write(text + "\n\n"), self.set_status("Thinking...")))
        self.run_agent(text)

    def reset_history(self):
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def clear(self):
        if self.busy:
            return
        self.reset_history()
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def send(self):
        text = self.entry.get().strip()
        if not text or self.busy:
            return
        self.entry.delete(0, "end")
        self.write("You: ", "you")
        self.write(text + "\n\n")
        self.busy = True
        self.set_status("Thinking...")
        threading.Thread(target=self.run_agent, args=(text,), daemon=True).start()

    def run_agent(self, text):
        self.messages.append({"role": "user", "content": text})
        reply = None
        try:
            for _ in range(MAX_STEPS):
                msg = call_groq(self.messages, self.api_key)
                calls = msg.get("tool_calls") or []
                entry = {"role": "assistant", "content": msg.get("content") or ""}
                if calls:
                    entry["tool_calls"] = calls
                self.messages.append(entry)
                if not calls:
                    reply = msg.get("content") or "Done."
                    self.ui(lambda: (self.write("Bot: ", "bot"), self.write(reply + "\n\n")))
                    break
                for call in calls:
                    result = self.run_tool(call)
                    self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            else:
                self.ui(lambda: self.write("Stopped: too many steps.\n\n", "error"))
        except urllib.error.HTTPError as e:
            err = f"API error {e.code}: {e.read().decode(errors='replace')}"
            self.ui(lambda: self.write(err + "\n\n", "error"))
            reply = "Sorry, something went wrong."
        except Exception as e:
            err = f"Error: {e}"
            self.ui(lambda: self.write(err + "\n\n", "error"))
            reply = "Sorry, something went wrong."
        try:
            if reply and self.speak_on:
                self.ui(lambda: self.set_status("Speaking..."))
                self.speaker.say(reply)
        finally:
            self.ui(self.finish)

    def run_tool(self, call):
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        label = f"â†’ {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})\n"
        self.ui(lambda: (self.write(label, "action"), self.set_status(f"Running {name}...")))
        func = TOOL_FUNCS.get(name)
        if not func:
            return f"Unknown tool {name}"
        try:
            return func(**args)
        except pyautogui.FailSafeException:
            raise RuntimeError("Aborted (mouse moved to screen corner).")
        except Exception as e:
            return f"Error: {e}"

    def finish(self):
        self.busy = False
        self.set_status("Listening... speak now" if self.listener else "Ready.")
        if self.hidden:
            self.hidden = False
            self.root.deiconify()
        self.entry.focus_set()

    def quit(self):
        if self.listener:
            self.listener.stop()
        self.speaker.stop()
        self.root.destroy()


APP = None


def main():
    global APP
    load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise SystemExit("GROQ_API_KEY not set (put it in .env)")
    root = tk.Tk()
    APP = App(root, api_key)
    root.mainloop()


if __name__ == "__main__":
    main()
