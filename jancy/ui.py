"""Main window: Home (status + examples), History and Settings."""
import ctypes
import threading
from datetime import datetime

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from .audio import list_microphones, list_voices
from .config import (APP_NAME, APP_VERSION, CHAT_MODELS, DATA_DIR, LANGUAGES, LISTEN_MODES, STT_MODELS,
                     WIDGET_POSITIONS)
from .groq_api import GroqError

ACCENT = "#8b5cf6"


# ---------------------------------------------------------------- icon + style

def make_icon_pixmap(size=256):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, QColor("#a855f7"))
    grad.setColorAt(1, QColor("#4f46e5"))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
    p.fillPath(path, grad)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("white"))
    heights = [0.28, 0.52, 0.74, 0.52, 0.28]
    bw, gap = size * 0.085, size * 0.06
    x = (size - (len(heights) * bw + (len(heights) - 1) * gap)) / 2
    for h in heights:
        bh = size * h
        p.drawRoundedRect(QRectF(x, (size - bh) / 2, bw, bh), bw / 2, bw / 2)
        x += bw + gap
    p.end()
    return pm


def make_icon():
    icon = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(make_icon_pixmap(s))
    return icon


def _arrow_path():
    path = DATA_DIR / "ui" / "arrow.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        pm = QPixmap(24, 24)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor("#9aa0b4"))
        pen = p.pen()
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawPolyline([QPointF(7, 10), QPointF(12, 15), QPointF(17, 10)])
        p.end()
        pm.save(str(path))
    return str(path).replace("\\", "/")


def stylesheet():
    return f"""
* {{ font-family: "Segoe UI"; color: #e8eaf0; font-size: 13px; }}
QMainWindow, #page {{ background: #0f1115; }}
#sidebar {{ background: #13151b; border-right: 1px solid #20232c; }}
#logo {{ font-size: 20px; font-weight: 700; }}
QPushButton#nav {{ text-align: left; padding: 10px 14px; border: none; border-radius: 10px; color: #a3a9ba;
    font-size: 14px; background: transparent; font-weight: 500; }}
QPushButton#nav:hover {{ background: #1b1e26; color: #e8eaf0; }}
QPushButton#nav:checked {{ background: #221e38; color: #ffffff; }}
#pageTitle {{ font-size: 26px; font-weight: 700; }}
#subtitle, #muted {{ color: #8b93a7; }}
#subtitle {{ font-size: 14px; }}
#small {{ color: #8b93a7; font-size: 12px; }}
QFrame#card {{ background: #171a21; border: 1px solid #242833; border-radius: 14px; }}
QFrame#histItem {{ background: #171a21; border: 1px solid #22262f; border-radius: 12px; }}
#cardTitle {{ font-size: 15px; font-weight: 600; }}
#sectionTitle {{ font-size: 12px; font-weight: 700; color: #8b93a7; letter-spacing: 1px; }}
QLabel#keycap {{ background: #232733; border: 1px solid #353b4a; border-bottom: 3px solid #353b4a;
    border-radius: 7px; padding: 3px 10px; font-weight: 600; }}
#statValue {{ font-size: 28px; font-weight: 700; }}
QPushButton {{ background: #252935; border: 1px solid #323745; border-radius: 9px; padding: 8px 14px;
    font-weight: 600; }}
QPushButton:hover {{ background: #2d3240; }}
QPushButton:disabled {{ color: #6b7184; }}
QPushButton#primary {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #8b5cf6, stop:1 #6366f1);
    border: none; color: white; }}
QPushButton#primary:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #9d74f8, stop:1 #7a7cf4); }}
QPushButton#ghost {{ background: transparent; border: none; color: #8b93a7; padding: 4px 8px; }}
QPushButton#ghost:hover {{ color: #e8eaf0; }}
QLineEdit, QPlainTextEdit, QComboBox {{ background: #11141a; border: 1px solid #2a2f3b; border-radius: 9px;
    padding: 7px 10px; selection-background-color: #6d4fe0; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 28px; }}
QComboBox::down-arrow {{ image: url("{_arrow_path()}"); width: 16px; height: 16px; }}
QComboBox QAbstractItemView {{ background: #171a21; border: 1px solid #2a2f3b; selection-background-color: #2a2550;
    outline: 0; padding: 4px; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 2px; }}
QScrollBar::handle:vertical {{ background: #2a2f3b; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
QFrame#banner {{ background: #2a2113; border: 1px solid #5c4515; border-radius: 12px; }}
#badgeD {{ background: #2a2250; color: #c4b5fd; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}
#badgeC {{ background: #3a2a10; color: #fbbf24; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700; }}
#chipOk {{ color: #4ade80; font-size: 12px; font-weight: 600; }}
#chipWarn {{ color: #fbbf24; font-size: 12px; font-weight: 600; }}
#chipErr {{ color: #f87171; font-size: 12px; font-weight: 600; }}
#statusTitle {{ font-size: 20px; font-weight: 700; }}
QLabel#chip {{ background: #1f2330; border: 1px solid #2c3140; border-radius: 9px; padding: 8px 12px; color: #cfd3df; }}
#ok {{ color: #4ade80; }}
#err {{ color: #f87171; }}
QMenu {{ background: #171a21; border: 1px solid #2a2f3b; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 22px 7px 14px; border-radius: 6px; }}
QMenu::item:selected {{ background: #2a2550; }}
QMenu::separator {{ height: 1px; background: #2a2f3b; margin: 4px 8px; }}
QToolTip {{ background: #171a21; color: #e8eaf0; border: 1px solid #2a2f3b; padding: 4px; }}
"""


