"""Jancy entry point: always-listening voice assistant that controls the PC, with a floating widget and tray icon."""
import collections
import getpass
import logging
import os
import random
import sys
import threading
import time
import winreg
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import actions
from .audio import Sounds, Speaker, WakeListener
from .brain import Assistant, is_for_assistant, is_noise, parse_wake
from .config import APP_NAME, CHAT_MODELS, DATA_DIR, Config, History
from .groq_api import Groq, GroqError
from .ui import MainWindow, make_icon, stylesheet
from .widget import FloatingWidget

log = logging.getLogger("jancy")

AWAIT_SECONDS = 8         # after a bare "Hey Jancy", how long to wait for the command
MAX_PHRASES_PER_MIN = 20  # cap on transcriptions (Groq free-tier rate limits)
FILTER_MODEL = "openai/gpt-oss-20b"   # small, fast model that screens out background talk
WAKE_REPLIES = ["Yes?", "Yes, I'm listening.", "How can I help?", "I'm here."]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

ACTION_LABELS = {
    "open_application": lambda a: f"Opening {a.get('name', 'app')}…",
    "switch_to_window": lambda a: f"Switching to {a.get('name', '')}…",
    "list_windows": lambda a: "Looking at open windows…",
    "list_screen_items": lambda a: "Reading the screen…",
    "click_item": lambda a: f"Clicking “{a.get('name', '')}”…",
    "open_website": lambda a: "Opening website…",
    "search_web": lambda a: f"Searching “{a.get('query', '')}”…",
    "type_text": lambda a: "Typing…",
    "insert_text": lambda a: "Writing…",
    "get_selected_text": lambda a: "Reading selection…",
    "press_keys": lambda a: f"Pressing {a.get('keys', '')}…",
    "scroll": lambda a: f"Scrolling {a.get('direction', '')}…",
    "media_control": lambda a: "Adjusting media…",
    "wait": lambda a: "Waiting…",
    "stop_listening": lambda a: "Muting…",
}


def setup_logging():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(DATA_DIR / "jancy.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def launch_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_jancy.pyw")
    return f'"{pythonw}" "{script}" --minimized'


def set_autostart(enabled):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
    except OSError:
        pass


