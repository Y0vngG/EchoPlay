import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, QSize
from PySide6.QtWidgets import QApplication

from echoplay.models import Track
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow


class CollectionWidthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          cls.directory.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("EchoPlayWidthTests")
        cls.app.setApplicationName("CollectionWidths")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def settle(self):
        for _ in range(3):
            self.app.processEvents()

    def check_widths(self, page):
        for skin in ("default", "excel"):
            for theme in ("light", "dark"):
                with self.subTest(page=page, skin=skin, theme=theme):
                    with tempfile.TemporaryDirectory() as directory:
                        pages = [Track("BV1234567890", "Collection with a long title " * 2,
                                       "Artist", "03:00", "", "", page_number=i,
                                       part_title=f"Part {i}", video_title="Collection")
                                 for i in range(1, 9)]
                        store = LibraryStore(Path(directory) / "library.db")
                        for track in pages:
                            store.add(track)
                        store.add(Track("BV9876543210", "Next song", "Artist", "03:00", "", ""))
                        with patch("echoplay.ui.main_window.load_session", return_value={}), \
                             patch.object(MainWindow, "_start_worker"), \
                             patch.object(MainWindow, "_prefetch_search_result_pages"):
                            window = MainWindow(store)
                            try:
                                window._set_skin(skin)
                                window._set_theme(theme)
                                window.resize(1200, 800)
                                if page == "search":
                                    window.page_stack.setCurrentWidget(window.search_tab)
                                    window._append_search_results([pages[0]], 1)
                                    item = next(iter(window.search_result_row_items.values()))
                                    window._search_result_pages_loaded(item, pages)
                                    listing = window.results
                                    details = window.search_result_page_lists[id(item)]
                                    toggle = window.toggle_search_result_pages
                                    update_size = window._update_search_result_item_size
                                else:
                                    window.page_stack.setCurrentWidget(window.library_tab)
                                    item = next(iter(window.library_row_items.values()))
                                    window.library_pages_by_bvid[pages[0].bvid] = pages
                                    window.toggle_library_pages(item)
                                    listing = window.library_items
                                    details = window.library_page_lists[id(item)]
                                    toggle = window.toggle_library_pages
                                    update_size = window._update_library_item_size
                                window.show()
                                self.settle()
                                container = listing.itemWidget(item)
                                panel = details.parentWidget()
                                self.assertLessEqual(panel.geometry().bottom(), container.height() - 1)
                                self.assertLessEqual(details.geometry().bottom(), panel.height() - 1)
                                self.assertLessEqual(container.height(), listing.visualItemRect(item).height())
                                if skin == "default":
                                    row = container.layout().itemAt(0).widget()
                                    margins = panel.layout().contentsMargins()
                                    row_height = (row.heightForWidth(row.width())
                                                  if row.hasHeightForWidth() else row.sizeHint().height())
                                    row_height = max(row.minimumSizeHint().height(), row_height)
                                    required_height = (row_height + details.height()
                                                       + margins.top() + margins.bottom()
                                                       + container.layout().spacing())
                                    self.assertGreaterEqual(listing.visualItemRect(item).height(), required_height)
                                    # An expansion can precede Qt's size-hint
                                    # invalidation for the formerly hidden panel.
                                    with patch.object(container, "sizeHint", return_value=QSize(0, 64)), \
                                         patch.object(container.layout(), "minimumSize", return_value=QSize(0, 64)):
                                        update_size(item)
                                    self.settle()
                                    self.assertGreaterEqual(listing.visualItemRect(item).height(), required_height)
                                # Force one normal list layout as a reference;
                                # subsequent toggles must retain that full width.
                                listing.doItemsLayout()
                                self.settle()
                                expected = container.width()
                                detail_width = details.width()
                                # Async page loads and theme changes may ask for
                                # the same height repeatedly, without a list relayout.
                                for _ in range(3):
                                    update_size(item)
                                    self.assertEqual(container.width(), expected)
                                for _ in range(8):
                                    toggle(item)
                                    self.settle()
                                    toggle(item)
                                    self.settle()
                                    self.assertEqual(container.width(), expected)
                                    self.assertEqual(details.width(), detail_width)
                                    self.assertLessEqual(container.height(), listing.visualItemRect(item).height())
                                window.resize(1400, 850)
                                self.settle()
                                self.assertGreater(container.width(), expected)
                                resized_width = container.width()
                                toggle(item)
                                toggle(item)
                                self.settle()
                                self.assertEqual(container.width(), resized_width)
                            finally:
                                window.close()

    def test_search_collection_width_is_stable(self):
        self.check_widths("search")

    def test_library_collection_width_is_stable(self):
        self.check_widths("library")
