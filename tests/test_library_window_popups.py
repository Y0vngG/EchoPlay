import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, QSettings
from PySide6.QtWidgets import QApplication, QWidget

from echoplay.models import Track
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow


class _TopLevelShowTracker(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.windows = []

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
        ):
            self.windows.append(
                (watched.metaObject().className(), watched.objectName())
            )
        return False


class LibraryWindowPopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings_directory = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            cls.settings_directory.name,
        )
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("EchoPlayTests")
        cls.app.setApplicationName("PopupRegressionTests")

    @classmethod
    def tearDownClass(cls):
        cls.settings_directory.cleanup()

    def test_library_refresh_does_not_show_temporary_top_level_widgets(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.sqlite3")
            collection_tracks = [
                Track(
                    "BV1234567890",
                    f"Collection - P{page_number}",
                    "Artist",
                    "03:00",
                    "",
                    "https://www.bilibili.com/video/BV1234567890",
                    page_number=page_number,
                    part_title=part_title,
                    video_title="Collection",
                )
                for page_number, part_title in ((1, "Part 1"), (2, "Part 2"))
            ]
            for track in collection_tracks:
                store.add(track)
            store.add(
                Track(
                    "BV1234567891",
                    "Single",
                    "Artist",
                    "03:00",
                    "",
                    "https://www.bilibili.com/video/BV1234567891",
                )
            )
            playlist_id = store.create_playlist("Test playlist")
            store.add_track_to_playlist(playlist_id, collection_tracks[0])

            tracker = _TopLevelShowTracker(self.app)
            self.app.installEventFilter(tracker)
            try:
                with patch("echoplay.ui.main_window.load_session", return_value={}):
                    window = MainWindow(store)
                self.assertEqual(tracker.windows, [])
                for badge in window.library_collection_badges.values():
                    self.assertFalse(badge.isWindow())
                window.library_playlist_list.setCurrentRow(1)
                window.library_playlist_list.setCurrentRow(0)
                self.app.processEvents()
                self.assertEqual(tracker.windows, [])
            finally:
                self.app.removeEventFilter(tracker)
                if "window" in locals():
                    window.close()

    def test_startup_never_shows_unparented_controls(self):
        for skin in ("default", "excel"):
            for logged_in in (False, True):
                with self.subTest(skin=skin, logged_in=logged_in):
                    settings = QSettings()
                    settings.setValue("appearance/skin", skin)
                    cookies = {"SESSDATA": "test"} if logged_in else {}
                    with tempfile.TemporaryDirectory() as directory:
                        tracker = _TopLevelShowTracker(self.app)
                        self.app.installEventFilter(tracker)
                        window = None
                        try:
                            with patch("echoplay.ui.main_window.load_session", return_value=cookies), \
                                 patch.object(MainWindow, "_fetch_account_profile"):
                                window = MainWindow(LibraryStore(Path(directory) / "library.sqlite3"))
                            self.app.processEvents()
                            self.assertEqual(tracker.windows, [])
                        finally:
                            self.app.removeEventFilter(tracker)
                            if window is not None:
                                window.close()
        QSettings().remove("appearance/skin")
