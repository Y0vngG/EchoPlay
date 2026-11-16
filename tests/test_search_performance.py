import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import QBuffer, QIODevice, QSettings
from PySide6.QtGui import QPixmap, QColor
from PySide6.QtWidgets import QApplication
from echoplay.models import Track
from echoplay.services import bilibili as api
from echoplay.ui.main_window import MainWindow
from echoplay.ui.workers import Worker
from echoplay.storage import LibraryStore


class SearchServicePerformanceTests(unittest.TestCase):
    def setUp(self):
        api._SEARCH_SESSIONS.clear();api._COVER_CACHE.clear()

    def test_preparation_reused_for_pagination_and_refreshed_for_account_and_expiry(self):
        opener=Mock();opener.open.return_value.__enter__=Mock(return_value=Mock())
        opener.open.return_value.__exit__=Mock(return_value=False)
        data={'result':[{'bvid':'BV1','title':'Song','pic':'//cover.test/image.jpg'}]}
        with patch.object(api,'build_opener',return_value=opener) as build, \
             patch.object(api,'_open_nav_data',return_value={}) as nav, \
             patch.object(api,'mixin_key',return_value='key'), \
             patch.object(api,'_open_api_json',return_value=data) as get_api, \
             patch.object(api.time,'monotonic',return_value=100) as clock:
            first=api.search_videos('music',cookies={'SESSDATA':'account-a'})
            second=api.search_videos('music',cookies={'SESSDATA':'account-a'},page=2)
            self.assertEqual(first,second)
            self.assertEqual(build.call_count,1);self.assertEqual(nav.call_count,1)
            self.assertEqual(opener.open.call_count,1);self.assertEqual(get_api.call_count,2)
            api.search_videos('music',cookies={'SESSDATA':'account-b'})
            self.assertEqual(nav.call_count,2)
            clock.return_value=701
            api.search_videos('music',cookies={'SESSDATA':'account-a'})
            self.assertEqual(nav.call_count,3)
            get_api.side_effect=api.BilibiliError('failure')
            with self.assertRaises(api.BilibiliError):api.search_videos('music',cookies={'SESSDATA':'account-a'})
            get_api.side_effect=None
            api.search_videos('music',cookies={'SESSDATA':'account-a'})
            self.assertEqual(nav.call_count,4)

    def test_covers_are_cached_by_url_with_bounded_memory_and_failures_not_cached(self):
        track=Track('BV1','Song','Artist','03:00','https://cover.test/a.jpg','')
        response=Mock();response.read.return_value=b'123456'
        manager=Mock();manager.__enter__=Mock(return_value=response);manager.__exit__=Mock(return_value=False)
        with patch.object(api,'urlopen',return_value=manager) as download:
            self.assertEqual(api.fetch_cover(track),('BV1',b'123456'))
            self.assertEqual(api.fetch_cover(replace(track,bvid='BV2')),('BV2',b'123456'))
            download.assert_called_once()
            self.assertEqual(download.call_args.args[0].get_header('User-agent'),api.WEB_HEADERS['User-Agent'])
            with patch.object(api,'_COVER_CACHE_BYTES',8):
                api.fetch_cover(replace(track,cover_url='https://cover.test/b.jpg'))
                self.assertIsNone(api.cached_cover(track.cover_url))
        with patch.object(api,'urlopen',side_effect=TimeoutError):
            failed=replace(track,cover_url='https://cover.test/fail.jpg')
            self.assertEqual(api.fetch_cover(failed),('BV1',b''))
            self.assertIsNone(api.cached_cover(failed.cover_url))


class SearchQueuePerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,self.directory.name)
        self.app.setOrganizationName('SearchPerformanceTests');self.app.setApplicationName('Performance')
        with patch('echoplay.ui.main_window.load_session',return_value={}):
            self.window=MainWindow(LibraryStore(Path(self.directory.name)/'library.db'))

    def tearDown(self):
        self.window.close();self.app.processEvents();self.directory.cleanup()

    def test_search_runs_while_cover_and_metadata_queues_are_busy_and_old_queued_covers_dropped(self):
        w=self.window;release=threading.Event();cover_busy=threading.Event();meta_busy=threading.Event();searched=threading.Event()
        def cover_job(*args):cover_busy.set();release.wait(3);return None
        def metadata_job(*args):meta_busy.set();release.wait(3);return None
        def search_job(*args):searched.set();return []
        w.cover_pool.setMaxThreadCount(1);w.metadata_pool.setMaxThreadCount(1)
        with patch('echoplay.ui.main_window.fetch_cover',cover_job), \
             patch('echoplay.ui.main_window.fetch_video_pages',metadata_job), \
             patch('echoplay.ui.main_window.search_videos',search_job):
            try:
                w._start_worker(Worker(cover_job));w._start_worker(Worker(metadata_job))
                self.assertTrue(cover_busy.wait(1));self.assertTrue(meta_busy.wait(1))
                queued=Worker(cover_job);w._start_worker(queued)
                w._start_worker(Worker(search_job))
                self.assertTrue(searched.wait(1),'Search was blocked by background downloads')
                w._clear_search_results()
                self.assertNotIn(queued,w._workers)
            finally:
                release.set()
                for pool in (w.search_pool,w.cover_pool,w.metadata_pool):self.assertTrue(pool.waitForDone(2000))
                self.app.processEvents()

    def test_cover_download_coalescing_all_matching_rows_and_stale_results(self):
        w=self.window
        track=Track('BV1','Song','Artist','03:00','https://cover.test/unique.png','')
        with patch.object(w,'_start_worker') as start,patch('echoplay.ui.main_window.cached_cover',return_value=None):
            w._append_search_results([track,replace(track,page_number=2)],0)
            downloads=[call.args[0] for call in start.call_args_list if call.args[0].function is api.fetch_cover]
            self.assertEqual(len(downloads),1)
            self.assertEqual(len(w.cover_labels['BV1']),2)
            pixmap=QPixmap(8,8);pixmap.fill(QColor('red'))
            buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly);pixmap.save(buffer,'PNG')
            raw=bytes(buffer.data())
            w._cover_loaded(('BV1',raw),w._result_epoch)
            for label in w.cover_labels['BV1']:self.assertFalse(label.pixmap().isNull())
            old_epoch=w._result_epoch
            w._clear_search_results();w._append_search_results([track],0)
            label=w.cover_labels['BV1'][0]
            w._cover_loaded(('BV1',raw),old_epoch)
            self.assertTrue(label.pixmap().isNull())
            w._cover_loaded(('BV1',b''),w._result_epoch)
            self.assertIn('\u5931\u8d25',label.text())
