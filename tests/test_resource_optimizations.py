import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import io
import wave
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from echoplay.models import Track
from echoplay.storage import LibraryStore
from echoplay.ui.library_helpers import group_library_tracks
from echoplay.ui.main_window import MainWindow
from echoplay.ui.widgets import MarqueeLabel
from echoplay.services.prefetch import AudioPrefetch
from echoplay.services.playback import AudioStream


def track(page=1, standalone=False):
    return Track("BV1234567890", "Collection", "Artist", "03:00", "",
                 "https://www.bilibili.com/video/BV1234567890",
                 page_number=page, part_title=f"Part {page}",
                 video_title="Collection", is_standalone=standalone)


class ResourceOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          cls.directory.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("EchoPlayResourceTests")
        cls.app.setApplicationName("ResourceTests")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def wait_until(self, condition):
        for _ in range(100):
            if condition():
                return True
            QTest.qWait(10)
        return condition()

    def test_grouping_preserves_standalone_and_collection_order(self):
        tracks = [track(2), track(1, True), track(1)]
        rows = group_library_tracks(tracks)
        self.assertEqual(rows[0][1], [tracks[0], tracks[2]])
        self.assertEqual(rows[1], (tracks[1], [tracks[1]], True))
        self.assertEqual(tracks, [track(2), track(1, True), track(1)])

    def test_playback_order_survives_window_restart(self):
        settings = QSettings()
        previous = settings.value("playback/order")
        with tempfile.TemporaryDirectory() as directory, \
             patch("echoplay.ui.main_window.load_session", return_value={}), \
             patch.object(MainWindow, "_start_worker"):
            try:
                for mode in ("列表循环", "单曲循环", "随机播放"):
                    window = MainWindow(LibraryStore(Path(directory) / "library.db"))
                    try:
                        window.playback_order_buttons[mode].click()
                        self.assertEqual(QSettings().value("playback/order"), mode)
                    finally:
                        window.close()
                    restored = MainWindow(LibraryStore(Path(directory) / "library.db"))
                    try:
                        self.assertEqual(restored._selected_playback_order(), mode)
                        self.assertEqual(restored.playback_order, mode)
                        self.assertTrue(restored.playback_order_buttons[mode].isChecked())
                    finally:
                        restored.close()
                settings.setValue("playback/order", "invalid")
                settings.sync()
                window = MainWindow(LibraryStore(Path(directory) / "library.db"))
                try:
                    self.assertEqual(window._selected_playback_order(), "列表循环")
                finally:
                    window.close()
            finally:
                if previous is None:
                    settings.remove("playback/order")
                else:
                    settings.setValue("playback/order", previous)
                settings.sync()

    def test_count_matches_saved_playlist_after_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.sqlite3")
            for item in [track(1), track(2), track(1, True)]:
                store.add(item)
            playlist = store.create_playlist("Test")
            store.add_track_to_playlist(playlist, track(1))
            self.assertEqual(store.count_tracks(), 3)
            self.assertEqual(store.count_tracks(playlist), len(store.list_playlist_tracks(playlist)))
            store.remove(track().bvid, 1, False)
            self.assertEqual(store.count_tracks(playlist), 0)

    def test_hidden_marquee_stops_timer_and_resumes(self):
        label = MarqueeLabel("Long title " * 50)
        label.resize(100, 30)
        self.assertFalse(label._scroll_timer.isActive())
        label.show()
        self.app.processEvents()
        self.assertTrue(label._scroll_timer.isActive())
        label.hide()
        self.assertFalse(label._scroll_timer.isActive())
        label.show()
        self.app.processEvents()
        self.assertTrue(label._scroll_timer.isActive())
        label.close()

    def test_filter_coalesces_edits_and_all_themes_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.sqlite3")
            store.add(track())
            with patch("echoplay.ui.main_window.load_session", return_value={}):
                window = MainWindow(store)
            try:
                with patch.object(window, "_list_current_library_tracks",
                                  wraps=window._list_current_library_tracks) as listing:
                    for query in ("C", "Co", "Col"):
                        window.library_filter.setText(query)
                    self.assertEqual(listing.call_count, 0)
                    QTest.qWait(300)
                    self.assertGreater(listing.call_count, 0)
                    self.assertEqual(window.library_items.count(), 1)
                for skin in ("excel", "default"):
                    for theme in ("dark", "light"):
                        window._set_skin(skin)
                        window._set_theme(theme)
                        self.app.processEvents()
                window.library_filter.setText("no matching result")
                QTest.qWait(300)
                self.assertEqual(window.library_items.count(), 0)
            finally:
                window.close()

    def test_prefetched_track_reuses_preloaded_player(self):
        raw = io.BytesIO()
        with wave.open(raw, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00" * 64000)
        item = track()
        job = AudioPrefetch(item, {})
        job._result = (AudioStream("https://audio.test", {}, "test", "wav"),
                       bytearray(raw.getvalue()))
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.sqlite3")
            store.add(item)
            with patch("echoplay.ui.main_window.load_session", return_value={}):
                window = MainWindow(store)
            try:
                window._next_audio_prefetch = job
                window._prepare_preloaded_audio(job)
                QTest.qWait(100)
                _, buffer, player = window._preloaded_audio
                with patch.object(window, "_schedule_next_audio_prefetch"), \
                     patch.object(window, "_start_worker") as start:
                    window._request_play_track(item, require_library=True)
                    self.assertIs(window.media_player, player)
                    self.assertIs(window.audio_buffer, buffer)
                    start.assert_not_called()
            finally:
                window.close()

    def test_ribbon_size_stays_fixed_across_window_state_transitions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.sqlite3")
            with patch("echoplay.ui.main_window.load_session", return_value={}):
                window = MainWindow(store)
            try:
                window._set_skin("excel")
                window.show()
                self.app.processEvents()
                for theme in ("light", "dark"):
                    window._set_theme(theme)
                    expected = window.excel_ribbon.pixmap().size()
                    for show in (window.showFullScreen, window.showNormal,
                                 window.showMaximized, window.showNormal):
                        show()
                        self.app.processEvents()
                        self.assertEqual(window.excel_ribbon.pixmap().size(), expected)
                    # Reproduce the Windows restore ordering: the state still
                    # reports maximized after the client has become narrower.
                    window.resize(1100, 800)
                    with patch.object(window, "isMaximized", return_value=True):
                        window._update_excel_ribbon_display_size()
                        self.assertEqual(window.excel_ribbon.pixmap().size(), expected)
            finally:
                window.close()

    def test_library_click_toggles_collection_but_double_click_only_plays(self):
        for skin in ("default", "excel"):
            with self.subTest(skin=skin), tempfile.TemporaryDirectory() as directory:
                store = LibraryStore(Path(directory) / "library.db")
                for page in (1, 2, 3):
                    store.add(track(page))
                store.add(track(1, True))
                with patch("echoplay.ui.main_window.load_session", return_value={}), \
                     patch.object(MainWindow, "_start_worker") as worker:
                    window = MainWindow(store)
                    try:
                        window._set_skin(skin)
                        window.page_stack.setCurrentWidget(window.library_tab)
                        window.library_click_timer.setInterval(20)
                        window.show()
                        self.app.processEvents()
                        item = next(item for item in window.library_row_items.values()
                                    if len(window.library_tracks_by_item[id(item)]) > 1)
                        label = window.library_title_labels[id(item)]
                        panel = window.library_page_panels[id(item)]
                        self.assertTrue(panel.isHidden())
                        worker.reset_mock()
                        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
                        self.assertTrue(self.wait_until(lambda: not panel.isHidden()))
                        self.assertEqual(window.library_page_lists[id(item)].count(), 3)
                        worker.assert_not_called()
                        QTest.mouseClick(window.library_page_lists[id(item)].viewport(),
                                         Qt.MouseButton.LeftButton)
                        QTest.qWait(40)
                        self.assertFalse(panel.isHidden())
                        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
                        self.assertTrue(self.wait_until(panel.isHidden))
                        with patch.object(window, "play_library_track") as play:
                            QTest.mouseClick(label, Qt.MouseButton.LeftButton)
                            QTest.mouseDClick(label, Qt.MouseButton.LeftButton)
                            QTest.qWait(40)
                            play.assert_called_once_with(item)
                            self.assertTrue(panel.isHidden())
                        single = next(item for item in window.library_row_items.values()
                                      if item.data(Qt.ItemDataRole.UserRole).is_standalone)
                        QTest.mouseClick(window.library_title_labels[id(single)], Qt.MouseButton.LeftButton)
                        self.assertFalse(window.library_click_timer.isActive())
                        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
                        window.refresh_library()
                        QTest.qWait(40)
                        self.assertIsNone(window.pending_library_click)
                        self.assertFalse(window.library_click_timer.isActive())
                    finally:
                        window.close()