class Controller(QObject):
    ui_signal = Signal(str, str)        # kind, text  (processing | reply | error | listening)
    finished = Signal()
    history_changed = Signal()
    status_changed = Signal()
    phrase_heard = Signal(bytes, float)
    woke = Signal()
    mute_requested = Signal()

    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.config = Config()
        self.history = History()
        self.groq = Groq(lambda: self.config.api_key)
        self.sounds = Sounds()
        self.speaker = Speaker()
        self.assistant = Assistant(self.groq, fallback_models=CHAT_MODELS)
        self.assistant.on_wait = lambda: self.ui_signal.emit("processing", "Waiting for Groq rate limit…")
        self.listener = None
        self.window = None

        self.state = "idle"       # idle | processing
        self.awaiting = False     # heard a bare wake word, waiting for the command
        self.phrase_times = collections.deque()
        self.await_timer = QTimer(self, singleShot=True, interval=AWAIT_SECONDS * 1000, timeout=self._await_timeout)

        self.widget = FloatingWidget(lambda: self.listener.level if self.listener else 0.0)
        self.widget.mute_clicked.connect(self.toggle_mute)
        self.widget.open_requested.connect(lambda: self.show_window())
        self.widget.menu_requested.connect(lambda pos: self.menu.popup(pos))

        self.ui_signal.connect(self._on_ui)
        self.finished.connect(self._on_finished)
        self.phrase_heard.connect(self.on_phrase)
        self.woke.connect(self.on_woke)
        self.mute_requested.connect(lambda: self.set_muted(True))
        actions.on_mute = self.mute_requested.emit

        self._build_tray()
        self.apply_settings()
        self.speaker.warm_up()
        threading.Thread(target=actions.load_start_apps, daemon=True).start()
        log.info("Jancy started (mode=%s, muted=%s)", self.config.get("listen_mode"), self.muted)

    @property
    def muted(self):
        return bool(self.config.get("muted"))

    @property
    def listening(self):
        return self.listener is not None

    # ---------------------------------------------------------- setup
    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        open_action = QAction("Open Jancy", menu, triggered=lambda: self.show_window())
        f = QFont(open_action.font())
        f.setBold(True)
        open_action.setFont(f)
        menu.addAction(open_action)
        menu.addAction(QAction("Settings", menu, triggered=lambda: self.show_window("settings")))
        menu.addSeparator()
        self.mute_action = QAction("Mute microphone", menu, triggered=self.toggle_mute)
        menu.addAction(self.mute_action)
        self.widget_action = QAction("Show floating widget", menu, checkable=True)
        self.widget_action.toggled.connect(lambda on: (self.config.set("show_widget", on), self.apply_settings()))
        menu.addAction(self.widget_action)
        menu.addSeparator()
        menu.addAction(QAction("Quit Jancy", menu, triggered=self.quit))
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def apply_settings(self):
        cfg = self.config
        self.speaker.set_voice(cfg.get("voice"))
        set_autostart(bool(cfg.get("start_with_windows")))
        self._update_listener()
        self.widget.set_position(cfg.get("widget_position"))
        self.widget.set_muted(self.muted or not cfg.api_key)
        self.widget.setVisible(bool(cfg.get("show_widget")))
        self.widget_action.blockSignals(True)
        self.widget_action.setChecked(bool(cfg.get("show_widget")))
        self.widget_action.blockSignals(False)
        self.mute_action.setText("Unmute microphone" if self.muted else "Mute microphone")
        state = "Muted" if self.muted else ("Listening" if self.listening else "Needs API key")
        self.tray.setToolTip(f"{APP_NAME} — {state}")
        self.status_changed.emit()

    def _update_listener(self):
        cfg = self.config
        want = not self.muted and bool(cfg.api_key)
        mic = cfg.get("microphone")
        if self.listener and (not want or self.listener.device_name != mic):
            self.listener.stop()
            self.listener = None
            log.info("microphone off")
        if want and not self.listener:
            self.listener = WakeListener(self.phrase_heard.emit, self._paused, mic)
            self.listener.start()
            log.info("microphone on")

    def _paused(self):
        return self.state != "idle" or self.speaker.is_speaking()

    def set_muted(self, muted):
        if muted == self.muted:
            return
        self.config.set("muted", muted)
        self._stop_awaiting()
        if self.config.get("sounds"):
            self.sounds.play("stop" if muted else "start")
        log.info("muted" if muted else "unmuted")
        self.apply_settings()

    def toggle_mute(self):
        if not self.config.api_key:
            self.show_window("settings")
            return
        self.set_muted(not self.muted)

    def show_window(self, page=None):
        if not self.window:
            self.window = MainWindow(self)
        if page:
            self.window.go(page)
        self.window.refresh()
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def on_window_hidden(self):
        if not self.config.get("welcomed"):
            self.config.set("welcomed", True)
            self.tray.showMessage(APP_NAME, "Jancy keeps listening in the background. Use the floating widget "
                                  "to mute or unmute.", make_icon(), 5000)

    def quit(self):
        if self.listener:
            self.listener.stop()
        self.speaker.shutdown()
        self.tray.hide()
        self.widget.hide()
        self.qapp.quit()

    # ---------------------------------------------------------- listening flow
    def on_phrase(self, wav, seconds):
        if self.state != "idle" or not self.listener:
            return
        now = time.time()
        while self.phrase_times and now - self.phrase_times[0] > 60:
            self.phrase_times.popleft()
        if len(self.phrase_times) >= MAX_PHRASES_PER_MIN and not self.awaiting:
            log.info("skipped phrase (rate cap)")
            return
        self.phrase_times.append(now)
        awaiting = self.awaiting
        self._stop_awaiting()
        self.state = "processing"
        self.widget.set_processing("Listening…")
        threading.Thread(target=self._process, args=(wav, seconds, awaiting), daemon=True).start()

    def on_woke(self):
        self.awaiting = True
        if self.config.get("sounds"):
            self.sounds.play("start")
        reply = random.choice(WAKE_REPLIES)
        self.widget.show_bubble(reply)
        if self.config.get("speak_replies"):
            self.speaker.say(reply)
        self.await_timer.start()

    def _await_timeout(self):
        if self.awaiting:
            self.awaiting = False
            self.widget.clear_bubble()

    def _stop_awaiting(self):
        self.awaiting = False
        self.await_timer.stop()

    # ---------------------------------------------------------- worker thread
    def _process(self, wav, seconds, awaiting):
        cfg = self.config
        try:
            text = self.groq.transcribe(wav, cfg.get("stt_model"), cfg.get("language") or None,
                                        prompt="Hey Jancy.")
            log.info("heard (%.1fs%s): %r", seconds, ", awaiting" if awaiting else "", text)
            if is_noise(text):
                return
            addressed = awaiting
            woke, rest = parse_wake(text, loose=True)
            if woke:
                if not rest or is_noise(rest):
                    self.woke.emit()
                    return
                text, addressed = rest, True
            elif cfg.get("listen_mode") == "wake" and not awaiting:
                return  # wake-word mode: ignore anything not addressed to Jancy
            elif not addressed and not is_for_assistant(self.groq, FILTER_MODEL, text,
                                                        self.assistant.recent_request()):
                log.info("ignored by filter (background talk)")
                return

            self.ui_signal.emit("processing", "Thinking…")
            reply = self.assistant.run(
                cfg.get("chat_model"), text, actions.foreground_info(),
                on_action=self._on_action, addressed=addressed)
            if reply is None:
                log.info("ignored (not meant for Jancy)")
                return
            log.info("command %r -> %r", text, reply)
            self.history.add(text, reply)
            self.history_changed.emit()
            self.ui_signal.emit("reply", reply)
            if cfg.get("speak_replies"):
                self.speaker.say(reply)
        except GroqError as e:
            log.warning("groq: %s", e)
            self.ui_signal.emit("error", str(e))
        except Exception as e:
            log.exception("processing failed")
            self.ui_signal.emit("error", f"Something went wrong: {e}")
        finally:
            self.finished.emit()

    def _on_action(self, name, args):
        log.info("tool %s %s", name, args)
        self.ui_signal.emit("processing", ACTION_LABELS.get(name, lambda a: "Working…")(args))

    def _on_ui(self, kind, text):
        if kind == "processing":
            self.widget.set_processing(text)
        elif kind == "reply":
            self.widget.show_bubble(text)
        elif kind == "error":
            if self.config.get("sounds"):
                self.sounds.play("error")
            self.widget.show_bubble(text, "error")

    def _on_finished(self):
        self.state = "idle"
        self.widget.set_listening()


def selftest():
    """`Jancy.exe --selftest`: checks the packaged build can reach the mic, screen reader and Groq."""
    from .audio import list_microphones
    cfg = Config()
    try:
        log.info("selftest mics: %s", list_microphones())
        log.info("selftest windows: %s", actions.list_windows()[:300])
        log.info("selftest screen: %s", actions.list_screen_items()[:300])
        log.info("selftest groq models: %d", len(Groq(lambda: cfg.api_key).list_models()))
        log.info("selftest OK")
    except Exception:
        log.exception("selftest FAILED")


def main():
    setup_logging()
    if "--selftest" in sys.argv:
        selftest()
        return
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_icon())
    app.setStyleSheet(stylesheet())

    # Single instance: if Jancy is already running, ask it to show its window and exit.
    server_name = f"{APP_NAME}-{getpass.getuser()}"
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        return
    QLocalServer.removeServer(server_name)
    server = QLocalServer()
    server.listen(server_name)

    ctrl = Controller(app)
    server.newConnection.connect(lambda: (server.nextPendingConnection(), ctrl.show_window()))

    if "--minimized" not in sys.argv or not ctrl.config.api_key:
        ctrl.show_window(None if ctrl.config.api_key else "settings")
    sys.exit(app.exec())
