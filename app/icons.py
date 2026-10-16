import os
import sys
from typing import Optional

from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QRadialGradient, QFont
from PySide6.QtCore import Qt, QPointF, QRectF

_connected_icon_cache: Optional[QIcon] = None
_disconnected_icon_cache: Optional[QIcon] = None
_idle_icon_cache: Optional[QIcon] = None
_pending_icon_cache: Optional[QIcon] = None
_logo_icon_cache: Optional[QIcon] = None
_github_icon_cache: Optional[QIcon] = None
_star_icon_cache: Optional[QIcon] = None
_default_app_icon_cache: Optional[QIcon] = None

def _make_status_icon(bg_main: QColor, fg: QColor, symbol: str) -> QIcon:
    """
    Draw a modern circular icon with a soft shadow and radial gradient fill,
    with one of three symbols:
      - 'check'       (connected)
      - 'cross'       (disconnected)
      - 'idle-dot'    (idle/starting)
    Generates multiple sizes for HiDPI clarity.
    """
    def make_pix(size: int) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            # Soft drop shadow
            shadow_color = QColor(0, 0, 0, 60)
            p.setPen(Qt.NoPen)
            p.setBrush(shadow_color)
            p.drawEllipse(int(size*0.13), int(size*0.18), int(size*0.74), int(size*0.74))
            # Circular gradient body
            grad = QRadialGradient(QPointF(size*0.35, size*0.35), size*0.6)
            grad.setColorAt(0.0, QColor(bg_main).lighter(130))
            grad.setColorAt(1.0, QColor(bg_main).darker(120))
            p.setBrush(grad)
            p.drawEllipse(int(size*0.1), int(size*0.1), int(size*0.8), int(size*0.8))
            # Symbol
            pen = QPen(fg)
            pen.setWidth(max(2, size // 8))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            if symbol == "check":
                x1, y1 = size * 0.28, size * 0.55
                x2, y2 = size * 0.45, size * 0.72
                x3, y3 = size * 0.75, size * 0.35
                p.drawLine(int(x1), int(y1), int(x2), int(y2))
                p.drawLine(int(x2), int(y2), int(x3), int(y3))
            elif symbol == "cross":
                m1, m2 = int(size*0.3), int(size*0.7)
                p.drawLine(m1, m1, m2, m2)
                p.drawLine(m2, m1, m1, m2)
            else:
                p.setPen(Qt.NoPen)
                p.setBrush(fg)
                dot_r = max(2, int(size * 0.14))
                p.drawEllipse(int(size/2 - dot_r/2), int(size/2 - dot_r/2), dot_r, dot_r)
        finally:
            p.end()
        return pm

    icon = QIcon()
    for s in (16, 20, 24, 28, 32):
        icon.addPixmap(make_pix(s))
    return icon

def _make_round_text_icon(bg1: QColor, bg2: QColor, text: str) -> QIcon:
    """
    Fancy circular gradient icon with centered bold text, used for the GitHub menu item.
    """
    def make_pix(size: int) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            grad = QRadialGradient(QPointF(size*0.35, size*0.35), size*0.6)
            grad.setColorAt(0.0, bg1)
            grad.setColorAt(1.0, bg2)
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawEllipse(int(size*0.08), int(size*0.08), int(size*0.84), int(size*0.84))
            # Text
            p.setPen(QColor("#ffffff"))
            font = QFont()
            font.setBold(True)
            font.setPointSize(max(7, size // 3))
            p.setFont(font)
            rect = QRectF(0, 0, size, size)
            p.drawText(rect, Qt.AlignCenter, text)
        finally:
            p.end()
        return pm
    icon = QIcon()
    for s in (16, 20, 24, 28, 32):
        icon.addPixmap(make_pix(s))
    return icon

def _make_star_icon() -> QIcon:
    """
    Gold star icon with subtle gradient to invite starring/support.
    """
    def make_pix(size: int) -> QPixmap:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            # background circle
            grad = QRadialGradient(QPointF(size*0.5, size*0.45), size*0.6)
            grad.setColorAt(0.0, QColor("#facc15"))  # amber-400
            grad.setColorAt(1.0, QColor("#d97706"))  # amber-600
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawEllipse(int(size*0.08), int(size*0.08), int(size*0.84), int(size*0.84))
            # star
            p.setPen(QPen(QColor("#ffffff"), max(2, size // 14), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            # Simple 5-point star path
            from math import sin, cos, pi
            cx, cy = size/2, size/2
            r_outer = size*0.33
            r_inner = r_outer*0.5
            pts = []
            for i in range(10):
                angle = -pi/2 + i * pi/5
                r = r_outer if i % 2 == 0 else r_inner
                pts.append(QPointF(cx + r*cos(angle), cy + r*sin(angle)))
            for i in range(10):
                p.drawLine(pts[i], pts[(i+1) % 10])
        finally:
            p.end()
        return pm
    icon = QIcon()
    for s in (16, 20, 24, 28, 32):
        icon.addPixmap(make_pix(s))
    return icon

def connected_icon() -> QIcon:
    global _connected_icon_cache
    if _connected_icon_cache is None:
        _connected_icon_cache = _make_status_icon(QColor("#2E7D32"), QColor("#FFFFFF"), "check")  # deep green
    return _connected_icon_cache

def disconnected_icon() -> QIcon:
    global _disconnected_icon_cache
    if _disconnected_icon_cache is None:
        _disconnected_icon_cache = _make_status_icon(QColor("#C62828"), QColor("#FFFFFF"), "cross")  # vivid red
    return _disconnected_icon_cache

def idle_icon() -> QIcon:
    global _idle_icon_cache
    if _idle_icon_cache is None:
        _idle_icon_cache = _make_status_icon(QColor("#1565C0"), QColor("#FFFFFF"), "idle-dot")  # refined blue
    return _idle_icon_cache


def pending_icon() -> QIcon:
    global _pending_icon_cache
    if _pending_icon_cache is None:
        _pending_icon_cache = _make_status_icon(QColor("#D97706"), QColor("#FFFFFF"), "idle-dot")
    return _pending_icon_cache

def _load_logo_icon() -> Optional[QIcon]:
    """
    Try to load logo.png from the main directory (next to executable), current working dir, or script dir.
    """
    global _logo_icon_cache
    if _logo_icon_cache is not None:
        return _logo_icon_cache
    candidates = []
    # alongside executable
    candidates.append(os.path.join(os.path.dirname(sys.argv[0]), "logo.png"))
    # current working directory
    candidates.append(os.path.join(os.getcwd(), "logo.png"))
    # script directory
    candidates.append(os.path.join(os.path.dirname(__file__), "..", "logo.png"))
    for path in candidates:
        path = os.path.abspath(path)
        if os.path.isfile(path):
            pix = QPixmap(path)
            if not pix.isNull():
                ic = QIcon(pix)
                _logo_icon_cache = ic
                return ic
    _logo_icon_cache = None
    return None

def default_app_icon() -> QIcon:
    """
    The app's default icon: if logo.png exists, use it; otherwise use the idle aesthetic icon.
    """
    global _default_app_icon_cache
    if _default_app_icon_cache is not None:
        return _default_app_icon_cache
    logo = _load_logo_icon()
    if logo:
        _default_app_icon_cache = logo
        return _default_app_icon_cache
    _default_app_icon_cache = idle_icon()
    return _default_app_icon_cache

def github_icon() -> QIcon:
    """
    Aesthetic circular icon with 'GH' letters to represent GitHub.
    """
    global _github_icon_cache
    if _github_icon_cache is None:
        _github_icon_cache = _make_round_text_icon(QColor("#111827"), QColor("#374151"), "GH")  # neutral dark
    return _github_icon_cache

def star_icon() -> QIcon:
    global _star_icon_cache
    if _star_icon_cache is None:
        _star_icon_cache = _make_star_icon()
    return _star_icon_cache
