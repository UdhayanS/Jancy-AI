"""Always-visible floating pill (Wispr Flow style): mic mute button, live voice bars, status and reply bubble."""
import math
from collections import deque

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

ACCENT, AMBER, GREEN, RED = QColor("#8b5cf6"), QColor("#f59e0b"), QColor("#22c55e"), QColor("#ef4444")
BG, BG_HOVER = QColor(17, 18, 24, 238), QColor(26, 28, 36, 245)
BORDER, TEXT, MUTED = QColor(255, 255, 255, 30), QColor("#f1f2f6"), QColor("#8b90a3")
PILL_H, MIC_D, BARS, MARGIN, GAP = 40, 30, 12, 18, 8
BUBBLE_MAX_W = 340


class FloatingWidget(QWidget):
    mute_clicked = Signal()
    open_requested = Signal()
    menu_requested = Signal(QPoint)

    def __init__(self, level_source):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)
        self.level_source = level_source       # callable -> 0..1
        self.position = "bottom-center"
        self.muted = False
        self.status = "listening"               # listening | processing
        self.status_text = ""
        self.bubble_text, self.bubble_kind = "", "reply"
        self.hover = False
        self.phase = 0.0
        self.smoothed = 0.0
        self.levels = deque([0.0] * BARS, maxlen=BARS)
        self.font = QFont("Segoe UI", 10, QFont.DemiBold)
        self.bubble_font = QFont("Segoe UI", 10)
        self.small = QFont("Segoe UI", 8, QFont.Bold)
        self.pill = QRectF()
        self.bubble = QRectF()
        self.timer = QTimer(self, interval=33, timeout=self._tick)
        self.timer.start()
        self.bubble_timer = QTimer(self, singleShot=True, timeout=self.clear_bubble)
        self._relayout()

    # ---------------------------------------------------------- public
    def set_position(self, position):
        self.position = position
        self._relayout()

    def set_muted(self, muted):
        self.muted = muted
        if muted:
            self.status = "listening"
        self._relayout()

    def set_processing(self, text):
        self.status, self.status_text = "processing", text
        self._relayout()

    def set_listening(self):
        self.status, self.status_text = "listening", ""
        self._relayout()

    def show_bubble(self, text, kind="reply"):
        self.bubble_text, self.bubble_kind = text, kind
        self.bubble_timer.start(min(15000, 4000 + 55 * len(text)))
        self._relayout()

    def clear_bubble(self):
        self.bubble_text = ""
        self.bubble_timer.stop()
        self._relayout()

    # ---------------------------------------------------------- geometry
    def _pill_width(self):
        if self.status == "processing" and not self.muted:
            text_w = QFontMetrics(self.font).horizontalAdvance(self.status_text)
            return min(420, 8 + MIC_D + 12 + 30 + text_w + 18)
        if self.muted and self.hover:
            return 8 + MIC_D + 10 + QFontMetrics(self.font).horizontalAdvance("Muted Â· click to unmute") + 16
        return 8 + MIC_D + 10 + BARS * 5 + 12

    def _relayout(self):
        pw = self._pill_width()
        bw = bh = 0
        if self.bubble_text:
            r = QFontMetrics(self.bubble_font).boundingRect(0, 0, BUBBLE_MAX_W - 28, 2000, Qt.TextWordWrap,
                                                            self.bubble_text)
            bw, bh = max(160, r.width() + 28), r.height() + 40
        w = max(pw, bw)
        h = PILL_H + (GAP + bh if bh else 0)
        top = self.position.startswith("top")
        align = self.position.split("-")[1]

        def x_in(width):
            return 0 if align == "left" else (w - width if align == "right" else (w - width) / 2)

        self.pill = QRectF(x_in(pw), 0 if top else h - PILL_H, pw, PILL_H)
        self.bubble = QRectF(x_in(bw), PILL_H + GAP if top else 0, bw, bh) if bh else QRectF()

        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = (screen.left() + MARGIN if align == "left" else
             screen.right() - MARGIN - w if align == "right" else screen.center().x() - w // 2)
        y = screen.top() + MARGIN if top else screen.bottom() - MARGIN - h
        self.setGeometry(int(x), int(y), int(w) + 1, int(h) + 1)
        self.update()

    def _mic_rect(self):
        return QRectF(self.pill.left() + 5, self.pill.top() + (PILL_H - MIC_D) / 2, MIC_D, MIC_D)

    # ---------------------------------------------------------- events
    def _tick(self):
        self.phase += 0.033
        level = 0.0 if self.muted else self.level_source()
        self.smoothed = self.smoothed * 0.5 + level * 0.5
        self.levels.append(self.smoothed)
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._mic_rect().adjusted(-4, -4, 4, 4).contains(e.position()):
            self.mute_clicked.emit()
        elif e.button() == Qt.RightButton:
            self.menu_requested.emit(e.globalPosition().toPoint())

    def mouseDoubleClickEvent(self, e):
        if not self._mic_rect().contains(e.position()):
            self.open_requested.emit()

    def mouseMoveEvent(self, e):
        on_mic = self._mic_rect().adjusted(-4, -4, 4, 4).contains(e.position())
        self.setCursor(Qt.PointingHandCursor if on_mic else Qt.ArrowCursor)
        self.setToolTip(("Unmute" if self.muted else "Mute") + " microphone" if on_mic else
                        "Double-click to open Jancy Â· right-click for menu")

    def enterEvent(self, _):
        self.hover = True
        self._relayout()

    def leaveEvent(self, _):
        self.hover = False
        self._relayout()

    # ---------------------------------------------------------- painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.bubble_text:
            self._paint_bubble(p)
        self._paint_pill(p)
        p.end()

    def _paint_bubble(self, p):
        r = self.bubble.adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.fillPath(path, BG)
        p.setPen(QPen(BORDER, 1))
        p.drawPath(path)
        color = RED if self.bubble_kind == "error" else ACCENT
        p.setFont(self.small)
        p.setPen(color)
        p.drawText(QRectF(r.left() + 14, r.top() + 8, r.width() - 28, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   "JANCY" if self.bubble_kind != "error" else "PROBLEM")
        p.setFont(self.bubble_font)
        p.setPen(TEXT)
        p.drawText(QRectF(r.left() + 14, r.top() + 28, r.width() - 28, r.height() - 34), Qt.TextWordWrap,
                   self.bubble_text)

    def _paint_pill(self, p):
        r = self.pill.adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, BG_HOVER if self.hover else BG)
        border = QColor(RED) if self.muted else QColor(BORDER)
        if self.muted:
            border.setAlpha(110)
        p.setPen(QPen(border, 1))
        p.drawPath(path)

        processing = self.status == "processing" and not self.muted
        mic = self._mic_rect()
        if self.muted:
            color = QColor("#3a3f4c")
        elif processing:
            color = AMBER
        else:
            color = ACCENT
        # soft glow while the user is speaking
        if not self.muted and not processing and self.smoothed > 0.35:
            glow = QColor(ACCENT)
            glow.setAlpha(int(min(1.0, self.smoothed) * 110))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(mic.adjusted(-3, -3, 3, 3))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(mic)
        self._paint_mic_glyph(p, mic.center(), self.muted)

        x = mic.right() + 10
        cy = r.center().y()
        if processing:
            for i in range(3):
                off = max(0.0, math.sin(self.phase * 9 - i * 0.7))
                c = QColor(AMBER)
                c.setAlphaF(0.45 + 0.55 * off)
                p.setBrush(c)
                p.drawEllipse(QRectF(x + i * 9, cy - 3 - 3 * off, 6, 6))
            p.setFont(self.font)
            p.setPen(TEXT)
            text = QFontMetrics(self.font).elidedText(self.status_text, Qt.ElideRight, int(r.right() - x - 46))
            p.drawText(QRectF(x + 30, r.top(), r.right() - x - 40, r.height()), Qt.AlignVCenter | Qt.AlignLeft, text)
        elif self.muted and self.hover:
            p.setFont(self.font)
            p.setPen(MUTED)
            p.drawText(QRectF(x, r.top(), r.right() - x - 10, r.height()), Qt.AlignVCenter | Qt.AlignLeft,
                       "Muted Â· click to unmute")
        else:
            p.setPen(Qt.NoPen)
            for i, lv in enumerate(self.levels):
                if self.muted:
                    c, bh = QColor("#4a5060"), 3.0
                else:
                    idle = 3 + 1.5 * (1 + math.sin(self.phase * 2.2 + i * 0.6))  # gentle "alive" shimmer
                    bh = max(idle, lv * (PILL_H - 14))
                    c = QColor(ACCENT)
                    c.setAlphaF(0.55 + 0.45 * min(1.0, lv * 2))
                p.setBrush(c)
                p.drawRoundedRect(QRectF(x + i * 5, cy - bh / 2, 3, bh), 1.5, 1.5)

    @staticmethod
    def _paint_mic_glyph(p, c, muted):
        pen = QPen(QColor("white"), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawRoundedRect(QRectF(c.x() - 3, c.y() - 8, 6, 10), 3, 3)
        p.setBrush(Qt.NoBrush)
        p.setPen(pen)
        p.drawArc(QRectF(c.x() - 5.5, c.y() - 5.5, 11, 9), 180 * 16, 180 * 16)
        p.drawLine(QPointF(c.x(), c.y() + 3.5), QPointF(c.x(), c.y() + 6.5))
        p.drawLine(QPointF(c.x() - 3, c.y() + 6.5), QPointF(c.x() + 3, c.y() + 6.5))
        if muted:
            p.setPen(QPen(QColor("#ff6b6b"), 2.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x() - 7, c.y() - 8), QPointF(c.x() + 7, c.y() + 7))
