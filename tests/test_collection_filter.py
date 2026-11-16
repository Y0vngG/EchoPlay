import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication
from echoplay.models import Track
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow


class CollectionFilterTests(unittest.TestCase):
    def test_part_matches_expand_only_matching_parts_and_restore_full_collection(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,d)
            app.setOrganizationName('CollectionFilterTests');app.setApplicationName('FilterTests')
            store=LibraryStore(Path(d)/'library.db')
            pages=[Track('BVcollection','Collection','Artist','03:00','','',page_number=i,
                         part_title=title,video_title='Collection')
                   for i,title in enumerate(('Intro','Blue Sky','Skyline'),1)]
            for track in pages:store.add(track)
            other=Track('BVother','Other','Another artist','03:00','','',is_standalone=True)
            store.add(other)
            playlist=store.create_playlist('Only two parts')
            for track in pages[:2]:store.add_track_to_playlist(playlist,track)
            self.assertEqual({p.page_number for p in store.list_tracks('Sky')},{2,3})
            self.assertEqual([p.page_number for p in store.list_playlist_tracks(playlist,'Sky')],[2])
            with patch('echoplay.ui.main_window.load_session',return_value={}),patch.object(MainWindow,'_start_worker'):
                w=MainWindow(store)
                try:
                    w.page_stack.setCurrentWidget(w.library_tab);w.show()
                    for skin in ('default','excel'):
                        for theme in ('light','dark'):
                            w._set_skin(skin);w._set_theme(theme)
                            w.library_filter.clear();w.refresh_library()
                            normal_item=next(item for item in w.library_row_items.values()
                                if item.data(Qt.ItemDataRole.UserRole).bvid=='BVcollection')
                            w.toggle_library_pages(normal_item)
                            for _ in range(3):app.processEvents()
                            normal_list=w.library_page_lists[id(normal_item)]
                            normal_size=normal_list.size()
                            normal_panel_size=w.library_page_panels[id(normal_item)].size()
                            w.library_filter.setText('sKy');w.refresh_library()
                            for _ in range(3):app.processEvents()
                            self.assertEqual(len(w.library_row_items),1)
                            item=next(iter(w.library_row_items.values()))
                            key=id(item);listing=w.library_page_lists[key]
                            self.assertEqual(item.data(Qt.ItemDataRole.UserRole).title,'Collection')
                            self.assertFalse(w.library_page_panels[key].isHidden())
                            self.assertEqual([listing.item(i).text() for i in range(listing.count())],
                                             ['Blue Sky','Skyline'])
                            self.assertEqual(len(w.library_tracks_by_item[key]),3)
                            self.assertEqual(listing.size(),normal_size)
                            self.assertEqual(w.library_page_panels[key].size(),normal_panel_size)
                            w._set_theme(theme)
                            for _ in range(3):app.processEvents()
                            self.assertEqual(listing.size(),normal_size)
                            # Collapsing/reopening must not bring nonmatching parts back.
                            w.toggle_library_pages(item);w.toggle_library_pages(item)
                            self.assertEqual(listing.count(),2)
                            with patch.object(w,'_request_play_track') as play:
                                w._play_library_page(listing.item(0))
                                self.assertEqual(play.call_args.args[0].page_number,2)
                            w.library_filter.setText('Blue');w.refresh_library()
                            item=next(iter(w.library_row_items.values()));key=id(item)
                            self.assertEqual(item.data(Qt.ItemDataRole.UserRole).title,'Collection')
                            self.assertFalse(w.library_page_panels[key].isHidden())
                            self.assertEqual(w.library_page_lists[key].count(),1)
                            for _ in range(3):app.processEvents()
                            self.assertEqual(w.library_page_lists[key].size(),normal_size)
                            w.library_filter.setText('absent keyword');w.refresh_library()
                            self.assertEqual(w.library_items.count(),0)
                            w.library_filter.clear();w.refresh_library()
                            self.assertEqual(w.library_items.count(),2)
                            item=next(item for item in w.library_row_items.values()
                                      if item.data(Qt.ItemDataRole.UserRole).bvid=='BVcollection')
                            w.toggle_library_pages(item)
                            self.assertEqual(w.library_page_lists[id(item)].count(),3)
                            w.selected_library_playlist_id=playlist
                            w.library_filter.setText('Sky');w.refresh_library()
                            item=next(iter(w.library_row_items.values()));key=id(item)
                            self.assertFalse(w.library_page_panels[key].isHidden())
                            listing=w.library_page_lists[key]
                            self.assertEqual(listing.count(),1)
                            self.assertEqual(listing.item(0).data(Qt.ItemDataRole.UserRole).page_number,2)
                            w.selected_library_playlist_id=None
                            w.library_filter.clear()
                finally:w.close()