# ---------------------------------------------------------------- widgets

class Toggle(QAbstractButton):
    def __init__(self, checked=False):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self._knob = 1.0 if checked else 0.0
        self.anim = QPropertyAnimation(self, b"knob", self, duration=140, easingCurve=QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(44, 24)

    def _animate(self, checked):
        self.anim.stop()
        self.anim.setStartValue(self._knob)
        self.anim.setEndValue(1.0 if checked else 0.0)
        self.anim.start()

    def _get_knob(self):
        return self._knob

    def _set_knob(self, v):
        self._knob = v
        self.update()

    knob = Property(float, _get_knob, _set_knob)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        off, on = QColor("#343a48"), QColor(ACCENT)
        t = self._knob
        track = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                       int(off.blue() + (on.blue() - off.blue()) * t))
        p.setPen(Qt.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 44, 24), 12, 12)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(3 + t * 20, 3, 18, 18))
        p.end()


def label(text, name=None, wrap=False):
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def card(spacing=12, margins=(20, 18, 20, 18)):
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return frame, lay


def keycaps(spec_label):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for i, part in enumerate(spec_label.split(" + ")):
        if i:
            lay.addWidget(label("+", "muted"))
        lay.addWidget(label(part, "keycap"))
    lay.addStretch()
    return w


def setting_row(title, desc, control):
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 4, 0, 4)
    lay.setSpacing(16)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(label(title, "cardTitle"))
    if desc:
        text.addWidget(label(desc, "small", wrap=True))
    lay.addLayout(text, 1)
    lay.addWidget(control, 0, Qt.AlignVCenter)
    return row


def combo(items, current, width=260, editable=False):
    """items: list of (label, value)."""
    cb = QComboBox()
    cb.setEditable(editable)
    cb.setFixedWidth(width)
    cb.setCursor(Qt.PointingHandCursor)
    for text, value in items:
        cb.addItem(text, value)
    idx = cb.findData(current)
    if idx >= 0:
        cb.setCurrentIndex(idx)
    elif editable:
        cb.setEditText(current)
    return cb


def scroll_page():
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    inner = QWidget()
    inner.setObjectName("page")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(36, 32, 36, 32)
    lay.setSpacing(16)
    area.setWidget(inner)
    return area, lay


def fmt_time(ts):
    dt = datetime.fromtimestamp(ts)
    if dt.date() == datetime.now().date():
        return dt.strftime("%I:%M %p").lstrip("0")
    return dt.strftime("%b %d, %I:%M %p").replace(" 0", " ")


