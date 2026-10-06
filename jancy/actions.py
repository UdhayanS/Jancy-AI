"""Things Jancy can do on the PC: apps, windows, websites, clicking on-screen items, typing, keys, media."""
import ctypes
import difflib
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import uuid
import webbrowser
import winreg
from ctypes import wintypes

import pyperclip
import uiautomation as auto
from pynput.keyboard import Controller as KeyController, Key
from pynput.mouse import Controller as MouseController

from .config import DATA_DIR

kb = KeyController()
mouse = MouseController()
NO_WINDOW = subprocess.CREATE_NO_WINDOW
user32 = ctypes.windll.user32
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    auto.Logger.SetLogFile(str(DATA_DIR / "uia.log"))
except Exception:
    pass

# Set by the app so the assistant can mute itself ("stop listening").
on_mute = None


# ---------------------------------------------------------------- focus / windows

def foreground_info():
    """(window class, exe name, title) of the focused window."""
    hwnd = user32.GetForegroundWindow()
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, 512)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
    if handle:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            exe = os.path.basename(buf.value).lower()
        ctypes.windll.kernel32.CloseHandle(handle)
    return cls.value, exe, title.value


def _bring_to_front(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Windows only lets the process that received the last input change focus; a synthetic Alt press grants that.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(0xE8, 0, 0, 0)   # unassigned key so the Alt tap doesn't open a menu bar
    user32.keybd_event(0xE8, 0, 2, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.3)


def _top_windows():
    wins = []
    for w in auto.GetRootControl().GetChildren():
        try:
            if w.ControlTypeName == "WindowControl" and w.Name and not w.IsOffscreen:
                wins.append(w)
        except Exception:
            pass
    return wins


def _score(query, name):
    q, n = query.lower().strip(), name.lower().strip()
    if not q or not n:
        return 0
    if q == n:
        return 100
    if n.startswith(q):
        return 85
    if q in n:
        return 70 - min(20, (len(n) - len(q)) // 5)
    return int(difflib.SequenceMatcher(None, q, n).ratio() * 60)


def list_windows():
    with auto.UIAutomationInitializerInThread():
        names = [w.Name for w in _top_windows()][:40]
    return "Open windows:\n" + "\n".join(f"- {n}" for n in names) if names else "No open windows found."


def switch_to_window(name):
    with auto.UIAutomationInitializerInThread():
        wins = _top_windows()
        best = max(wins, key=lambda w: _score(name, w.Name), default=None)
        if not best or _score(name, best.Name) < 40:
            return f"No open window matching '{name}'. " + list_windows()
        title = best.Name
        _bring_to_front(best.NativeWindowHandle)
    return f"Switched to '{title}'."


# ---------------------------------------------------------------- on-screen items (UI Automation)

CLICKABLE = {"ButtonControl", "HyperlinkControl", "MenuItemControl", "TabItemControl", "ListItemControl",
             "TreeItemControl", "CheckBoxControl", "RadioButtonControl", "EditControl", "ComboBoxControl",
             "SplitButtonControl", "DataItemControl"}


def _screen_items(include_text, limit, budget):
    root = auto.ControlFromHandle(user32.GetForegroundWindow())
    if not root:
        return None, []
    items, seen, start = [], set(), time.time()
    for ctrl, _ in auto.WalkControl(root, includeTop=False, maxDepth=40):
        if time.time() - start > budget or len(items) >= limit:
            break
        try:
            ct = ctrl.ControlTypeName
            if ct not in CLICKABLE and not (include_text and ct == "TextControl"):
                continue
            name = (ctrl.Name or "").strip()
            if not name or ctrl.IsOffscreen:
                continue
            key = (ct, name)
            if key in seen:
                continue
            seen.add(key)
            items.append((ct.replace("Control", ""), name, ctrl))
        except Exception:
            continue
    return root, items


def list_screen_items(include_text=False):
    with auto.UIAutomationInitializerInThread():
        root, items = _screen_items(bool(include_text), limit=90, budget=3.0)
        if root is None:
            return "No focused window."
        title = root.Name
    if not items:
        return f"Window '{title}': no named items found (the app may not expose them)."
    lines = [f"{t}: {n[:60]}" for t, n, _ in items]
    return f"Window '{title}' contains:\n" + "\n".join(lines)


def click_item(name, item_type=""):
    with auto.UIAutomationInitializerInThread():
        _, items = _screen_items(True, limit=600, budget=4.0)
        want = item_type.lower().strip()
        scored = [(_score(name, n) + (10 if want and t.lower() == want else 0)
                   + (5 if t != "Text" else 0), t, n, c) for t, n, c in items]
        if not scored:
            return "Couldn't read anything in the focused window."
        score, t, n, ctrl = max(scored, key=lambda x: x[0])
        if score < 45:
            return f"Nothing matching '{name}' on screen. Use list_screen_items to see what's there."
        try:
            ctrl.Click(simulateMove=False, waitTime=0.2)
        except Exception:
            try:
                ctrl.GetInvokePattern().Invoke()
            except Exception as e:
                return f"Found {t} '{n}' but couldn't click it: {e}"
    return f"Clicked {t} '{n}'."


# ---------------------------------------------------------------- clipboard / text

def _clip():
    try:
        return pyperclip.paste()
    except Exception:
        return ""


def _hotkey(*keys):
    for k in keys:
        kb.press(k)
    for k in reversed(keys):
        kb.release(k)


def insert_text(text):
    """Paste text at the cursor via the clipboard, then restore the clipboard."""
    old = _clip()
    pyperclip.copy(text)
    time.sleep(0.05)
    _hotkey(Key.ctrl, "v")
    time.sleep(0.4)
    if old:
        pyperclip.copy(old)
    return "Inserted the text at the cursor."


def get_selected_text():
    old = _clip()
    sentinel = f"jancy-{uuid.uuid4().hex}"
    pyperclip.copy(sentinel)
    _hotkey(Key.ctrl, "c")
    selected = sentinel
    for _ in range(12):
        time.sleep(0.03)
        selected = _clip()
        if selected != sentinel:
            break
    if old:
        pyperclip.copy(old)
    if selected == sentinel or not selected.strip():
        return "No text is selected."
    return f"Selected text:\n{selected[:8000]}"


def type_text(text, press_enter=False):
    kb.type(text)
    if press_enter:
        _hotkey(Key.enter)
    return "Typed the text." + (" Pressed Enter." if press_enter else "")


KEY_NAMES = {
    "ctrl": Key.ctrl, "control": Key.ctrl, "alt": Key.alt, "shift": Key.shift, "win": Key.cmd,
    "windows": Key.cmd, "enter": Key.enter, "return": Key.enter, "tab": Key.tab, "esc": Key.esc,
    "escape": Key.esc, "space": Key.space, "backspace": Key.backspace, "delete": Key.delete, "del": Key.delete,
    "home": Key.home, "end": Key.end, "pageup": Key.page_up, "pagedown": Key.page_down, "up": Key.up,
    "down": Key.down, "left": Key.left, "right": Key.right, "capslock": Key.caps_lock,
    "printscreen": Key.print_screen, "insert": Key.insert,
    **{f"f{i}": getattr(Key, f"f{i}") for i in range(1, 21)},
}


def press_keys(keys, times=1):
    parts = []
    for raw in keys.lower().replace(" ", "").split("+"):
        if raw in KEY_NAMES:
            parts.append(KEY_NAMES[raw])
        elif len(raw) == 1:
            parts.append(raw)
        else:
            return f"Unknown key: {raw}"
    for _ in range(max(1, min(int(times or 1), 30))):
        _hotkey(*parts)
        time.sleep(0.05)
    return f"Pressed {keys}."


def scroll(direction, amount=5):
    steps = max(1, min(int(amount or 5), 50))
    mouse.scroll(0, steps if direction == "up" else -steps)
    return f"Scrolled {direction}."


MEDIA_KEYS = {"play_pause": Key.media_play_pause, "next": Key.media_next, "previous": Key.media_previous,
              "volume_up": Key.media_volume_up, "volume_down": Key.media_volume_down,
              "mute": Key.media_volume_mute}


def media_control(action, times=1):
    key = MEDIA_KEYS.get(action)
    if not key:
        return f"Unknown media action {action}"
    for _ in range(max(1, min(int(times or 1), 25))):
        _hotkey(key)
    return f"Media: {action}."


def wait(seconds):
    seconds = max(0.0, min(float(seconds), 10.0))
    time.sleep(seconds)
    return f"Waited {seconds:g}s."


def stop_listening():
    if on_mute:
        on_mute()
    return "Microphone muted. The user can unmute from the Jancy widget."


# ---------------------------------------------------------------- apps / web

START_APPS = {}


def load_start_apps():
    cmd = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                             encoding="utf-8", timeout=30, creationflags=NO_WINDOW).stdout
        data = json.loads(out) if out.strip() else []
    except Exception:
        return
    if isinstance(data, dict):
        data = [data]
    START_APPS.update({a["Name"]: a["AppID"] for a in data if a.get("Name") and a.get("AppID")})


APP_ALIASES = {"chrome": "google chrome", "explorer": "file explorer", "files": "file explorer",
               "vscode": "visual studio code", "vs code": "visual studio code", "edge": "microsoft edge"}
EXE_FALLBACKS = {"cmd": "cmd", "command prompt": "cmd", "terminal": "wt", "powershell": "powershell",
                 "notepad": "notepad", "calculator": "calc", "paint": "mspaint", "file explorer": "explorer",
                 "task manager": "taskmgr", "control panel": "control"}


def _find_start_app(name):
    name = APP_ALIASES.get(name, name)
    best, best_key = None, None
    for app in START_APPS:
        lower = app.lower()
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
        if best_key is None or (score, len(lower)) < best_key:
            best, best_key = app, (score, len(lower))
    return best


def open_application(name):
    query = name.strip().lower()
    if query in ("settings", "windows settings"):
        os.startfile("ms-settings:")
        return "Opened Settings."
    if not START_APPS:
        load_start_apps()
    app = _find_start_app(query)
    if app:
        os.startfile("shell:AppsFolder\\" + START_APPS[app])
        return f"Opened {app}."
    exe = EXE_FALLBACKS.get(query, query)
    if shutil.which(exe):
        subprocess.Popen([exe], creationflags=subprocess.CREATE_NEW_CONSOLE if exe in ("cmd", "powershell") else 0)
        return f"Opened {exe}."
    return f"Could not find an application named '{name}'."


BROWSER_EXES = {"chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe"}


def _browser_path(browser):
    exe = BROWSER_EXES.get(browser)
    if not exe:
        return None
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths" + "\\" + exe
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, sub) as key:
                path = (winreg.QueryValue(key, None) or "").strip('"')
                if path and os.path.exists(path):
                    return path
        except OSError:
            pass
    return None


