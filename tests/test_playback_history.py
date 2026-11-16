import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QListWidgetItem
from echoplay.models import Track
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow


class PlaybackHistoryTests(unittest.TestCase):
    def test_history_limit_recency_pages_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'library.db'
            store = LibraryStore(path)
            tracks = [Track(f'BV{i}', f'Track {i}', 'Author', '03:00', '', '') for i in range(55)]
            for track in tracks:
                store.record_playback(track)
            self.assertEqual(store.list_playback_history(), list(reversed(tracks[5:])))
            store.record_playback(tracks[10])
            self.assertEqual(store.list_playback_history()[0], tracks[10])
            self.assertEqual(len(store.list_playback_history()), 50)
            part = replace(tracks[10], page_number=2, page_url='https://www.bilibili.com/video/BV10?p=2')
            store.record_playback(part)
            history = LibraryStore(path).list_playback_history()
            self.assertEqual(history[:2], [part, tracks[10]])
            self.assertEqual(len(history), 50)
            self.assertEqual(store.list_tracks(), [])

    def test_button_results_themes_and_stale_search(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory)
            app.setOrganizationName('EchoPlayHistoryTests')
            app.setApplicationName('HistoryTests')
            store = LibraryStore(Path(directory) / 'library.db')
            with patch('echoplay.ui.main_window.load_session', return_value={}), patch.object(MainWindow, '_start_worker'):
                window = MainWindow(store)
                try:
                    window.page_stack.setCurrentWidget(window.search_tab)
                    window.show()
                    window.history_button.click()
                    self.assertEqual(window.results.count(), 1)
                    self.assertIn('暂无', window.search_status.text())
                    track = Track('BV1', 'Song', 'Author', '03:00', '', '')
                    window._request_play_track(track, record_history=True)
                    window.active_play_id = track.playback_id
                    window._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
                    window._on_playback_state_changed(QMediaPlayer.PlaybackState.PausedState)
                    window._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
                    self.assertEqual(store.list_playback_history(), [track])
                    for skin in ('default', 'excel'):
                        window._set_skin(skin)
                        for theme in ('light', 'dark'):
                            window._set_theme(theme)
                            window.history_button.click()
                            app.processEvents()
                            self.assertEqual(window.history_button.size(), window.search_button.size())
                            self.assertLess(window.history_button.mapTo(window, window.history_button.rect().topLeft()).x(),
                                            window.search_button.mapTo(window, window.search_button.rect().topLeft()).x())
                            self.assertEqual(window.results.count(), 2)
                            marker = window.results.item(0)
                            self.assertEqual(marker.text(), "— 历史记录 —")
                            self.assertEqual(marker.flags(), Qt.ItemFlag.NoItemFlags)
                            self.assertEqual(marker.textAlignment(), Qt.AlignmentFlag.AlignCenter)
                            self.assertEqual(window.results.item(1).data(Qt.ItemDataRole.UserRole), track)
                    old_generation = window.search_generation - 1
                    window._search_page_succeeded([], 1, old_generation)
                    self.assertEqual(window.results.count(), 2)
                    self.assertFalse(window.search_has_more)
                    window.search_input.setText('new query')
                    window.search()
                    self.assertEqual(window.results.count(), 0)
                    self.assertEqual(window.search_keyword, 'new query')
                finally:
                    window.close()

    def test_long_collection_history_details_match_search_without_blank_gap(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory)
            app.setOrganizationName("EchoPlayHistoryLayoutTests")
            app.setApplicationName("LayoutTests")
            store = LibraryStore(Path(directory) / "library.db")
            track = Track("BV1", "Long collection title " * 12, "Author", "03:00", "", "")
            store.record_playback(track)
            pages = [replace(track, page_number=i + 1, part_title=f"Part {i + 1}")
                     for i in range(20)]
            with patch("echoplay.ui.main_window.load_session", return_value={}), \
                 patch.object(MainWindow, "_start_worker"):
                window = MainWindow(store)
                try:
                    window._set_skin("default")
                    window.page_stack.setCurrentWidget(window.search_tab)
                    window.resize(1100, 800)
                    window.show()
                    app.processEvents()
                    sizes = []
                    for history in (True, False):
                        if history:
                            window.show_playback_history()
                        else:
                            window._clear_search_results()
                            window._append_search_results([track], 1)
                        app.processEvents()
                        item = next(iter(window.search_result_row_items.values()))
                        window._search_result_pages_loaded(item, pages)
                        for _ in range(4):
                            app.processEvents()
                        container = window.search_result_containers[id(item)]
                        row = container.layout().itemAt(0).widget()
                        panel = window.search_result_page_panels[id(item)]
                        details = window.search_result_page_lists[id(item)]
                        self.assertEqual(details.y(), 0)
                        self.assertEqual(panel.y() - row.geometry().bottom() - 1, 6)
                        self.assertEqual(panel.height(), details.height() + 8)
                        self.assertLessEqual(item.sizeHint().height(),
                                             row.height() + details.height() + 14)
                        sizes.append(item.sizeHint().height())
                    self.assertEqual(sizes[0], sizes[1])
                finally:
                    window.close()

    def test_only_successful_search_playback_is_recorded_not_library_or_auto_next(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, directory)
            app.setOrganizationName('HistoryOriginTests');app.setApplicationName('OriginTests')
            store = LibraryStore(Path(directory) / 'library.db')
            library = Track('BVlibrary', 'Library', 'Artist', '03:00', '', '')
            searched = Track('BVsearch', 'Search', 'Artist', '03:00', '', '')
            part = replace(searched, page_number=2, part_title='Part 2')
            for track in (library, searched): store.add(track)
            def item(track):
                result = QListWidgetItem()
                result.setData(Qt.ItemDataRole.UserRole, track)
                return result
            with patch('echoplay.ui.main_window.load_session', return_value={}), \
                 patch.object(MainWindow, '_start_worker'):
                window = MainWindow(store)
                try:
                    def started():
                        window.pending_play_id = None
                        window.active_play_id = window.current_track.playback_id
                        window._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
                    with patch.object(store, 'record_playback', wraps=store.record_playback) as record:
                        window.play_library_track(item(library));started()
                        record.assert_not_called()
                        self.assertEqual(store.list_playback_history(), [])
                        window.play_search_result(item(searched))
                        self.assertEqual(store.list_playback_history(), [])
                        started()
                        window._on_playback_state_changed(QMediaPlayer.PlaybackState.PausedState)
                        window._on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
                        record.assert_called_once_with(searched)
                        window.play_library_track(item(searched));started()
                        self.assertEqual(record.call_count, 1)
                        window._play_search_page(item(part))
                        played_part = window.current_track
                        started()
                        self.assertEqual(store.list_playback_history(), [played_part, searched])
                        self.assertEqual(record.call_count, 2)
                        window._play_track(library);started()
                        self.assertEqual(record.call_count, 2)
                        # A search request that never starts must not leak its
                        # history policy into a following library request.
                        window.play_search_result(item(part))
                        window.play_library_track(item(library));started()
                        self.assertEqual(record.call_count, 2)
                        self.assertEqual(store.list_playback_history(), [played_part, searched])
                finally:
                    window.close()