def history_item(entry):
    frame = QFrame()
    frame.setObjectName("histItem")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 12, 12, 12)
    lay.setSpacing(6)
    top = QHBoxLayout()
    is_dict = entry["mode"] == "dictation"
    top.addWidget(label("DICTATION" if is_dict else "COMMAND", "badgeD" if is_dict else "badgeC"))
    top.addWidget(label(fmt_time(entry["ts"]), "small"))
    top.addStretch()
    copy = QPushButton("Copy")
    copy.setObjectName("ghost")
    copy.setCursor(Qt.PointingHandCursor)

    def do_copy():
        QGuiApplication.clipboard().setText(entry["text"])
        copy.setText("Copied")
        QTimer.singleShot(1200, lambda: copy.setText("Copy"))

    copy.clicked.connect(do_copy)
    top.addWidget(copy)
    lay.addLayout(top)
    text = label(entry["text"], wrap=True)
    text.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(text)
    if entry.get("reply"):
        lay.addWidget(label("↳ " + entry["reply"], "muted", wrap=True))
    return frame


def clear_layout(lay):
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w:
            w.setParent(None)
            w.deleteLater()


# ---------------------------------------------------------------- pages

EXAMPLES = ["Open Excel", "Search YouTube for lofi music", "Switch to Chrome", "Click Sign in",
            "What's on my screen?", "Type hello everyone and press enter", "Scroll down", "Volume up",
            "Open a new tab and go to gmail.com", "Rewrite the selected text politely", "Close this window",
            "Stop listening"]


