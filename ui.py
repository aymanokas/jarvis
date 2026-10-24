"""Floating always-on-top overlay: an animated orb plus a transcript card, and a tray icon.

The assistant runs in a worker thread and talks to the UI only through Bridge signals,
which Qt delivers safely on the UI thread.
"""
import ctypes
import math
import time

from PySide6.QtCore import QObject, QPoint, QPointF, QPropertyAnimation, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QRadialGradient
from PySide6.QtWidgets import (QApplication, QLabel, QMenu, QMessageBox, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

COLORS = {
    "loading": "#8a94a6",
    "idle": "#2f8bff",
    "listening": "#00e08a",
    "thinking": "#ffaa2b",
    "speaking": "#00d9ff",
    "muted": "#ff4d5e",
    "error": "#ff4d5e",
}
STATUS_TEXT = {
    "loading": "STARTING UP",
    "idle": "JARVIS",
    "listening": "LISTENING",
    "thinking": "THINKING",
    "speaking": "JARVIS",
    "muted": "WAKE WORD PAUSED",
    "error": "STARTUP FAILED - SEE jarvis.log",
}
SPIN_SPEED = {"thinking": 7.0, "listening": 2.0, "speaking": 2.5, "loading": 4.0}  # degrees per frame

ORB_SIZE = 132   # widget size, including room for the glow
CARD_WIDTH = 340
CARD_LINGER_MS = 7000

FLAGS = (Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)


class Bridge(QObject):
    state = Signal(str)
    user = Signal(str)
    reply = Signal(str)


def _keep_topmost(widget):
    """Re-assert HWND_TOPMOST without stealing focus (other topmost windows can bury us)."""
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
    ctypes.windll.user32.SetWindowPos(int(widget.winId()), -1, 0, 0, 0, 0,
                                      SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)


def _lerp_color(a, b, t):
    return QColor(int(a.red() + (b.red() - a.red()) * t),
                  int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


def _with_alpha(color, alpha):
    c = QColor(color)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


class Orb(QWidget):
    def __init__(self, overlay):
        super().__init__(None, FLAGS)
        self.overlay = overlay
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(ORB_SIZE, ORB_SIZE)
        self.setCursor(Qt.PointingHandCursor)

        self.state = "loading"
        self.color = QColor(COLORS["loading"])
        self.level = 0.0
        self.angle = 0.0
        self.start = time.time()
        self._press = None
        self._dragged = False

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(16)

    def _tick(self):
        self.color = _lerp_color(self.color, QColor(COLORS[self.state]), 0.12)
        self.level += (self.overlay.level() - self.level) * 0.35
        self.angle = (self.angle + SPIN_SPEED.get(self.state, 0.5)) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(ORB_SIZE / 2, ORB_SIZE / 2)
        t = time.time() - self.start
        breathe = 0.5 + 0.5 * math.sin(t * 1.8)
        lv = self.level

        # Outer glow, swelling with the voice level
        glow = 52 + 12 * lv + 3 * breathe
        g = QRadialGradient(c, glow)
        g.setColorAt(0.55, _with_alpha(self.color, 70 + 110 * lv))
        g.setColorAt(1.0, _with_alpha(self.color, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, glow, glow)

        # Dark glass disc with a thin rim
        p.setBrush(QColor(8, 12, 20, 235))
        p.setPen(QPen(_with_alpha(self.color, 200), 1.5))
        p.drawEllipse(c, 36, 36)

        # Three spinning arcs
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(self.color, 3, Qt.SolidLine, Qt.RoundCap))
        r = 29
        for i in range(3):
            p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), int((self.angle + i * 120) * 16), 70 * 16)

        # Two counter-rotating inner arcs
        p.setPen(QPen(_with_alpha(self.color, 150), 1.8, Qt.SolidLine, Qt.RoundCap))
        r = 21
        for i in range(2):
            p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), int((-self.angle * 1.6 + i * 180) * 16), 50 * 16)

        # Glowing core
        core = 9 + 9 * lv + 1.5 * breathe
        g = QRadialGradient(c, core)
        g.setColorAt(0.0, QColor(255, 255, 255, 240))
        g.setColorAt(0.45, _with_alpha(self.color, 230))
        g.setColorAt(1.0, _with_alpha(self.color, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(g)
        p.drawEllipse(c, core, core)

        if self.state == "muted":
            p.setPen(QPen(QColor(COLORS["muted"]), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x() - 14, c.y() + 14), QPointF(c.x() + 14, c.y() - 14))

    # --- click to talk / drag to move / right-click menu
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPosition().toPoint() - self.pos()
            self._start_global = e.globalPosition().toPoint()
            self._dragged = False

    def mouseMoveEvent(self, e):
        if self._press is None:
            return
        pos = e.globalPosition().toPoint()
        if (pos - self._start_global).manhattanLength() > 4:
            self._dragged = True
        if self._dragged:
            self.move(pos - self._press)
            self.overlay.place_card()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press is not None:
            if self._dragged:
                self.overlay.save_position()
            else:
                self.overlay.on_click()
        self._press = None

    def contextMenuEvent(self, e):
        self.overlay.menu.exec(e.globalPos())


class Card(QWidget):
    """Transcript bubble beside the orb."""

    def __init__(self):
        super().__init__(None, FLAGS)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # clicks pass through to whatever is below
        self.setFixedWidth(CARD_WIDTH)
        self.accent = QColor(COLORS["idle"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(6)
        self.status = QLabel()
        self.status.setFont(QFont("Segoe UI", 8, QFont.DemiBold))
        self.you = QLabel()
        self.you.setFont(QFont("Segoe UI", 9))
        self.you.setStyleSheet("color: rgba(200,210,225,170);")
        self.reply = QLabel()
        self.reply.setFont(QFont("Segoe UI", 11))
        self.reply.setStyleSheet("color: #f2f6fc;")
        for label in (self.you, self.reply):
            label.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(self.you)
        layout.addWidget(self.reply)

        self.setWindowOpacity(0.0)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setDuration(220)
        self.fade.finished.connect(self._fade_done)

    def set_status(self, text, color):
        self.accent = QColor(color)
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; letter-spacing: 2px;")
        self.update()

    def set_text(self, you=None, reply=None):
        if you is not None:
            self.you.setText(f"“{you}”" if you else "")
            self.you.setVisible(bool(you))
        if reply is not None:
            self.reply.setText(reply[-500:])
            self.reply.setVisible(bool(reply))
        self.adjustSize()

    def fade_in(self):
        if not self.isVisible():
            self.show()
        self.fade.stop()
        self.fade.setEndValue(1.0)
        self.fade.start()

    def fade_out(self):
        self.fade.stop()
        self.fade.setEndValue(0.0)
        self.fade.start()

    def _fade_done(self):
        if self.windowOpacity() == 0.0:
            self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(QPen(_with_alpha(self.accent, 110), 1.2))
        p.setBrush(QColor(8, 12, 20, 228))
        p.drawRoundedRect(rect, 14, 14)
        # accent bar on the left edge
        p.setPen(Qt.NoPen)
        p.setBrush(_with_alpha(self.accent, 220))
        p.drawRoundedRect(QRectF(rect.left() + 7, rect.top() + 14, 3, rect.height() - 28), 1.5, 1.5)


class Overlay:
    def __init__(self, on_click, on_pause_toggle, on_quit, level_fn,
                 autostart_enabled, on_autostart_toggle, on_clear_memory, hotkey_label):
        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        self._on_click = on_click
        self._on_pause_toggle = on_pause_toggle
        self._on_autostart_toggle = on_autostart_toggle
        self._on_clear_memory = on_clear_memory
        self._autostart_enabled = autostart_enabled
        self._level_fn = level_fn
        self.state = "loading"
        self.paused = False
        self.settings = QSettings("Jarvis", "Overlay")

        self.bridge = Bridge()
        self.bridge.state.connect(self.set_state)
        self.bridge.user.connect(self._on_user)
        self.bridge.reply.connect(self._on_reply)
        self._reply_text = ""

        self.orb = Orb(self)
        hint = f" ({hotkey_label})" if hotkey_label else ""
        self.orb.setToolTip(f"Click to talk{hint} · drag to move · right-click for options")
        self.card = Card()
        self._build_menu(on_quit)
        self._build_tray()

        self.linger = QTimer(singleShot=True)
        self.linger.setInterval(CARD_LINGER_MS)
        self.linger.timeout.connect(self.card.fade_out)

        self.topmost = QTimer()
        self.topmost.timeout.connect(self._reassert_topmost)
        self.topmost.start(2000)

        self._restore_position()
        self.orb.show()
        self.set_state("loading")

    # --- menu & tray
    def _build_menu(self, on_quit):
        self.menu = QMenu()
        self.menu.setStyleSheet(
            "QMenu{background:#0c111b;color:#e6edf7;border:1px solid #23304a;padding:4px;}"
            "QMenu::item{padding:6px 18px;border-radius:4px;}"
            "QMenu::item:selected{background:#1b2a44;}")
        self.show_action = QAction("Show overlay", checkable=True, checked=True)
        self.show_action.toggled.connect(self._set_visible)
        self.pause_action = QAction("Pause wake word", checkable=True)
        self.pause_action.toggled.connect(self._set_paused)
        autostart_action = QAction("Start with Windows", checkable=True, checked=self._autostart_enabled)
        autostart_action.toggled.connect(self._on_autostart_toggle)
        clear_action = QAction("Clear memory...")
        clear_action.triggered.connect(self._confirm_clear_memory)
        quit_action = QAction("Quit JARVIS")
        quit_action.triggered.connect(on_quit)
        for a in (self.show_action, self.pause_action, autostart_action):
            self.menu.addAction(a)
        self.menu.addSeparator()
        self.menu.addAction(clear_action)
        self.menu.addAction(quit_action)
        self._actions = (self.show_action, self.pause_action, autostart_action, clear_action, quit_action)

    def _confirm_clear_memory(self):
        answer = QMessageBox.question(
            None, "JARVIS", "Erase everything JARVIS remembers about you?\nThis can't be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self._on_clear_memory()

    def _build_tray(self):
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(COLORS["idle"]), 6))
        p.drawEllipse(6, 6, 52, 52)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["speaking"]))
        p.drawEllipse(22, 22, 20, 20)
        p.end()
        self.tray = QSystemTrayIcon(QIcon(pix))
        self.tray.setToolTip("JARVIS")
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(lambda reason: reason == QSystemTrayIcon.Trigger
                                    and self.show_action.setChecked(not self.show_action.isChecked()))
        self.tray.show()

    def _set_visible(self, visible):
        self.orb.setVisible(visible)
        if not visible:
            self.card.hide()

    def _set_paused(self, paused):
        self.paused = paused
        self._on_pause_toggle(paused)
        self.set_state(self.state)

    # --- position
    def _restore_position(self):
        pos = self.settings.value("pos")
        screen = self.app.primaryScreen().availableGeometry()
        if pos is None or not any(s.availableGeometry().contains(pos) for s in self.app.screens()):
            pos = screen.bottomRight() - self.orb.rect().bottomRight() - QPoint(24, 24)
        self.orb.move(pos)

    def save_position(self):
        self.settings.setValue("pos", self.orb.pos())

    def place_card(self):
        """Put the card beside the orb, on whichever side has room."""
        orb = self.orb.geometry()
        screen = (self.app.screenAt(orb.center()) or self.app.primaryScreen()).availableGeometry()
        gap = -12  # tuck into the orb's glow
        x = orb.left() - self.card.width() - gap
        if x < screen.left():
            x = orb.right() + gap
        y = orb.center().y() - self.card.height() // 2
        y = max(screen.top() + 8, min(y, screen.bottom() - self.card.height() - 8))
        self.card.move(x, y)

    def _reassert_topmost(self):
        if self.orb.isVisible():
            _keep_topmost(self.orb)
        if self.card.isVisible():
            _keep_topmost(self.card)

    # --- state
    def level(self):
        return self._level_fn(self.state)

    def on_click(self):
        self._on_click()

    def set_state(self, state):
        if state == "listening" and self.state == "idle":  # new conversation: clear the old one
            self._reply_text = ""
            self.card.set_text(you="", reply="")
        self.state = state
        shown = "muted" if self.paused and state == "idle" else state
        self.orb.state = shown
        self.card.set_status(STATUS_TEXT[shown], COLORS[shown])
        self.tray.setToolTip(f"JARVIS - {shown}")
        if state == "idle":
            if self.card.isVisible():
                self.linger.start()
        elif state != "muted":
            self.linger.stop()
            if state in ("loading", "error"):
                self.card.set_text(you="", reply="")
            if self.show_action.isChecked():
                self.place_card()
                self.card.fade_in()
            if state == "error":
                self.linger.start()
        if state == "idle" and self._reply_text == "" and not self.card.you.text():
            self.card.fade_out()

    def _on_user(self, text):
        self._reply_text = ""
        self.card.set_text(you=text, reply="")
        self.place_card()

    def _on_reply(self, chunk):
        self._reply_text = (self._reply_text + " " + chunk).strip()
        self.card.set_text(reply=self._reply_text)
        self.place_card()

    def run(self):
        return self.app.exec()