def open_website(url, browser="default"):
    if "://" not in url:
        url = "https://" + url
    path = _browser_path(browser) if browser and browser != "default" else None
    if path:
        subprocess.Popen([path, url])
        return f"Opened {url} in {browser}."
    webbrowser.open(url)
    return f"Opened {url}."


def search_web(query, browser="default"):
    open_website("https://www.google.com/search?q=" + urllib.parse.quote_plus(query), browser)
    return f"Searched Google for '{query}'."


# ---------------------------------------------------------------- tool schema

def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


BROWSER_PROP = {"type": "string", "enum": ["default", "chrome", "edge", "firefox"]}
TOOLS = [
    _fn("open_application", "Open an installed app by name, e.g. excel, notepad, chrome, calculator, settings.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("switch_to_window", "Bring an already-open window to the front by (part of) its title or app name.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("list_windows", "List the titles of open windows."),
    _fn("list_screen_items", "List buttons, links, menus, tabs, text fields etc. in the focused window, so you "
        "know what can be clicked. Set include_text to also read visible text (to describe or read the screen).",
        {"include_text": {"type": "boolean"}}),
    _fn("click_item", "Click a button, link, menu item, tab, list item or text field in the focused window by "
        "its visible name.", {"name": {"type": "string"},
                              "item_type": {"type": "string", "description": "Optional: Button, Hyperlink, Edit, "
                                            "MenuItem, TabItem, ListItem…"}}, ["name"]),
    _fn("open_website", "Open a URL in a browser.", {"url": {"type": "string"}, "browser": BROWSER_PROP}, ["url"]),
    _fn("search_web", "Search Google.", {"query": {"type": "string"}, "browser": BROWSER_PROP}, ["query"]),
    _fn("type_text", "Type text into the focused field (short input like search boxes, chat messages).",
        {"text": {"type": "string"}, "press_enter": {"type": "boolean"}}, ["text"]),
    _fn("insert_text", "Paste longer text (paragraphs, emails, documents) at the cursor; replaces any selection.",
        {"text": {"type": "string"}}, ["text"]),
    _fn("get_selected_text", "Read the text currently selected in the focused app (for 'rewrite this', "
        "'translate this', 'summarize this')."),
    _fn("press_keys", "Press a key or combination, optionally several times, e.g. enter, ctrl+t, alt+f4, win+d, "
        "ctrl+s, tab, down.", {"keys": {"type": "string"}, "times": {"type": "integer"}}, ["keys"]),
    _fn("scroll", "Scroll the window under the mouse.",
        {"direction": {"type": "string", "enum": ["up", "down"]}, "amount": {"type": "integer"}}, ["direction"]),
    _fn("media_control", "Control media playback and system volume (volume steps are 2% each).",
        {"action": {"type": "string", "enum": list(MEDIA_KEYS)}, "times": {"type": "integer"}}, ["action"]),
    _fn("wait", "Wait for apps or pages to load (max 10 seconds).", {"seconds": {"type": "number"}}, ["seconds"]),
    _fn("stop_listening", "Mute Jancy's microphone when the user asks you to stop listening, mute yourself, or "
        "go to sleep."),
]

TOOL_FUNCS = {
    "open_application": open_application, "switch_to_window": switch_to_window, "list_windows": list_windows,
    "list_screen_items": list_screen_items, "click_item": click_item, "open_website": open_website,
    "search_web": search_web, "type_text": type_text, "insert_text": insert_text,
    "get_selected_text": get_selected_text, "press_keys": press_keys, "scroll": scroll,
    "media_control": media_control, "wait": wait, "stop_listening": stop_listening,
}
