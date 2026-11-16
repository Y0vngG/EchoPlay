import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from echoplay.models import Track
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow
from echoplay.ui.widgets import AddToLibraryButton


class SavedButtonTests(unittest.TestCase):
    def test_saved_state_add_remove_size_and_disabled_click_in_all_views(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,directory)
            app.setOrganizationName('SavedButtonTests');app.setApplicationName('SavedButtons')
            store=LibraryStore(Path(directory)/'library.db')
            track=Track('BV1234567890','Song','Author','03:00','','')
            saved=replace(track,bvid='BV9876543210')
            store.add(saved)
            for t in (saved,track): store.record_playback(t)
            with patch('echoplay.ui.main_window.load_session',return_value={}),patch.object(MainWindow,'_start_worker'):
                w=MainWindow(store)
                try:
                    w.page_stack.setCurrentWidget(w.search_tab);w.show()
                    for history in (False,True):
                        for skin in ('default','excel'):
                            for theme in ('light','dark'):
                                w._set_skin(skin);w._set_theme(theme)
                                if history: w.show_playback_history()
                                else:
                                    w._clear_search_results();w._append_search_results([track,saved],1)
                                for _ in range(3): app.processEvents()
                                buttons={item.data(Qt.ItemDataRole.UserRole).bvid:
                                         w.search_result_containers[key].findChild(AddToLibraryButton)
                                         for key,item in w.search_result_row_items.items()}
                                plus=buttons[track.bvid];check=buttons[saved.bvid]
                                self.assertEqual(plus.text(),'+');self.assertTrue(plus.isEnabled())
                                self.assertEqual(check.text(),'\u2714');self.assertFalse(check.isEnabled())
                                self.assertEqual(plus.size(),check.size())
                                self.assertEqual(check.toolTip(),'')
                                if skin == 'excel':
                                    normal = plus.grab().toImage()
                                    added = check.grab().toImage()
                                    for x, y in ((0, 10), (1, 1), (8, 8),
                                                 (normal.width()-2, 2)):
                                        self.assertEqual(added.pixelColor(x, y), normal.pixelColor(x, y))
                                with patch.object(w,'add_to_library') as add:
                                    QTest.mouseClick(check,Qt.MouseButton.LeftButton)
                                    app.processEvents()
                                    add.assert_not_called()
                                self.assertFalse(w.search_result_click_timer.isActive())
                                original_size=plus.size()
                                QTest.mouseClick(plus,Qt.MouseButton.LeftButton)
                                for _ in range(3): app.processEvents()
                                self.assertEqual(plus.text(),'\u2714');self.assertFalse(plus.isEnabled())
                                self.assertEqual(plus.size(),original_size)
                                store.remove(track.bvid);w.refresh_library()
                                for _ in range(3): app.processEvents()
                                self.assertEqual(plus.text(),'+');self.assertTrue(plus.isEnabled())
                                self.assertEqual(plus.size(),original_size)
                    # A saved standalone page counts as the same playable part,
                    # but another page of that video must remain addable.
                    store.add(replace(track,is_standalone=True))
                    self.assertIn(track.track_id,store.saved_track_ids())
                    self.assertNotIn((track.bvid,2),store.saved_track_ids())
                finally:w.close()
