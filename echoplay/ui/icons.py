from pathlib import Path
import ctypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _RgbQuad(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("bmiHeader", _BitmapInfoHeader), ("bmiColors", _RgbQuad * 1)]


class _IconInfo(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.c_int),
        ("xHotspot", ctypes.c_uint32),
        ("yHotspot", ctypes.c_uint32),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]


def _create_windows_icon(image: QImage) -> int:
    if sys.platform != "win32":
        raise OSError("自定义任务栏图标仅支持 Windows。")

    image = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise ValueError("所选图片无法读取。")
    image = image.scaled(
        64,
        64,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    canvas = QImage(64, 64, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawImage((64 - image.width()) // 2, (64 - image.height()) // 2, image)
    painter.end()

    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    bitmap_info = _BitmapInfo()
    bitmap_info.bmiHeader = _BitmapInfoHeader(
        ctypes.sizeof(_BitmapInfoHeader),
        canvas.width(),
        -canvas.height(),
        1,
        32,
        0,
        canvas.sizeInBytes(),
        0,
        0,
        0,
        0,
    )
    bits = ctypes.c_void_p()
    user32.GetDC.argtypes = [ctypes.c_void_p]
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.ReleaseDC.restype = ctypes.c_int
    gdi32.CreateDIBSection.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_BitmapInfo),
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    gdi32.CreateDIBSection.restype = ctypes.c_void_p
    gdi32.CreateBitmap.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    gdi32.CreateBitmap.restype = ctypes.c_void_p
    gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
    gdi32.DeleteObject.restype = ctypes.c_int
    user32.CreateIconIndirect.argtypes = [ctypes.POINTER(_IconInfo)]
    user32.CreateIconIndirect.restype = ctypes.c_void_p

    dc = user32.GetDC(None)
    if not dc:
        raise ctypes.WinError(ctypes.get_last_error())
    color_bitmap = None
    mask_bitmap = None
    icon_handle = None
    try:
        color_bitmap = gdi32.CreateDIBSection(
            dc, ctypes.byref(bitmap_info), 0, ctypes.byref(bits), None, 0
        )
        if not color_bitmap or not bits.value:
            raise ctypes.WinError(ctypes.get_last_error())
        pixel_data = canvas.constBits().tobytes()
        ctypes.memmove(bits, pixel_data, canvas.sizeInBytes())
        mask_stride = ((canvas.width() + 15) // 16) * 2
        mask_data = ctypes.create_string_buffer(bytes(mask_stride * canvas.height()))
        mask_bitmap = gdi32.CreateBitmap(
            canvas.width(), canvas.height(), 1, 1, mask_data
        )
        if not mask_bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        icon_info = _IconInfo(1, 0, 0, mask_bitmap, color_bitmap)
        icon_handle = user32.CreateIconIndirect(ctypes.byref(icon_info))
        if not icon_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(icon_handle)
    finally:
        if mask_bitmap:
            gdi32.DeleteObject(mask_bitmap)
        if color_bitmap:
            gdi32.DeleteObject(color_bitmap)
        user32.ReleaseDC(None, dc)


def _set_taskbar_large_icon(window_handle: int, icon_handle: int) -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    previous_icon = user32.SendMessageW(
        ctypes.c_void_p(window_handle),
        0x0080,
        1,
        icon_handle,
    )
    return int(previous_icon or 0)


def _asset_path(*parts: str) -> Path:
    asset_root = (
        Path(sys._MEIPASS)
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2]
    )
    return asset_root.joinpath(*parts)


def _make_control_icon(name: str, color: QColor) -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(
        QPen(
            color,
            2,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)

    path = QPainterPath()
    if name == "search":
        painter.drawEllipse(4, 4, 12, 12)
        painter.drawLine(14, 14, 20, 20)
    elif name == "close":
        painter.drawLine(7, 7, 17, 17)
        painter.drawLine(17, 7, 7, 17)
    elif name == "history":
        painter.drawArc(4, 4, 16, 16, 135 * 16, -315 * 16)
        path.moveTo(3, 4)
        path.lineTo(3, 9)
        path.lineTo(8, 9)
        painter.drawPath(path)
        painter.drawLine(12, 7, 12, 12)
        painter.drawLine(12, 12, 16, 14)
    elif name == "library":
        path.moveTo(4, 5)
        path.lineTo(10, 4)
        path.lineTo(10, 19)
        path.lineTo(4, 18)
        path.closeSubpath()
        path.moveTo(20, 5)
        path.lineTo(14, 4)
        path.lineTo(14, 19)
        path.lineTo(20, 18)
        path.closeSubpath()
        path.moveTo(12, 5)
        path.lineTo(12, 20)
        painter.drawPath(path)
    elif name == "settings":
        for y, knob_x in ((6, 9), (12, 15), (18, 7)):
            painter.drawLine(4, y, 20, y)
            painter.drawEllipse(knob_x - 2, y - 2, 4, 4)
    elif name in ("previous", "next"):
        if name == "previous":
            painter.drawLine(6, 5, 6, 19)
            path.moveTo(18, 5)
            path.lineTo(9, 12)
            path.lineTo(18, 19)
        else:
            painter.drawLine(18, 5, 18, 19)
            path.moveTo(6, 5)
            path.lineTo(15, 12)
            path.lineTo(6, 19)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "play":
        path.moveTo(7, 4)
        path.lineTo(19, 12)
        path.lineTo(7, 20)
        path.closeSubpath()
        painter.fillPath(path, color)
    elif name == "pause":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(6, 4, 5, 16, 1.5, 1.5)
        painter.drawRoundedRect(14, 4, 5, 16, 1.5, 1.5)
    elif name in ("repeat_all", "repeat_one"):
        path.moveTo(5, 9)
        path.lineTo(5, 7)
        path.cubicTo(5, 5, 6, 4, 8, 4)
        path.lineTo(18, 4)
        path.moveTo(19, 15)
        path.lineTo(19, 17)
        path.cubicTo(19, 19, 18, 20, 16, 20)
        path.lineTo(6, 20)
        painter.drawPath(path)
        arrow = QPainterPath()
        arrow.moveTo(18, 1)
        arrow.lineTo(22, 4)
        arrow.lineTo(18, 7)
        arrow.closeSubpath()
        arrow.moveTo(6, 17)
        arrow.lineTo(2, 20)
        arrow.lineTo(6, 23)
        arrow.closeSubpath()
        painter.fillPath(arrow, color)
        if name == "repeat_one":
            painter.drawLine(12, 9, 12, 15)
            painter.drawLine(10, 15, 14, 15)
    elif name == "shuffle":
        path.moveTo(4, 6)
        path.lineTo(7, 6)
        path.cubicTo(11, 6, 13, 18, 17, 18)
        path.lineTo(19, 18)
        path.moveTo(4, 18)
        path.lineTo(7, 18)
        path.cubicTo(11, 18, 13, 6, 17, 6)
        path.lineTo(19, 6)
        painter.drawPath(path)
        arrow = QPainterPath()
        arrow.moveTo(17, 3)
        arrow.lineTo(21, 6)
        arrow.lineTo(17, 9)
        arrow.closeSubpath()
        arrow.moveTo(17, 15)
        arrow.lineTo(21, 18)
        arrow.lineTo(17, 21)
        arrow.closeSubpath()
        painter.fillPath(arrow, color)
    elif name.startswith("volume"):
        path.moveTo(3, 10)
        path.lineTo(7, 10)
        path.lineTo(12, 6)
        path.lineTo(12, 18)
        path.lineTo(7, 14)
        path.lineTo(3, 14)
        path.closeSubpath()
        painter.drawPath(path)
        if name == "volume_mute":
            painter.drawLine(16, 9, 21, 15)
            painter.drawLine(21, 9, 16, 15)
        else:
            wave_count = {
                "volume_low": 1,
                "volume_medium": 2,
                "volume_high": 3,
            }.get(name)
            if wave_count is None:
                painter.end()
                raise ValueError(f"Unknown control icon: {name}")
            center_x, center_y = 12, 12
            for radius in (4, 7, 10)[:wave_count]:
                painter.drawArc(
                    center_x - radius,
                    center_y - radius,
                    radius * 2,
                    radius * 2,
                    -60 * 16,
                    120 * 16,
                )
    else:
        painter.end()
        raise ValueError(f"Unknown control icon: {name}")

    painter.end()
    return QIcon(pixmap)


def _make_skin_preview_icon() -> QIcon:
    width, height = 192, 104
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    frame = QPainterPath()
    frame.addRoundedRect(2, 2, width - 4, height - 4, 14, 14)
    painter.setClipPath(frame)

    palettes = (
        {
            "window": "#f5f6f8",
            "panel": "#ffffff",
            "foreground": "#20242b",
            "muted": "#69717d",
            "border": "#d8dce4",
            "accent": "#3978d4",
        },
        {
            "window": "#171a21",
            "panel": "#222733",
            "foreground": "#e7eaf0",
            "muted": "#a0a8b7",
            "border": "#3b4352",
            "accent": "#586fe8",
        },
    )
    half_width = width // 2
    for side, colors in enumerate(palettes):
        x = side * half_width
        painter.fillRect(x, 0, half_width, height, QColor(colors["window"]))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors["panel"]))
        painter.drawRoundedRect(x + 10, 10, half_width - 20, height - 20, 7, 7)

        painter.setBrush(QColor(colors["accent"]))
        painter.drawEllipse(x + 17, 18, 7, 7)
        painter.setBrush(QColor(colors["foreground"]))
        painter.drawRoundedRect(x + 29, 19, 30, 4, 2, 2)

        painter.setPen(QPen(QColor(colors["border"]), 1))
        painter.drawLine(x + 16, 31, x + half_width - 16, 31)
        painter.setPen(Qt.PenStyle.NoPen)

        for row in range(3):
            row_y = 40 + row * 14
            painter.setBrush(
                QColor(colors["accent"] if row == 0 else colors["border"])
            )
            painter.drawEllipse(x + 17, row_y, 6, 6)
            painter.setBrush(
                QColor(colors["foreground"] if row == 0 else colors["muted"])
            )
            painter.drawRoundedRect(x + 29, row_y + 1, 39, 4, 2, 2)
            painter.setBrush(QColor(colors["border"]))
            painter.drawRoundedRect(x + 29, row_y + 7, 25, 3, 1, 1)

    painter.setClipping(False)
    painter.setPen(QPen(QColor("#808894"), 1))
    painter.drawLine(half_width, 8, half_width, height - 8)
    painter.end()
    return QIcon(pixmap)


def _make_excel_skin_preview_icon() -> QIcon:
    width, height = 192, 104
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#f3f3f3"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.fillRect(0, 0, width, 18, QColor("#ffffff"))
    painter.fillRect(0, 18, width, 15, QColor("#f8f8f8"))
    painter.fillRect(0, 33, width, 15, QColor("#ffffff"))
    painter.fillRect(0, 48, width, height - 48, QColor("#ffffff"))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#217346"))
    painter.drawRoundedRect(6, 5, 9, 9, 2, 2)

    painter.setPen(QPen(QColor("#d6d6d6"), 1))
    for x in range(0, width + 1, 24):
        painter.drawLine(x, 48, x, height)
    for y in range(48, height + 1, 14):
        painter.drawLine(0, y, width, y)
    painter.drawLine(0, 18, width, 18)
    painter.drawLine(0, 33, width, 33)

    painter.setPen(QPen(QColor("#217346"), 2))
    painter.drawRect(25, 62, 47, 13)
    painter.setPen(QPen(QColor("#555555"), 1))
    painter.drawLine(20, 26, 43, 26)
    painter.drawLine(52, 26, 75, 26)
    painter.drawLine(84, 26, 107, 26)
    painter.drawLine(116, 26, 139, 26)
    painter.end()
    return QIcon(pixmap)

