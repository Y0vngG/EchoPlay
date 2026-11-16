import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt, QObject, QEvent, QPointF
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtGui import QMouseEvent, QEnterEvent, QHoverEvent
from PySide6.QtWidgets import QApplication
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow


class VisibilityEvents(QObject):
    def __init__(self):
        super().__init__()
        self.events = []

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self.events.append(event.type())
        return False


class SearchHistoryTests(unittest.TestCase):
    def test_six_keywords_recency_deduplication_and_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'library.db'
            store = LibraryStore(path)
            for i in range(9):
                store.record_search(f'keyword {i}')
            store.record_search(' keyword 5 ')
            store.record_search('  ')
            store.record_search('https://www.bilibili.com/video/BV1')
            self.assertEqual(LibraryStore(path).list_search_history(),
                             ['keyword 5', 'keyword 8', 'keyword 7', 'keyword 6', 'keyword 4', 'keyword 3'])
            self.assertEqual(store.list_playback_history(), [])

    def test_click_field_and_keyword_in_both_skins(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, d)
            app.setOrganizationName('EchoPlayKeywordTests')
            app.setApplicationName('Keywords')
            store = LibraryStore(Path(d) / 'library.db')
            for i in range(6):
                store.record_search(f'keyword {i}')
            with patch('echoplay.ui.main_window.load_session', return_value={}), \
                 patch.object(MainWindow, '_start_worker') as worker:
                window = MainWindow(store)
                try:
                    window.page_stack.setCurrentWidget(window.search_tab)
                    window.show()
                    for skin in ('default', 'excel'):
                        window._set_skin(skin)
                        for _ in range(3):
                            app.processEvents()
                        QTest.mouseClick(window.search_input, Qt.MouseButton.LeftButton)
                        for _ in range(3):
                            app.processEvents()
                        popup = window.search_history_popup
                        self.assertTrue(popup.isVisible())
                        self.assertEqual(popup.model().rowCount(), 6)
                        self.assertEqual(popup.y(), window.search_input.mapTo(window,
                            window.search_input.rect().bottomLeft()).y() + 1)
                        events = VisibilityEvents()
                        popup.installEventFilter(events)
                        resets = QSignalSpy(window.search_history_model.modelReset)
                        for _ in range(3):
                            QTest.mouseClick(window.search_input, Qt.MouseButton.LeftButton)
                            app.processEvents()
                        self.assertEqual(events.events, [])
                        self.assertEqual(resets.count(), 0)
                        self.assertTrue(window.search_input.hasFocus())
                        self.assertEqual(window.search_input.cursor().shape(), Qt.CursorShape.IBeamCursor)
                        window.search_input.setText("typed")
                        window.search_input.setCursorPosition(2)
                        QTest.keyClicks(window.search_input, "X")
                        self.assertEqual(window.search_input.text(), "tyXped")
                        self.assertTrue(popup.isVisible())
                        popup.removeEventFilter(events)
                        index = popup.model().index(1, 0)
                        keyword = index.data()
                        worker.reset_mock()
                        # Windows routes input through the native QWindow first;
                        # this must not dismiss the overlay before QWidget sees it.
                        local = popup.visualRect(index).center()
                        global_pos = popup.viewport().mapToGlobal(local)
                        native_pos = window.mapFromGlobal(global_pos)
                        event = QMouseEvent(QEvent.Type.MouseButtonPress,
                                            QPointF(native_pos), QPointF(global_pos),
                                            Qt.MouseButton.LeftButton,
                                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                        app.sendEvent(window.windowHandle(), event)
                        self.assertEqual(window.search_input.text(), keyword)
                        self.assertEqual(window.search_keyword, keyword)
                        worker.assert_called_once()
                        self.assertFalse(popup.isVisible())
                        window._search_page_succeeded([], 1, window.search_generation)
                        QTest.mouseClick(window.search_input, Qt.MouseButton.LeftButton)
                        app.processEvents()
                        index = popup.model().index(1, 0)
                        keyword = index.data()
                        worker.reset_mock()
                        # Reproduce native focus transfer before the list receives
                        # its mouse event, which previously dismissed the list.
                        popup.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                        popup.setFocus(Qt.FocusReason.MouseFocusReason)
                        QTest.mousePress(popup.viewport(), Qt.MouseButton.LeftButton,
                                         pos=popup.visualRect(index).center())
                        app.processEvents()
                        QTest.mouseRelease(popup.viewport(), Qt.MouseButton.LeftButton,
                                           pos=popup.visualRect(index).center())
                        popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                        app.processEvents()
                        self.assertEqual(window.search_input.text(), keyword)
                        self.assertEqual(window.search_keyword, keyword)
                        self.assertEqual(store.list_search_history()[0], keyword)
                        self.assertFalse(popup.isVisible())
                        worker.assert_called_once()
                        window._search_page_succeeded([], 1, window.search_generation)
                        QTest.mouseClick(window.search_input, Qt.MouseButton.LeftButton)
                        app.processEvents()
                        QTest.keyClick(window.search_input, Qt.Key.Key_Escape)
                        self.assertFalse(popup.isVisible())
                        QTest.mouseClick(window.search_input, Qt.MouseButton.LeftButton)
                        app.processEvents()
                        QTest.mouseClick(window.results.viewport(), Qt.MouseButton.LeftButton)
                        self.assertFalse(popup.isVisible())
                        QTest.mouseClick(window.search_input, Qt.MouseButton.LeftButton)
                        app.processEvents()
                        worker.reset_mock()
                        QTest.keyClick(window.search_input, Qt.Key.Key_Down)
                        QTest.keyClick(window.search_input, Qt.Key.Key_Return)
                        self.assertFalse(popup.isVisible())
                        worker.assert_called_once()
                        window._search_page_succeeded([], 1, window.search_generation)
                finally:
                    window.close()

    def test_hover_highlights_only_pointed_keyword_in_all_themes(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, d)
            app.setOrganizationName('KeywordHoverTests')
            app.setApplicationName('HoverTests')
            store = LibraryStore(Path(d) / 'library.db')
            for keyword in ('First', 'Second', 'Third'):
                store.record_search(keyword)
            with patch('echoplay.ui.main_window.load_session', return_value={}), \
                 patch.object(MainWindow, '_start_worker'):
                window = MainWindow(store)
                try:
                    window.page_stack.setCurrentWidget(window.search_tab)
                    window.show()
                    for skin in ('default', 'excel'):
                        for theme in ('light', 'dark'):
                            window._set_skin(skin)
                            window._set_theme(theme)
                            for _ in range(3): app.processEvents()
                            QTest.mouseClick(window.search_input, Qt.MouseButton.LeftButton)
                            for _ in range(3): app.processEvents()
                            popup = window.search_history_popup
                            viewport = popup.viewport()
                            first = popup.visualRect(popup.model().index(0, 0))
                            second = popup.visualRect(popup.model().index(1, 0))
                            x = viewport.width() - 12
                            before = viewport.grab().toImage()
                            point = first.center()
                            app.sendEvent(viewport, QEnterEvent(QPointF(point), QPointF(point),
                                QPointF(viewport.mapToGlobal(point))))
                            QTest.mouseMove(viewport, point)
                            app.sendEvent(viewport, QHoverEvent(QEvent.Type.HoverMove,
                                QPointF(point), QPointF(viewport.mapToGlobal(point)), QPointF(-1, -1)))
                            app.processEvents()
                            after = viewport.grab().toImage()
                            self.assertNotEqual(before.pixelColor(x, first.center().y()),
                                                after.pixelColor(x, first.center().y()), (skin, theme))
                            self.assertEqual(before.pixelColor(x, second.center().y()),
                                             after.pixelColor(x, second.center().y()))
                            self.assertTrue(window.search_input.hasFocus())
                            QTest.mouseMove(window.search_input, window.search_input.rect().center())
                            app.sendEvent(viewport, QEvent(QEvent.Type.Leave))
                            app.processEvents()
                            restored = viewport.grab().toImage()
                            self.assertEqual(before.pixelColor(x, first.center().y()),
                                             restored.pixelColor(x, first.center().y()))
                            popup.hide()
                finally:
                    window.close()

    def test_hover_delete_removes_only_keyword_without_searching(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, d)
            app.setOrganizationName('KeywordDeleteTests')
            app.setApplicationName('DeleteTests')
            path = Path(d) / 'library.db'
            store = LibraryStore(path)
            with patch('echoplay.ui.main_window.load_session', return_value={}), \
                 patch.object(MainWindow, '_start_worker') as worker:
                window = MainWindow(store)
                try:
                    window.page_stack.setCurrentWidget(window.search_tab)
                    window.show()
                    for skin in ('default', 'excel'):
                        for theme in ('light', 'dark'):
                            for i in range(6): store.record_search(f'keyword {i}')
                            window._set_skin(skin)
                            window._set_theme(theme)
                            for _ in range(3): app.processEvents()
                            window.search_input.setText('keep input')
                            QTest.mouseClick(window.search_input, Qt.MouseButton.LeftButton)
                            for _ in range(3): app.processEvents()
                            popup = window.search_history_popup
                            viewport = popup.viewport()
                            self.assertTrue(popup.delete_button.isHidden())
                            selected = popup.model().index(2, 0)
                            keyword = selected.data()
                            point = popup.visualRect(selected).center()
                            app.sendEvent(viewport, QHoverEvent(QEvent.Type.HoverMove,
                                QPointF(point), QPointF(viewport.mapToGlobal(point)), QPointF(-1, -1)))
                            app.processEvents()
                            button = popup.delete_button
                            self.assertTrue(button.isVisible())
                            # Real hover events also reach the list and window,
                            # unlike direct viewport-only synthetic events.
                            global_hover = viewport.mapToGlobal(point)
                            for ancestor in (popup, window):
                                local_hover = ancestor.mapFromGlobal(global_hover)
                                app.sendEvent(ancestor, QHoverEvent(QEvent.Type.HoverMove,
                                    QPointF(local_hover), QPointF(global_hover), QPointF(-1, -1)))
                            app.processEvents()
                            self.assertTrue(button.isVisible())
                            self.assertEqual(button.toolButtonStyle(), Qt.ToolButtonStyle.ToolButtonIconOnly)
                            self.assertFalse(button.icon().isNull())
                            self.assertEqual(popup._delete_keyword, keyword)
                            self.assertGreater(button.x(), viewport.width() - 40)
                            worker.reset_mock()
                            # Include Windows' native window dispatch before the
                            # widget press/release, so deletion cannot become a search.
                            global_pos = button.mapToGlobal(button.rect().center())
                            native_pos = window.mapFromGlobal(global_pos)
                            for kind, buttons in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                                                  (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
                                app.sendEvent(window.windowHandle(), QMouseEvent(kind,
                                    QPointF(native_pos), QPointF(global_pos),
                                    Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier))
                            app.processEvents()
                            self.assertTrue(popup.isVisible())
                            self.assertEqual(popup.model().rowCount(), 5)
                            self.assertNotIn(keyword, LibraryStore(path).list_search_history())
                            self.assertEqual(window.search_input.text(), 'keep input')
                            self.assertTrue(window.search_input.hasFocus())
                            worker.assert_not_called()
                            while popup.model().rowCount():
                                point = popup.visualRect(popup.model().index(0, 0)).center()
                                app.sendEvent(viewport, QHoverEvent(QEvent.Type.HoverMove,
                                    QPointF(point), QPointF(viewport.mapToGlobal(point)), QPointF(-1, -1)))
                                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                                app.processEvents()
                            self.assertFalse(popup.isVisible())
                            self.assertEqual(LibraryStore(path).list_search_history(), [])
                            worker.assert_not_called()
                finally:
                    window.close()
