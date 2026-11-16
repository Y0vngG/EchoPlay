from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QRectF,
    QSize,
    QPropertyAnimation,
    Qt,
    QTimer,
    Property,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QPushButton,
    QToolButton,
    QStyle,
    QStyleOptionButton,
    QGridLayout,
    QLabel,
    QListWidget,
    QListView,
    QApplication,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QWidget,
    QVBoxLayout,
)


class AddToLibraryButton(QPushButton):
    """Keep rounded hover borders even at fractional display scaling."""

    def set_in_library(self, saved):
        self.setText("✔" if saved else "+")
        self.setEnabled(not saved)
        self.setAccessibleName("已加入曲库" if saved else "加入曲库")
        self.update()

    def sizeHint(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        metrics = self.fontMetrics()
        content = QSize(max(metrics.horizontalAdvance("+"), metrics.horizontalAdvance("✔")),
                        metrics.height())
        return self.style().sizeFromContents(QStyle.ContentsType.CT_PushButton, option, content, self)

    def paintEvent(self, event):
        window = self.window()
        excel = getattr(window, "skin", "default") == "excel"
        if (excel and self.isEnabled()) or not hasattr(window, "_theme_colors"):
            return super().paintEvent(event)
        colors = window._theme_colors()
        hovered = self.isEnabled() and (self.underMouse() or self.isDown())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if excel:
            # Use the same stylesheet cell background and left grid border as
            # the plus button; only the symbol is replaced by an outline check.
            option = QStyleOptionButton()
            self.initStyleOption(option)
            option.text = ""
            option.state &= ~(QStyle.StateFlag.State_MouseOver |
                              QStyle.StateFlag.State_Sunken | QStyle.StateFlag.State_HasFocus)
            self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        else:
            # Paint the whole outline in one pass on device-pixel boundaries.
            # This also avoids a native focus outline stacking over a QSS border.
            ratio = self.devicePixelRatioF()
            stroke = max(1, round(ratio))
            painter.save()
            painter.scale(1 / ratio, 1 / ratio)
            painter.setPen(QPen(QColor(colors["accent"] if hovered else colors["border"]), stroke))
            background = colors["hover"] if hovered else colors["panel"]
            if not self.isEnabled():
                background = "#343943" if getattr(window, "theme", "light") == "dark" else "#e5e7eb"
            painter.setBrush(QColor(background))
            rect = QRectF(stroke / 2, stroke / 2,
                          int(self.width() * ratio + 0.5) - stroke,
                          int(self.height() * ratio + 0.5) - stroke)
            painter.drawRoundedRect(rect, 10 * ratio, 10 * ratio)
            painter.restore()
        if self.isEnabled():
            painter.setFont(self.font())
            painter.setPen(QColor(colors["accent"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        else:
            # A light outline check matches the plus glyph's visual weight,
            # independent of the font's heavy checkmark or emoji fallback.
            painter.setPen(QPen(QColor(colors["muted"]), 1.6,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            center_x, center_y = self.width() / 2, self.height() / 2
            check = QPainterPath()
            check.moveTo(center_x - 5.5, center_y)
            check.lineTo(center_x - 1.5, center_y + 4)
            check.lineTo(center_x + 6, center_y - 4)
            painter.drawPath(check)
        painter.end()


class RecentSearchPopup(QListView):
    """Show history without a native popup's mouse grab or focus transfer."""
    keywordSelected = Signal(str)
    keywordDeleteRequested = Signal(str)

    def __init__(self, anchor, parent):
        super().__init__(parent)
        self.anchor = anchor
        self.setObjectName("recentSearchKeywords")
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pressed.connect(self._choose)
        self.delete_button = QToolButton(self.viewport())
        self.delete_button.setObjectName("recentSearchDeleteButton")
        self.delete_button.setText("\u00d7")
        self.delete_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.delete_button.setIconSize(QSize(18, 18))
        self.delete_button.setAccessibleName("\u5220\u9664\u8be5\u641c\u7d22\u5173\u952e\u8bcd")
        self.delete_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.delete_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.delete_button.clicked.connect(self._delete_hovered_keyword)
        self._delete_keyword = None
        self.delete_button.hide()
        self.hide()
        QApplication.instance().installEventFilter(self)

    def show_below_input(self):
        if self.isVisible() or self.model().rowCount() == 0:
            return
        top = self.anchor.mapTo(self.parentWidget(), QPoint(0, self.anchor.height()))
        row_height = max(28, self.sizeHintForRow(0))
        self.setGeometry(top.x(), top.y(), self.anchor.width(),
                         row_height * min(6, self.model().rowCount()) + 2 * self.frameWidth() + 8)
        self.clearSelection()
        self.setCurrentIndex(self.model().index(-1, 0))
        self.show()
        self.raise_()

    def refresh_after_deletion(self):
        self.delete_button.hide()
        self._delete_keyword = None
        if not self.model().rowCount():
            self.hide()
            return
        row_height = max(28, self.sizeHintForRow(0))
        self.resize(self.width(), row_height * min(6, self.model().rowCount())
                    + 2 * self.frameWidth() + 8)
        self.doItemsLayout()
        self.viewport().update()

    def _show_delete_button(self, position):
        index = self.indexAt(position)
        if not index.isValid():
            self.delete_button.hide()
            self._delete_keyword = None
            return
        self._delete_keyword = index.data()
        row = self.visualRect(index)
        self.delete_button.setGeometry(row.right() - 28, row.center().y() - 12, 24, 24)
        self.delete_button.show()
        self.delete_button.raise_()

    def _delete_hovered_keyword(self):
        keyword = self._delete_keyword
        if keyword is not None:
            self.keywordDeleteRequested.emit(keyword)

    def hideEvent(self, event):
        self.delete_button.hide()
        self._delete_keyword = None
        super().hideEvent(event)

    def _choose(self, index):
        keyword = index.data()
        self.hide()
        if keyword:
            self.keywordSelected.emit(keyword)

    def _dismiss_after_focus_change(self):
        if self.anchor.hasFocus() or not self.isVisible():
            return
        focus = QApplication.focusWidget()
        if focus is self or (focus is not None and self.isAncestorOf(focus)):
            return
        self.hide()

    def eventFilter(self, watched, event):
        if not self.isVisible():
            return False
        kind = event.type()
        if kind in (QEvent.Type.MouseMove, QEvent.Type.HoverMove) and isinstance(watched, QWidget):
            # Hover events propagate from the viewport to its parents. Decide
            # by pointer position, not event recipient, so those events cannot
            # immediately hide the button just shown for the hovered row.
            global_pos = watched.mapToGlobal(event.position().toPoint())
            position = self.viewport().mapFromGlobal(global_pos)
            if self.viewport().rect().contains(position):
                self._show_delete_button(position)
            else:
                self.delete_button.hide()
                self._delete_keyword = None
        if kind == QEvent.Type.MouseButtonPress and isinstance(watched, QWidget):
            # Native Windows input first passes through QWindow before Qt
            # routes it to a child widget. It is not an outside click yet.
            inside_list = watched is self or (isinstance(watched, QWidget) and self.isAncestorOf(watched))
            inside_input = watched is self.anchor
            if not inside_list and not inside_input:
                self.hide()
        elif watched is self.anchor and kind == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
            if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                row = self.currentIndex().row()
                step = 1 if event.key() == Qt.Key.Key_Down else -1
                row = (row + step) % self.model().rowCount()
                self.setCurrentIndex(self.model().index(row, 0))
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
                self._choose(self.currentIndex())
                return True
        elif kind == QEvent.Type.ApplicationDeactivate:
            self.hide()
        elif watched is self.anchor and kind == QEvent.Type.Hide:
            self.hide()
        elif watched is self.anchor and kind == QEvent.Type.FocusOut:
            # Native mouse focus changes can precede the list's press event.
            # Let that event select its keyword before considering dismissal.
            QTimer.singleShot(0, self._dismiss_after_focus_change)
        elif watched is self.parentWidget() and kind == QEvent.Type.Resize:
            self.hide()
        return False


class ExcelLibraryGridLayout(QGridLayout):
    def setGeometry(self, rect):
        super().setGeometry(rect)
        button_cell = self.itemAtPosition(1, 1)
        if button_cell is not None:
            # Include the preceding cell's one-pixel right border in the button.
            button = button_cell.widget()
            if button is not None:
                button.setGeometry(self.cellRect(1, 1).adjusted(-1, 0, 0, 0))
                button.raise_()


class ThemeToggle(QWidget):
    themeChanged = Signal(str)

    def __init__(self, theme: str, parent=None):
        super().__init__(parent)
        self._theme = theme
        self._offset = self._target_offset(theme)
        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.setFixedSize(76, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("切换亮色或暗色模式")
        self._update_tooltip()

    @Property(float)
    def offset(self) -> float:
        return self._offset

    @offset.setter
    def offset(self, value: float):
        self._offset = value
        self.update()

    def set_theme(self, theme: str):
        if theme == self._theme:
            return
        self._theme = theme
        self._animation.stop()
        self._animation.setStartValue(self._offset)
        self._animation.setEndValue(self._target_offset(theme))
        self._animation.start()
        self._update_tooltip()
        self.themeChanged.emit(theme)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_theme("dark" if self._theme == "light" else "light")
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.set_theme("dark" if self._theme == "light" else "light")
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        dark = self._theme == "dark"
        track = QColor("#293548" if dark else "#e7ebf3")
        knob = QColor("#586fe8" if dark else "#ffffff")
        active_icon = QColor("#ffffff" if dark else "#d69a18")
        inactive_icon = QColor("#8190a4" if dark else "#9aa3b2")

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 17, 17)
        self._draw_sun(painter, 18, 18, inactive_icon)
        self._draw_moon(painter, 58, 18, inactive_icon, track)
        painter.setBrush(knob)
        painter.drawEllipse(round(self._offset), 3, 30, 30)
        icon_x = round(self._offset) + 15
        if dark:
            self._draw_moon(painter, icon_x, 18, active_icon, knob)
        else:
            self._draw_sun(painter, icon_x, 18, active_icon)
        painter.end()

    @staticmethod
    def _target_offset(theme: str) -> float:
        return 43.0 if theme == "dark" else 3.0

    @staticmethod
    def _draw_sun(painter: QPainter, x: int, y: int, color: QColor):
        painter.save()
        painter.setPen(color)
        painter.setBrush(color)
        painter.drawEllipse(x - 4, y - 4, 8, 8)
        for angle in range(0, 360, 45):
            painter.save()
            painter.translate(x, y)
            painter.rotate(angle)
            painter.drawLine(0, -7, 0, -10)
            painter.restore()
        painter.restore()

    @staticmethod
    def _draw_moon(
        painter: QPainter, x: int, y: int, color: QColor, cutout: QColor
    ):
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(x - 6, y - 6, 12, 12)
        painter.setBrush(cutout)
        painter.drawEllipse(x - 1, y - 8, 11, 11)
        painter.restore()

    def _update_tooltip(self):
        mode = "暗色" if self._theme == "dark" else "亮色"
        self.setToolTip(f"当前为{mode}模式，点击切换")


class ExcelSheetStack(QStackedWidget):
    cell_height = 28
    library_column_weights = (163, 31, 104, 104, 104, 104, 104, 104, 104, 104)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.property("excelGridEnabled"):
            return

        background = QColor(self.property("excelSheetColor"))
        painter = QPainter(self)
        painter.fillRect(self.rect(), background)
        # Borders belong to scrolling rows, never to a stationary page grid.
        painter.end()


class ExcelSheetRowHeader(QWidget):
    row_height = ExcelSheetStack.cell_height

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("excelRowHeader")
        self.setFixedWidth(28)

    def paintEvent(self, event):
        super().paintEvent(event)
        background = QColor(self.property("excelSheetColor"))
        grid_color = QColor(self.property("excelGridColor"))
        text_color = QColor(self.property("excelTextColor"))
        painter = QPainter(self)
        painter.fillRect(self.rect(), background)
        painter.setPen(QPen(grid_color, 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        fixed = min(self.height(), int(self.property("searchFixedHeight") or 0))
        offset = int(self.property("searchScrollOffset") or 0)
        for top, bottom, scroll in ((0, fixed, 0), (fixed, self.height(), offset)):
            painter.save()
            painter.setClipRect(0, top, self.width(), bottom - top)
            first = top // self.row_height + scroll // self.row_height
            y = top - scroll % self.row_height
            while y < bottom:
                painter.setPen(QPen(grid_color, 1))
                painter.drawLine(0, y + self.row_height - 1, self.width(), y + self.row_height - 1)
                painter.setPen(QPen(text_color, 1))
                painter.drawText(0, y, self.width() - 1, self.row_height,
                                 Qt.AlignmentFlag.AlignCenter, str(first + 3))
                y += self.row_height
                first += 1
            painter.restore()
        painter.end()


class ExcelRibbonImage(QLabel):
    def __init__(self, light_image_path: Path, dark_image_path: Path, parent=None):
        super().__init__(parent)
        self.setObjectName("excelRibbonImage")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.setMinimumWidth(0)
        self.setScaledContents(False)
        self._theme_pixmaps = {
            "light": QPixmap(str(light_image_path)),
            "dark": QPixmap(str(dark_image_path)),
        }
        for theme, pixmap in self._theme_pixmaps.items():
            if pixmap.isNull():
                image_path = (
                    light_image_path if theme == "light" else dark_image_path
                )
                raise RuntimeError(f"无法读取 Excel 功能区图片：{image_path}")
        self._source_pixmap = self._theme_pixmaps["light"]
        self._current_theme = "light"
        self._display_width = self._source_pixmap.width()
        self._update_pixmap()

    def set_theme(self, theme: str):
        if theme not in self._theme_pixmaps:
            raise ValueError(f"Unknown Excel ribbon theme: {theme}")
        if theme == self._current_theme:
            return
        self._current_theme = theme
        self._source_pixmap = self._theme_pixmaps[theme]
        self._update_pixmap()

    def _update_pixmap(self):
        # Scale to the full-screen content width, never to the current widget
        # width. A smaller window clips the right edge without shrinking it.
        pixmap = self._source_pixmap.scaledToWidth(
            self._display_width, Qt.TransformationMode.SmoothTransformation
        )
        self.setFixedHeight(round(pixmap.deviceIndependentSize().height()))
        self.setPixmap(pixmap)

    def set_display_width(self, width: int):
        width = max(1, width)
        if width != self._display_width:
            self._display_width = width
            self._update_pixmap()


class SmoothScrollListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._scroll_animation = QPropertyAnimation(
            self.verticalScrollBar(), b"value", self
        )
        self._scroll_animation.setDuration(150)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def wheelEvent(self, event):
        delta = event.pixelDelta().y()
        if not delta:
            delta = round(event.angleDelta().y() * 0.6)
        if not delta:
            super().wheelEvent(event)
            return

        scrollbar = self.verticalScrollBar()
        start = scrollbar.value()
        target = max(
            scrollbar.minimum(),
            min(scrollbar.maximum(), start - delta),
        )
        if target == start:
            _forward_wheel_event_to_parent_scroll_area(self, event)
            return
        self._scroll_animation.stop()
        self._scroll_animation.setStartValue(start)
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()
        event.accept()


class ExcelPlaylistListWidget(SmoothScrollListWidget):
    """Keep playlist row separators tied to the list's own scroll offset."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.property("excelCells"):
            return
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(self.property("gridColor")), 1))
        height = ExcelSheetStack.cell_height
        offset = self.verticalScrollBar().value()
        for y in range(height - 1 - offset % height, self.viewport().height(), height):
            painter.drawLine(0, y, self.viewport().width(), y)
        painter.end()


class ExcelLibraryListWidget(SmoothScrollListWidget):
    """Draw worksheet cells exclusively in space not occupied by song rows."""

    sheet_layout = None

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.property("excelCells"):
            return
        viewport = self.viewport()
        region = QRegion(viewport.rect())
        first_item = self.itemAt(1, 0)
        first = self.row(first_item) if first_item is not None else 0
        for index in range(first, self.count()):
            rect = self.visualItemRect(self.item(index))
            if rect.top() > viewport.height():
                break
            region -= QRegion(rect)
        painter = QPainter(viewport)
        painter.setClipRegion(region)
        painter.fillRect(viewport.rect(), QColor(self.property("sheetColor")))
        painter.setPen(QPen(QColor(self.property("gridColor")), 1))
        if self.sheet_layout is not None:
            origin = self.geometry().left() + viewport.geometry().left()
            for column in range(3, 10):
                x = self.sheet_layout.cellRect(2, column).left() - origin
                painter.drawLine(x, 0, x, viewport.height())
        height = ExcelSheetStack.cell_height
        offset = self.verticalScrollBar().value()
        for y in range(height - 1 - offset % height, viewport.height(), height):
            painter.drawLine(0, y, viewport.width(), y)
        painter.drawLine(viewport.width() - 1, 0, viewport.width() - 1, viewport.height())
        painter.end()


class ExcelCollectionPageListWidget(SmoothScrollListWidget):
    """Continue worksheet borders in unused space below filtered parts."""
    sheet_reference = None

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.property("excelCells"):
            return
        viewport = self.viewport()
        blank = QRegion(viewport.rect())
        for index in range(self.count()):
            blank -= QRegion(self.visualItemRect(self.item(index)))
        painter = QPainter(viewport)
        painter.setClipRegion(blank)
        painter.fillRect(viewport.rect(), QColor(self.property("sheetColor")))
        painter.setPen(QPen(QColor(self.property("gridColor")), 1))
        height = ExcelSheetStack.cell_height
        offset = self.verticalScrollBar().value()
        for y in range(height - 1 - offset % height, viewport.height(), height):
            painter.drawLine(0, y, viewport.width() - 1, y)
        if self.sheet_reference is not None:
            layout = self.sheet_reference.layout()
            for column in range(3, 10):
                edge = layout.cellRect(2, column).left()
                x = viewport.mapFrom(self.sheet_reference, QPoint(edge, 0)).x()
                painter.drawLine(x, 0, x, viewport.height() - 1)
        # The details pane also owns its outer right edge, including filled rows.
        painter.setClipping(False)
        painter.drawLine(viewport.width() - 1, 0, viewport.width() - 1, viewport.height() - 1)
        painter.end()


class ExcelSearchListWidget(SmoothScrollListWidget):
    """Show a grid only while empty; populated results own their cell borders."""

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.property("excelCells"):
            return
        viewport = self.viewport()
        region = QRegion(viewport.rect())
        markers = []
        # Visit visible rows only, even after many search pages have loaded.
        item = self.itemAt(1, 0)
        first = self.row(item) if item is not None else 0
        for index in range(first, self.count()):
            item = self.item(index)
            rect = self.visualItemRect(item)
            if rect.top() > viewport.height():
                break
            if self.itemWidget(item) is not None:
                region -= QRegion(rect)
            else:
                markers.append((rect, item.text()))
        painter = QPainter(viewport)
        painter.setClipRegion(region)
        painter.fillRect(viewport.rect(), QColor(self.property("sheetColor")))
        painter.setPen(QPen(QColor(self.property("gridColor")), 1))
        if self.count() == 0:
            for column in range(1, 12):
                x = self.width() * column // 12
                painter.drawLine(x, 0, x, viewport.height())
            height = ExcelSheetStack.cell_height
            for y in range(height - 1, viewport.height(), height):
                painter.drawLine(0, y, viewport.width(), y)
        for rect, text in markers:
            painter.setPen(QPen(QColor(self.property("gridColor")), 1))
            painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())
            painter.setPen(QColor(self.property("textColor")))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.end()


class SearchResultDetails(QWidget):
    """Paint empty cells beside collection details in their own scroll coordinates."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.property("excelCells") or self.layout() is None:
            return
        width = self.layout().contentsMargins().left()
        painter = QPainter(self)
        if self.property("sheetColor"):
            painter.fillRect(self.rect(), QColor(self.property("sheetColor")))
        painter.setClipRect(0, 0, width, self.height())
        painter.setPen(QPen(QColor(self.property("gridColor")), 1))
        painter.drawLine(0, 0, 0, self.height() - 1)
        details = self.findChild(QListWidget)
        offset = details.verticalScrollBar().value() if details is not None else 0
        for y in range(ExcelSheetStack.cell_height - 1 - offset % ExcelSheetStack.cell_height,
                       self.height(), ExcelSheetStack.cell_height):
            painter.drawLine(0, y, width, y)
        columns = int(self.property("gridColumns") or 2)
        for column in range(1, columns):
            x = (width + 1) * column // columns - 1
            painter.drawLine(x, 0, x, self.height())
        painter.drawLine(0, self.height() - 1, width, self.height() - 1)
        painter.end()


class SearchResultContainer(QWidget):
    """Keep a full-width separator above the following result, including blank cells."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.bottom_border = QFrame(self)
        self.bottom_border.setObjectName("searchResultBottomBorder")
        self.bottom_border.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.bottom_border.hide()

    def set_excel(self, excel, grid_color):
        self.bottom_border.setVisible(excel)
        for panel in self.findChildren(SearchResultDetails):
            panel.setProperty("excelCells", excel)
            panel.setProperty("gridColor", grid_color)
            panel.update()
        self._position_border()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_border()

    def _position_border(self):
        self.bottom_border.setGeometry(0, self.height() - 1, self.width(), 1)
        self.bottom_border.raise_()


class SearchResultRow(QFrame):
    """Switch between a normal result card and two merged spreadsheet cells."""

    def bind_controls(self, reference, cover, title, metadata, badge, indicator, button, panel):
        self.reference = reference
        self._column_sync_timer = QTimer(self)
        self._column_sync_timer.setSingleShot(True)
        self._column_sync_timer.timeout.connect(self._sync_columns)
        self.reference.installEventFilter(self)
        self.cover = cover
        self.title = title
        self.metadata = metadata
        self.badge = badge
        self.indicator = indicator
        self.button = button
        self.button.setObjectName("searchResultAddButton")
        self.panel = panel
        self.excel = False
        self.cover_cell = QWidget(self)
        self.cover_cell.setObjectName("searchCoverCell")
        cover_layout = QVBoxLayout(self.cover_cell)
        cover_layout.setContentsMargins(0, 0, 0, 0)
        cover_layout.addWidget(cover, 0, Qt.AlignmentFlag.AlignCenter)
        self.layout().removeWidget(cover)
        self.layout().insertWidget(1, self.cover_cell)
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setToolTip(self.title.text())
        self.set_excel(False)

    def set_excel(self, excel):
        self.excel = excel
        margins = (0, 0, 0, 0) if excel else (10, 6, 8, 6)
        self.layout().setContentsMargins(*margins)
        self.layout().setSpacing(0 if excel else 6)
        self.setMinimumHeight(56 if excel else 0)
        self.setMaximumHeight(56 if excel else 16777215)
        self.metadata.setVisible(not excel)
        self.button.show()
        self.button.setSizePolicy(QSizePolicy.Policy.Minimum,
                                  QSizePolicy.Policy.Expanding if excel else QSizePolicy.Policy.Fixed)
        if not excel:
            self.button.setMinimumWidth(0)
            self.button.setMaximumWidth(16777215)
        self.indicator.setVisible(not excel)
        self.badge.setVisible(bool(self.badge.property("hasPages")))
        self.title.setWordWrap(not excel)
        self.title.setContentsMargins(4 if excel else 0, 0, 4 if excel else 0, 0)
        self.title.setStyleSheet("font-weight: 400;" if excel else "font-weight: 600;")
        self.cover.setFixedSize(92 if excel else 104, 52 if excel else 58)
        self._sync_columns()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "cover_cell"):
            self._sync_columns()

    def eventFilter(self, watched, event):
        if watched is self.reference and event.type() in (
                QEvent.Type.Resize, QEvent.Type.LayoutRequest, QEvent.Type.Show):
            # Grid column widths settle after the viewport's resize event.
            self._column_sync_timer.start(0)
        return super().eventFilter(watched, event)

    def _sync_columns(self):
        width = self.reference.width() // 6 if self.excel else 104
        self.cover_cell.setFixedWidth(width)
        if self.excel:
            # Anchor the button to columns K-L in the header grid. The list's
            # viewport can be narrower when its vertical scrollbar is visible.
            cell = self.reference.layout().cellRect(1, 10)
            left = self.mapFrom(self.reference, QPoint(cell.left() - 1, 0)).x()
            self.button.setFixedWidth(max(1, self.contentsRect().right() - left + 1))
        # Both borders must occupy the same pixel, rather than drawing the
        # details border one pixel to the right of the cover cell's border.
        left = self.x() + self.contentsRect().left() + width - 1 if self.excel else 144
        self.panel.layout().setContentsMargins(left, 0,
                                              0 if self.excel else 8, 0 if self.excel else 8)
        self.panel.update()
        source = getattr(self.cover, "source_pixmap", None)
        if source is not None:
            self.cover.setPixmap(source.scaled(self.cover.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))


class SmoothScrollArea(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_animation = QPropertyAnimation(
            self.verticalScrollBar(), b"value", self
        )
        self._scroll_animation.setDuration(150)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def wheelEvent(self, event):
        delta = event.pixelDelta().y()
        if not delta:
            delta = round(event.angleDelta().y() * 0.6)
        if not delta:
            super().wheelEvent(event)
            return

        scrollbar = self.verticalScrollBar()
        start = scrollbar.value()
        target = max(
            scrollbar.minimum(),
            min(scrollbar.maximum(), start - delta),
        )
        if target == start:
            _forward_wheel_event_to_parent_scroll_area(self, event)
            return
        self._scroll_animation.stop()
        self._scroll_animation.setStartValue(start)
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()
        event.accept()


def _forward_wheel_event_to_parent_scroll_area(widget, event):
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, SmoothScrollArea):
            parent.wheelEvent(event)
            return
        parent = parent.parentWidget()
    event.ignore()


class SettingsDetailLabel(QLabel):
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setObjectName("settingsDetail")
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._update_alignment()

    def setText(self, text: str):
        super().setText(text)
        self._update_alignment()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_alignment()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
        ):
            self._update_alignment()

    def _update_alignment(self):
        content_width = max(1, self.contentsRect().width())
        is_multiline = (
            "\n" in self.text()
            or self.fontMetrics().horizontalAdvance(self.text()) > content_width
        )
        vertical_alignment = (
            Qt.AlignmentFlag.AlignTop
            if is_multiline
            else Qt.AlignmentFlag.AlignVCenter
        )
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | vertical_alignment)


class MarqueeLabel(QLabel):
    clicked = Signal()

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._scroll_offset = 0
        self._gap = 48
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(30)
        self._scroll_timer.timeout.connect(self._advance_scroll)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def setText(self, text: str):
        super().setText(text)
        self._scroll_offset = 0
        self._update_scroll_state()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scroll_state()

    def paintEvent(self, event):
        if not self._needs_scrolling():
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setClipRect(self.contentsRect())
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        metrics = QFontMetrics(self.font())
        baseline = self.contentsRect().top() + (
            self.contentsRect().height() - metrics.height()
        ) // 2 + metrics.ascent()
        text_width = metrics.horizontalAdvance(self.text())
        x = self.contentsRect().left() - self._scroll_offset
        painter.drawText(x, baseline, self.text())
        painter.drawText(x + text_width + self._gap, baseline, self.text())

    def _needs_scrolling(self) -> bool:
        return QFontMetrics(self.font()).horizontalAdvance(self.text()) > self.contentsRect().width()

    def _update_scroll_state(self):
        if self.isVisible() and self._needs_scrolling():
            if not self._scroll_timer.isActive():
                self._scroll_timer.start()
        else:
            self._scroll_timer.stop()
            self._scroll_offset = 0
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_scroll_state()

    def hideEvent(self, event):
        self._scroll_timer.stop()
        super().hideEvent(event)

    def _advance_scroll(self):
        text_width = QFontMetrics(self.font()).horizontalAdvance(self.text())
        self._scroll_offset = (self._scroll_offset + 1) % (text_width + self._gap)
        self.update()


class EasterEggLabel(QLabel):
    def __init__(self, text: str, replacement: str, click_limit: int, parent=None):
        super().__init__(text, parent)
        self._original_text = text
        self._replacement = replacement
        self._click_limit = click_limit
        self._click_count = 0
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.setInterval(10_000)
        self._reset_timer.timeout.connect(self._reset_easter_egg)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._click_count < self._click_limit:
            self._click_count += 1
            if self._click_count == self._click_limit:
                self.setText(self._replacement)
                self._reset_timer.start()
        super().mousePressEvent(event)

    def _reset_easter_egg(self):
        self.setText(self._original_text)
        self._click_count = 0

