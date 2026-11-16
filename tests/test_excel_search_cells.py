import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame

from echoplay.models import Track
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow
from echoplay.ui.widgets import SearchResultRow


class ExcelSearchCellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          cls.directory.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("EchoPlayCellTests")
        cls.app.setApplicationName("ExcelSearchCells")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def settle(self):
        for _ in range(3):
            self.app.processEvents()

    def assert_library_column_border(self, window):
        rect = window.library_items.viewport().geometry()
        image = window.library_items.grab().toImage()
        color = window._theme_colors()["border"]
        self.assertFalse(window.library_column_border.isHidden())
        for y in range(rect.top(), rect.bottom() + 1):
            self.assertEqual(image.pixelColor(rect.left(), y).name(), color, y)

    def test_playlist_add_button_uses_sheet_column_after_resize_and_skin_switch(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("echoplay.ui.main_window.load_session", return_value={}), \
                 patch.object(MainWindow, "_start_worker"):
                window = MainWindow(LibraryStore(Path(directory) / "library.db"))
                try:
                    window.page_stack.setCurrentWidget(window.library_tab)
                    window.show()
                    for theme in ("light", "dark"):
                        window._set_theme(theme)
                        window._set_skin("default")
                        self.settle()
                        self.assertEqual(window.add_playlist_button.width(), 34)
                        self.assertTrue(window.add_playlist_button.isVisible())
                        window._set_skin("excel")
                        for width in (973, 1280, 1501):
                            window.resize(width, 850)
                            self.settle()
                            button = window.add_playlist_button
                            column = window.library_excel_layout.cellRect(0, 1)
                            position = button.mapTo(window.library_excel_view, QPoint())
                            self.assertTrue(button.isVisible())
                            self.assertEqual(position.x(), column.left() - 1)
                            self.assertEqual(button.width(), column.width() + 1)
                            self.assertEqual(button.height(), 28)
                finally:
                    window.close()

    def test_merged_cells_scroll_with_content_and_restore_default_cards(self):
        tracks = [Track(f"BV12345678{i:02d}", f"Video title {i}", "Artist", "03:00", "", "")
                  for i in range(1, 31)]
        for theme in ("light", "dark"):
            with self.subTest(theme=theme), tempfile.TemporaryDirectory() as directory:
                with patch("echoplay.ui.main_window.load_session", return_value={}), \
                     patch.object(MainWindow, "_start_worker"), \
                     patch.object(MainWindow, "_prefetch_search_result_pages"):
                    window = MainWindow(LibraryStore(Path(directory) / "library.db"))
                    try:
                        window._set_skin("excel")
                        window._set_theme(theme)
                        window.resize(1280, 850)
                        window.page_stack.setCurrentWidget(window.search_tab)
                        window._append_search_results(tracks[:15], 1)
                        window._append_search_results(tracks[15:], 2)
                        window.show()
                        self.settle()
                        item = next(iter(window.search_result_row_items.values()))
                        row = window.results.itemWidget(item).findChild(SearchResultRow)
                        self.assertEqual(item.sizeHint().height(), 56)
                        self.assertEqual(row.height(), 56)
                        self.assertEqual(row.cover_cell.width(), window.excel_search_view.width() // 6)
                        self.assertTrue(row.metadata.isHidden())
                        self.assertFalse(row.button.isHidden())
                        self.assertTrue(row.badge.isHidden())
                        self.assertTrue(window.search_status.isHidden())
                        self.assertEqual(window.results.item(0).sizeHint().height(), 28)
                        self.assertEqual(window.excel_row_header.property("searchFixedHeight"), 56)
                        self.assertEqual(window.results.item(16).sizeHint().height(), 28)
                        initial_top = window.results.visualItemRect(item).top()
                        window.results.verticalScrollBar().setValue(13)
                        self.settle()
                        self.assertEqual(window.results.visualItemRect(item).top(), initial_top - 13)
                        self.assertEqual(window.excel_row_header.property("searchScrollOffset"), 13)
                        # Row widgets carry their cell borders. Their pixels move
                        # with their content, including a partial-row scroll.
                        image = window.results.viewport().grab().toImage()
                        rect = window.results.visualItemRect(item)
                        border = window._theme_colors()["border"]
                        self.assertEqual(image.pixelColor(10, rect.bottom()).name(), border)
                        parts = [Track(tracks[0].bvid, "Collection", "Artist", "03:00", "", "",
                                       page_number=i, part_title=f"Part {i}") for i in range(1, 13)]
                        window._search_result_pages_loaded(item, parts)
                        self.settle()
                        self.assertEqual(item.sizeHint().height() % 28, 0)
                        self.assertFalse(row.badge.isHidden())
                        container = window.results.itemWidget(item)
                        details = window.search_result_page_lists[id(item)]
                        self.assertEqual(item.sizeHint().height(), 56 + details.height())
                        cover_edge = row.cover_cell.mapTo(
                            container, QPoint(row.cover_cell.width() - 1, 0)).x()
                        details_edge = details.mapTo(container, QPoint()).x()
                        self.assertEqual(cover_edge, details_edge)
                        rendered = container.grab().toImage()
                        border_color = window._theme_colors()["border"]
                        self.assertEqual(rendered.pixelColor(cover_edge, 10).name(), border_color)
                        self.assertEqual(rendered.pixelColor(details_edge, 66).name(), border_color)
                        for x in (1, cover_edge // 2, cover_edge, rendered.width() - 2):
                            self.assertEqual(rendered.pixelColor(x, rendered.height() - 1).name(), border_color)
                        panel = window.search_result_page_panels[id(item)]
                        # Hovering a result must not erase the empty worksheet
                        # cells below its cover, including partial-row scrolling.
                        for target in (row, details.viewport(), window.search_input):
                            QTest.mouseMove(target, QPoint(10, 10))
                            self.settle()
                            image = panel.grab().toImage()
                            for y in (27, 55, 83):
                                self.assertEqual(image.pixelColor(10, y).name(), border_color)
                            x = (panel.layout().contentsMargins().left() + 1) // 2 - 1
                            self.assertEqual(image.pixelColor(x, 10).name(), border_color)
                        window.results.verticalScrollBar().setValue(21)
                        self.settle()
                        self.assertEqual(panel.grab().toImage().pixelColor(10, 27).name(), border_color)
                        for offset in (0, 1, 13, 27, 41, 9):
                            window.results.verticalScrollBar().setValue(offset)
                            self.settle()
                            image = window.results.viewport().grab().toImage()
                            top = panel.mapTo(window.results.viewport(), QPoint()).y()
                            for local_y in range(panel.height() - 1):
                                y = top + local_y
                                if 0 <= y < image.height():
                                    expected = (border_color if (local_y + 1) % 28 == 0
                                                else window._theme_colors()["panel"])
                                    self.assertEqual(image.pixelColor(10, y).name(), expected,
                                                     (theme, offset, local_y))
                        window._set_skin("default")
                        self.settle()
                        self.assertFalse(row.metadata.isHidden())
                        self.assertFalse(row.button.isHidden())
                        self.assertFalse(row.badge.isHidden())
                        self.assertEqual(row.cover.size().toTuple(), (104, 58))
                        window._set_skin("excel")
                        self.settle()
                        self.assertEqual(row.height(), 56)
                        self.assertTrue(row.metadata.isHidden())
                    finally:
                        window.close()

    def test_background_grid_only_appears_when_search_results_are_empty(self):
        for theme in ("light", "dark"):
            with self.subTest(theme=theme), tempfile.TemporaryDirectory() as directory:
                with patch("echoplay.ui.main_window.load_session", return_value={}):
                    window = MainWindow(LibraryStore(Path(directory) / "library.db"))
                try:
                    window._set_skin("excel")
                    window._set_theme(theme)
                    window.page_stack.setCurrentWidget(window.search_tab)
                    window.show()
                    self.settle()
                    image = window.results.viewport().grab().toImage()
                    background = window._theme_colors()["panel"]
                    border = window._theme_colors()["border"]
                    self.assertEqual(image.pixelColor(10, 10).name(), background)
                    for y in (27, 55, 83, 111):
                        self.assertEqual(image.pixelColor(10, y).name(), border)
                    for column in (1, 2, 5, 11):
                        x = window.results.width() * column // 12
                        self.assertEqual(image.pixelColor(x, 10).name(), border)
                    track = Track("BV1234567890", "Result", "Artist", "03:00", "", "")
                    with patch.object(window, "_prefetch_search_result_pages"):
                        window._append_search_results([track], 1)
                    self.settle()
                    populated = window.results.viewport().grab().toImage()
                    # Space below a short result list stays plain, rather than
                    # retaining the empty-state grid after a partial repaint.
                    for y in (111, 139, 167):
                        self.assertEqual(populated.pixelColor(10, y).name(), background)
                        x = window.results.width() // 12
                        self.assertEqual(populated.pixelColor(x, y - 5).name(), background)
                    window._clear_search_results()
                    self.settle()
                    empty = window.results.viewport().grab().toImage()
                    self.assertEqual(empty.pixelColor(10, 27).name(), border)
                    window._append_search_results([], 1)
                    self.settle()
                    self.assertEqual(window.results.viewport().grab().toImage().pixelColor(10, 55).name(), border)
                finally:
                    window.close()

    def test_library_background_grid_only_covers_unused_space(self):
        for theme in ("light", "dark"):
            with self.subTest(theme=theme), tempfile.TemporaryDirectory() as directory:
                store = LibraryStore(Path(directory) / "library.db")
                for number in range(1, 4):
                    store.add(Track("BV1234567890", "Collection", "Artist", "03:00", "", "",
                                    page_number=number, part_title=f"Part {number}",
                                    video_title="Collection"))
                store.add(Track("BV1234567891", "Following", "Artist", "03:00", "", ""))
                with patch("echoplay.ui.main_window.load_session", return_value={}), \
                     patch.object(MainWindow, "_start_worker"):
                    window = MainWindow(store)
                    try:
                        window._set_skin("excel")
                        window._set_theme(theme)
                        window.resize(1280, 850)
                        window.page_stack.setCurrentWidget(window.library_tab)
                        window.show()
                        self.settle()
                        listing = window.library_items
                        grid = window.library_excel_layout
                        origin = listing.geometry().left() + listing.viewport().geometry().left()
                        column_x = grid.cellRect(2, 8).left() - origin
                        border = window._theme_colors()["border"]
                        background = window._theme_colors()["panel"]
                        image = listing.viewport().grab().toImage()
                        self.assertEqual(image.pixelColor(column_x, 10).name(), background)
                        self.assertEqual(image.pixelColor(column_x, 150).name(), border)
                        self.assertEqual(image.pixelColor(10, 139).name(), border)
                        item = next(iter(window.library_row_items.values()))
                        window.toggle_library_pages(item)
                        self.settle()
                        expanded = listing.viewport().grab().toImage()
                        self.assertEqual(expanded.pixelColor(column_x, 66).name(), background)
                        self.assertEqual(expanded.pixelColor(column_x, 234).name(), border)
                        self.assertEqual(expanded.pixelColor(10, 223).name(), border)
                        window.toggle_library_pages(item)
                        self.settle()
                        collapsed = listing.viewport().grab().toImage()
                        self.assertEqual(collapsed.pixelColor(column_x, 150).name(), border)
                        window.library_filter.setText("no matching song")
                        window.refresh_library()
                        self.settle()
                        self.assertEqual(listing.count(), 0)
                        empty = listing.viewport().grab().toImage()
                        self.assertEqual(empty.pixelColor(column_x, 10).name(), border)
                        self.assertEqual(empty.pixelColor(10, 27).name(), border)
                        self.assert_library_column_border(window)
                    finally:
                        window.close()

    def test_library_details_align_with_merged_cells_on_startup_and_skin_switch(self):
        for theme in ("light", "dark"):
            with self.subTest(theme=theme), tempfile.TemporaryDirectory() as directory:
                QSettings().setValue("appearance/skin", "excel")
                store = LibraryStore(Path(directory) / "library.db")
                for number in range(1, 13):
                    store.add(Track("BV1234567890", "Collection", "Artist", "03:00", "", "",
                                    page_number=number, part_title=f"Part {number}",
                                    video_title="Collection"))
                for number in range(1, 14):
                    store.add(Track(f"BV00000000{number:02d}", f"Following {number}",
                                    "Artist", "03:00", "", ""))
                for number in range(25):
                    store.create_playlist(f"Playlist {number}")
                with patch("echoplay.ui.main_window.load_session", return_value={}), \
                     patch.object(MainWindow, "_start_worker"):
                    window = MainWindow(store)
                    try:
                        window._set_theme(theme)
                        window.resize(1280, 850)
                        window.show()
                        item = next(iter(window.library_row_items.values()))
                        window.toggle_library_pages(item)
                        self.settle()
                        self.assert_library_column_border(window)
                        for _ in range(2):
                            container = window.library_items.itemWidget(item)
                            row = container.findChild(QFrame, "libraryTrackRow")
                            panel = window.library_page_panels[id(item)]
                            parts = window.library_page_lists[id(item)]
                            self.assertEqual(row.height(), 56)
                            self.assertEqual(panel.mapTo(container, QPoint()).y(), 56)
                            indent = window.library_excel_layout.cellRect(2, 2).width()
                            self.assertGreater(indent, 0)
                            self.assertEqual(parts.mapTo(container, QPoint()).x(), indent)
                            self.assertEqual(parts.mapTo(container, QPoint()).y(), 56)
                            self.assertEqual(parts.width(), container.width() - indent)
                            self.assertEqual(parts.height() % 28, 0)
                            for index in range(5):
                                self.assertEqual(parts.visualItemRect(parts.item(index)).top(), index * 28)
                            window._set_skin("default")
                            self.settle()
                            self.assertTrue(window.library_column_border.isHidden())
                            window._set_skin("excel")
                            self.settle()
                            self.assert_library_column_border(window)
                        width = container.width()
                        window.toggle_library_pages(item)
                        window.toggle_library_pages(item)
                        self.settle()
                        self.assertEqual(container.width(), width)
                        window.resize(1450, 900)
                        self.settle()
                        indent = window.library_excel_layout.cellRect(2, 2).width()
                        self.assertEqual(parts.mapTo(container, QPoint()).x(), indent)
                        self.assertEqual(parts.mapTo(container, QPoint()).y(), 56)
                        self.assertEqual(parts.width(), container.width() - indent)
                        self.assert_library_column_border(window)
                        self.assertGreater(window.library_items.verticalScrollBar().maximum(), 0)
                        self.assertGreater(parts.verticalScrollBar().maximum(), 0)
                        border = window._theme_colors()["border"]
                        background = window._theme_colors()["panel"]
                        sidebar = window.library_playlist_list
                        self.assertGreater(sidebar.verticalScrollBar().maximum(), 0)
                        for offset in (0, 1, 13, 27, 41):
                            sidebar.verticalScrollBar().setValue(offset)
                            self.settle()
                            sidebar_image = sidebar.viewport().grab().toImage()
                            for y in range(27 - offset % 28, sidebar_image.height(), 28):
                                self.assertEqual(sidebar_image.pixelColor(10, y).name(), border,
                                                 (theme, offset, y))
                        for outer in (0, 13, 27):
                            window.library_items.verticalScrollBar().setValue(outer)
                            for inner in (0, 1, 13, 27, 41):
                                parts.verticalScrollBar().setValue(inner)
                                self.settle()
                                self.assert_library_column_border(window)
                                image = window.library_items.viewport().grab().toImage()
                                top = panel.mapTo(window.library_items.viewport(), QPoint()).y()
                                self.assertEqual(window.excel_row_header.property("searchScrollOffset"), outer)
                                for local_y in range(panel.height() - 1):
                                    y = top + local_y
                                    if 0 <= y < image.height():
                                        self.assertEqual(image.pixelColor(0, y).name(), border,
                                                         (theme, outer, inner, "left edge", local_y))
                                        is_border = (local_y + inner + 1) % 28 == 0
                                        self.assertEqual(image.pixelColor(indent // 2, y).name(),
                                                         border if is_border else background,
                                                         (theme, outer, inner, local_y))
                                        if is_border:
                                            self.assertEqual(image.pixelColor(indent + 3, y).name(), border)
                    finally:
                        window.close()

    def test_excel_search_and_history_have_working_add_buttons_at_right_edge(self):
        track = Track("BV1234567890", "A long collection title " * 8,
                      "Artist", "03:00", "", "")
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.db")
            extra_tracks = [Track(f"BVextra{i}", f"Extra {i}", "Artist", "03:00", "", "")
                            for i in range(25)]
            for extra in extra_tracks:
                store.record_playback(extra)
            store.record_playback(track)
            with patch("echoplay.ui.main_window.load_session", return_value={}), \
                 patch.object(MainWindow, "_start_worker"):
                window = MainWindow(store)
                try:
                    window.page_stack.setCurrentWidget(window.search_tab)
                    window.show()
                    for history in (False, True):
                        if history:
                            window.show_playback_history()
                        else:
                            window._clear_search_results()
                            window._append_search_results([track] + extra_tracks, 1)
                        for theme in ("light", "dark"):
                            window._set_skin("excel")
                            window._set_theme(theme)
                            for width in (973, 1280, 1501):
                                window.resize(width, 850)
                                self.settle()
                                item = next(iter(window.search_result_row_items.values()))
                                container = window.search_result_containers[id(item)]
                                row = container.findChild(SearchResultRow)
                                button = row.button
                                self.assertTrue(button.isVisible())
                                cell = window.excel_search_layout.cellRect(1, 10)
                                button_left = button.mapTo(window.excel_search_view, QPoint()).x()
                                self.assertEqual(button_left, cell.left() - 1)
                                self.assertGreater(window.results.verticalScrollBar().maximum(), 0)
                                border = window._theme_colors()["border"]
                                self.assertEqual(button.grab().toImage().pixelColor(0, 10).name(), border)
                                self.assertLessEqual(abs(button.geometry().right() - row.contentsRect().right()), 1)
                                title_edge = row.title.mapTo(row, QPoint(row.title.width(), 0)).x()
                                self.assertLessEqual(title_edge, button.x())
                                with patch.object(window, "add_to_library") as add, \
                                     patch.object(window, "_request_play_track") as play:
                                    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
                                    self.settle()
                                    add.assert_called_once_with(track)
                                    play.assert_not_called()
                                self.assertFalse(window.search_result_click_timer.isActive())
                            window._set_skin("default")
                            self.settle()
                            self.assertTrue(button.isVisible())
                            self.assertEqual(button.maximumWidth(), 16777215)
                finally:
                    window.close()

    def test_filtered_collection_keeps_grid_in_unused_detail_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.db")
            for number in range(1, 21):
                store.add(Track("BVcollection", "Collection", "Artist", "03:00", "", "",
                                page_number=number, video_title="Collection",
                                part_title=("Match " if number <= 2 else "Other ") + str(number)))
            for number in range(12):
                store.add(Track(f"BVextra{number}", f"Match extra {number}", "Artist", "03:00", "", "",
                                is_standalone=True))
            with patch("echoplay.ui.main_window.load_session", return_value={}), \
                 patch.object(MainWindow, "_start_worker"):
                window = MainWindow(store)
                try:
                    window.page_stack.setCurrentWidget(window.library_tab)
                    window.show()
                    for theme in ("light", "dark"):
                        window._set_skin("excel")
                        window._set_theme(theme)
                        window.library_filter.setText("Match")
                        window.refresh_library()
                        item = next(item for item in window.library_row_items.values()
                                    if item.data(Qt.ItemDataRole.UserRole).bvid == "BVcollection")
                        parts = window.library_page_lists[id(item)]
                        for width in (973, 1280, 1501):
                            window.resize(width, 800)
                            self.settle()
                            self.assertEqual(parts.count(), 2)
                            self.assertEqual(parts.height(), 224)
                            for offset in (0, 13, 27):
                                window.library_items.verticalScrollBar().setValue(offset)
                                self.settle()
                                image = parts.viewport().grab().toImage()
                                border = window._theme_colors()["border"]
                                for y in range(83, image.height(), 28):
                                    for x in (10, image.width() // 2, image.width() - 2):
                                        self.assertEqual(image.pixelColor(x, y).name(), border,
                                                         (theme, width, offset, x, y))
                                for column in range(4, 10):
                                    edge = window.library_excel_layout.cellRect(2, column).left()
                                    x = parts.viewport().mapFrom(window.library_excel_view, QPoint(edge, 0)).x()
                                    if 0 <= x < image.width():
                                        for y in (60, 90, 120):
                                            self.assertEqual(image.pixelColor(x, y).name(), border,
                                                             (theme, width, offset, column, x, y))
                                self.assertEqual(image.pixelColor(image.width() - 1, 10).name(), border)
                        window._set_skin("default")
                        self.settle()
                        self.assertFalse(parts.property("excelCells"))
                finally:
                    window.close()