class HomePage(QWidget):
    def __init__(self, ctrl, go):
        super().__init__()
        self.ctrl = ctrl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, lay = scroll_page()
        outer.addWidget(area)

        hour = datetime.now().hour
        greet = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        lay.addWidget(label(greet, "pageTitle"))
        lay.addWidget(label("Talk to your PC. Jancy listens, understands and does it — no keyboard or mouse needed.",
                            "subtitle", wrap=True))
        lay.addSpacing(4)

        self.banner = QFrame()
        self.banner.setObjectName("banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(18, 14, 14, 14)
        bl.addWidget(label("Add your Groq API key to start using Jancy.", wrap=True), 1)
        add = QPushButton("Add API key")
        add.setObjectName("primary")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(lambda: go("settings"))
        bl.addWidget(add)
        lay.addWidget(self.banner)

        status, sl = card(margins=(22, 20, 22, 20))
        row = QHBoxLayout()
        row.setSpacing(16)
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(14, 14)
        row.addWidget(self.status_dot, 0, Qt.AlignTop | Qt.AlignHCenter)
        text = QVBoxLayout()
        text.setSpacing(4)
        self.status_title = label("", "statusTitle")
        self.status_desc = label("", "muted", wrap=True)
        text.addWidget(self.status_title)
        text.addWidget(self.status_desc)
        row.addLayout(text, 1)
        self.mute_btn = QPushButton()
        self.mute_btn.setCursor(Qt.PointingHandCursor)
        self.mute_btn.setMinimumWidth(120)
        self.mute_btn.clicked.connect(ctrl.toggle_mute)
        row.addWidget(self.mute_btn, 0, Qt.AlignVCenter)
        sl.addLayout(row)
        lay.addWidget(status)

        ex, el = card()
        el.addWidget(label("Try saying", "cardTitle"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        for i, e in enumerate(EXAMPLES):
            grid.addWidget(label(f"“{e}”", "chip"), i // 2, i % 2)
        el.addLayout(grid)
        lay.addWidget(ex)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.stat_labels = {}
        for key, title in (("today", "Tasks today"), ("total", "Tasks done")):
            c, l = card(spacing=4)
            l.addWidget(label(title, "muted"))
            v = label("0", "statValue")
            l.addWidget(v)
            self.stat_labels[key] = v
            stats.addWidget(c)
        lay.addLayout(stats)

        lay.addSpacing(6)
        lay.addWidget(label("RECENT", "sectionTitle"))
        self.recent = QVBoxLayout()
        self.recent.setSpacing(10)
        lay.addLayout(self.recent)
        lay.addStretch()
        self.refresh()

    def refresh(self):
        ctrl = self.ctrl
        has_key = bool(ctrl.config.api_key)
        self.banner.setVisible(not has_key)
        wake = ctrl.config.get("listen_mode") == "wake"
        if not has_key:
            color, title, desc = "#f59e0b", "Waiting for your API key", "Add it in Settings and Jancy starts listening."
        elif ctrl.muted:
            color, title, desc = "#ef4444", "Muted", "The microphone is off. Unmute here or click the mic on the widget."
        else:
            color, title = "#22c55e", "Listening"
            desc = ("Say “Hey Jancy”, then your request." if wake else
                    "Just speak. Jancy acts on requests and ignores other conversation around you.")
        self.status_dot.setStyleSheet(f"background:{color}; border-radius:7px;")
        self.status_title.setText(title)
        self.status_desc.setText(desc)
        self.mute_btn.setVisible(has_key)
        self.mute_btn.setText("Unmute" if ctrl.muted else "Mute")
        self.mute_btn.setObjectName("primary" if ctrl.muted else "")
        self.mute_btn.setStyleSheet("")

        s = ctrl.history.stats()
        self.stat_labels["today"].setText(f"{s['today']:,}")
        self.stat_labels["total"].setText(f"{s['total']:,}")
        clear_layout(self.recent)
        items = ctrl.history.items[-4:][::-1]
        if not items:
            self.recent.addWidget(label("Nothing yet. Try saying “Open Notepad”.", "muted"))
        for e in items:
            self.recent.addWidget(history_item(e))


class HistoryPage(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, lay = scroll_page()
        outer.addWidget(area)
        head = QHBoxLayout()
        head.addWidget(label("History", "pageTitle"))
        head.addStretch()
        clear = QPushButton("Clear all")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self._clear)
        head.addWidget(clear)
        lay.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search everything you've asked…")
        self.search.textChanged.connect(lambda: self.debounce.start())
        lay.addWidget(self.search)
        self.debounce = QTimer(self, singleShot=True, interval=200, timeout=self.refresh)
        self.list = QVBoxLayout()
        self.list.setSpacing(10)
        lay.addLayout(self.list)
        lay.addStretch()
        self.refresh()

    def _clear(self):
        self.ctrl.history.clear()
        self.ctrl.history_changed.emit()

    def refresh(self):
        clear_layout(self.list)
        q = self.search.text().strip().lower()
        items = [e for e in reversed(self.ctrl.history.items)
                 if not q or q in e["text"].lower() or q in e.get("reply", "").lower()][:150]
        if not items:
            self.list.addWidget(label("No matches." if q else "Everything you ask Jancy will appear here.", "muted"))
        for e in items:
            self.list.addWidget(history_item(e))


class SettingsPage(QWidget):
    key_result = Signal(bool, str)
    voices_loaded = Signal(list)

    def __init__(self, ctrl, on_change):
        super().__init__()
        self.ctrl, self.cfg, self.on_change = ctrl, ctrl.config, on_change
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area, lay = scroll_page()
        outer.addWidget(area)
        lay.addWidget(label("Settings", "pageTitle"))
        lay.addWidget(label("Changes are saved automatically.", "subtitle"))

        # --- API key
        c, l = card()
        l.addWidget(label("Groq API key", "cardTitle"))
        link = label('Jancy uses Groq for speech recognition and AI. Get a free key at '
                     f'<a style="color:{ACCENT}" href="https://console.groq.com/keys">console.groq.com/keys</a>. '
                     'It is stored encrypted on this PC.', "small", wrap=True)
        link.setOpenExternalLinks(True)
        l.addWidget(link)
        row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("gsk_…  (a key is saved)" if self.cfg.api_key else "gsk_…")
        row.addWidget(self.key_edit, 1)
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(lambda on: (self.key_edit.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password),
                                         show.setText("Hide" if on else "Show")))
        row.addWidget(show)
        self.save_key_btn = QPushButton("Save && test")
        self.save_key_btn.setObjectName("primary")
        self.save_key_btn.setCursor(Qt.PointingHandCursor)
        self.save_key_btn.clicked.connect(self._save_key)
        row.addWidget(self.save_key_btn)
        l.addLayout(row)
        self.key_status = label("✓ API key saved" if self.cfg.api_key else "", "ok")
        l.addWidget(self.key_status)
        self.key_result.connect(self._key_tested)
        lay.addWidget(c)

        # --- Listening
        c, l = card()
        l.addWidget(label("Listening", "cardTitle"))
        mode = combo([(v, k) for k, v in LISTEN_MODES.items()], self.cfg.get("listen_mode"), width=300)
        mode.currentIndexChanged.connect(lambda: self._set("listen_mode", mode.currentData()))
        l.addWidget(setting_row("Mode", "Always listening acts on anything that sounds like a request. Wake word "
                                "only responds after “Hey Jancy”, “Hi Jancy” or “Hello”.", mode))
        lang = combo([(v, k) for k, v in LANGUAGES.items()], self.cfg.get("language"))
        lang.currentIndexChanged.connect(lambda: self._set("language", lang.currentData()))
        l.addWidget(setting_row("Language you speak", "Pick it explicitly; Auto-detect can mis-hear short "
                                "phrases as another language.", lang))
        mics = [("System default", "")] + [(m, m) for m in list_microphones()]
        mic = combo(mics, self.cfg.get("microphone"))
        mic.currentIndexChanged.connect(lambda: self._set("microphone", mic.currentData()))
        l.addWidget(setting_row("Microphone", None, mic))
        lay.addWidget(c)

        # --- Widget
        c, l = card()
        l.addWidget(label("Floating widget", "cardTitle"))
        l.addWidget(self._toggle_row("Show the widget", "A small bar on your desktop that shows when Jancy hears "
                                     "you. Click its mic to mute or unmute; right-click for options.", "show_widget"))
        pos = combo([(v, k) for k, v in WIDGET_POSITIONS.items()], self.cfg.get("widget_position"))
        pos.currentIndexChanged.connect(lambda: self._set("widget_position", pos.currentData()))
        l.addWidget(setting_row("Position", "Which corner of the screen it sits in.", pos))
        lay.addWidget(c)

        # --- Voice
        c, l = card()
        l.addWidget(label("Voice replies", "cardTitle"))
        l.addWidget(self._toggle_row("Speak replies", "Read Jancy's answers out loud.", "speak_replies"))
        self.voice_combo = combo([(self.cfg.get("voice"), self.cfg.get("voice"))], self.cfg.get("voice"))
        self.voice_combo.currentIndexChanged.connect(lambda: self._set("voice", self.voice_combo.currentData()))
        l.addWidget(setting_row("Voice", None, self.voice_combo))
        self.voices_loaded.connect(self._fill_voices)
        threading.Thread(target=lambda: self.voices_loaded.emit(list_voices()), daemon=True).start()
        lay.addWidget(c)

        # --- General
        c, l = card()
        l.addWidget(label("General", "cardTitle"))
        l.addWidget(self._toggle_row("Launch at startup", "Start Jancy automatically when you sign in to Windows.",
                                     "start_with_windows"))
        l.addWidget(self._toggle_row("Sound effects", "Soft sounds for mute, unmute and errors.", "sounds"))
        lay.addWidget(c)

        # --- Advanced
        c, l = card()
        l.addWidget(label("AI models", "cardTitle"))
        for key, title, desc, options in (
            ("stt_model", "Speech recognition", "Turbo is fastest.", STT_MODELS),
            ("chat_model", "Assistant", "Plans and performs the actions.", CHAT_MODELS),
        ):
            cb = combo([(m, m) for m in options], self.cfg.get(key), editable=True)
            cb.currentTextChanged.connect(lambda text, k=key: self._set(k, text.strip()) if text.strip() else None)
            l.addWidget(setting_row(title, desc, cb))
        lay.addWidget(c)
        lay.addStretch()

    def _toggle_row(self, title, desc, key):
        t = Toggle(bool(self.cfg.get(key)))
        t.toggled.connect(lambda on: self._set(key, on))
        return setting_row(title, desc, t)

    def _set(self, key, value):
        self.cfg.set(key, value)
        self.on_change()

    def _fill_voices(self, voices):
        if not voices:
            return
        current = self.cfg.get("voice")
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for v in voices:
            self.voice_combo.addItem(v.replace("Microsoft ", "").replace(" Desktop", ""), v)
        idx = self.voice_combo.findData(current)
        self.voice_combo.setCurrentIndex(max(0, idx))
        self.voice_combo.blockSignals(False)
        if idx < 0:
            self._set("voice", self.voice_combo.currentData())

    def _save_key(self):
        key = self.key_edit.text().strip()
        if not key:
            self._key_status("err", "Paste your key first.")
            return
        self.save_key_btn.setEnabled(False)
        self._key_status("muted", "Testing…")

        def test():
            try:
                models = self.ctrl.groq.list_models(key=key)
                self.key_result.emit(True, f"✓ Key works. {len(models)} models available. Jancy is listening.")
            except GroqError as e:
                self.key_result.emit(False, str(e))

        threading.Thread(target=test, daemon=True).start()

    def _key_status(self, name, text):
        self.key_status.setObjectName(name)
        self.key_status.setStyleSheet("")  # re-polish with the new objectName
        self.key_status.setText(text)

    def _key_tested(self, ok, msg):
        self.save_key_btn.setEnabled(True)
        if ok:
            self.cfg.set_api_key(self.key_edit.text())
            self.key_edit.clear()
            self.key_edit.setPlaceholderText("gsk_…  (a key is saved)")
            self.on_change()
        self._key_status("ok" if ok else "err", msg)


# ---------------------------------------------------------------- window

class MainWindow(QMainWindow):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self.resize(1000, 720)
        self.setMinimumSize(840, 580)

        root = QWidget()
        root.setObjectName("page")
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(220)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(14, 22, 14, 18)
        sl.setSpacing(4)
        logo = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(make_icon_pixmap(64).scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        logo.addWidget(icon)
        logo.addWidget(label(APP_NAME, "logo"))
        logo.addStretch()
        sl.addLayout(logo)
        sl.addSpacing(22)

        self.stack = QStackedWidget()
        self.home = HomePage(ctrl, self.go)
        self.history = HistoryPage(ctrl)
        self.settings = SettingsPage(ctrl, ctrl.apply_settings)
        self.pages = {"home": self.home, "history": self.history, "settings": self.settings}
        self.nav = {}
        group = QButtonGroup(self)
        for key, text in (("home", "⌂   Home"), ("history", "☰   History"), ("settings", "⚙   Settings")):
            b = QPushButton(text)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, k=key: self.go(k))
            group.addButton(b)
            sl.addWidget(b)
            self.nav[key] = b
            self.stack.addWidget(self.pages[key])
        sl.addStretch()
        self.chip = QLabel()
        sl.addWidget(self.chip)
        sl.addWidget(label(f"v{APP_VERSION}", "small"))

        h.addWidget(side)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        ctrl.history_changed.connect(self.refresh)
        ctrl.status_changed.connect(self.refresh)
        self.refresh()
        self.go("home")

    def go(self, page):
        self.nav[page].setChecked(True)
        self.stack.setCurrentWidget(self.pages[page])

    def refresh(self):
        self.home.refresh()
        self.history.refresh()
        ctrl = self.ctrl
        if not ctrl.config.api_key:
            name, text = "chipWarn", "●  API key needed"
        elif ctrl.muted:
            name, text = "chipErr", "●  Muted"
        else:
            name, text = "chipOk", "●  Listening"
        self.chip.setObjectName(name)
        self.chip.setText(text)
        self.chip.setStyleSheet("")

    def showEvent(self, e):
        super().showEvent(e)
        try:  # dark title bar
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(self.winId()), 20, ctypes.byref(value), 4)
        except Exception:
            pass

    def closeEvent(self, e):
        e.ignore()
        self.hide()
        self.ctrl.on_window_hidden()
