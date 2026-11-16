import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from echoplay.models import Track
from echoplay.services.playback import resolve_audio_stream
from echoplay.services.stream_quality import has_active_vip, select_real_stream
from echoplay.storage import LibraryStore
from echoplay.ui.main_window import MainWindow


class StreamSelectionTests(unittest.TestCase):
    def test_membership_requires_active_not_expired_status(self):
        self.assertFalse(has_active_vip({}))
        self.assertFalse(has_active_vip({"vipType": 2, "vipStatus": 0}))
        self.assertFalse(has_active_vip({"vipType": 2, "vipStatus": 1, "vipDueDate": 1}))
        self.assertFalse(has_active_vip({"vipType": 2, "vipStatus": 3}))
        self.assertTrue(has_active_vip({"vipType": 2, "vipStatus": 1}))
        self.assertTrue(has_active_vip({"vip": {"type": 1, "status": 1,
                                                "due_date": int(time.time() * 1000) + 10000}}))

    def test_hires_prefers_flac_and_falls_back_to_regular_audio(self):
        audio = {"id": 30251, "baseUrl": "https://hires.test", "bandwidth": 1500000}
        data = {"dash": {"audio": [{"id": 30280, "baseUrl": "https://normal.test"}],
                         "flac": {"audio": audio}}}
        selected = select_real_stream(data, "HiRes", {})
        self.assertEqual(selected.url, "https://hires.test")
        del data["dash"]["flac"]
        self.assertEqual(select_real_stream(data, "HiRes", {}).url, "https://normal.test")

    def test_hires_missing_url_falls_back_to_best_audio(self):
        data = {"dash": {"flac": {"audio": {"id": 30251}}, "audio": [
            {"id": 30216, "baseUrl": "https://low.test", "bandwidth": 64000},
            {"id": 30280, "base_url": "https://high.test", "bandwidth": 192000}]}}
        self.assertEqual(select_real_stream(data, "HiRes", {}).url, "https://high.test")
        data["dash"]["dolby"] = {"audio": [
            {"id": 30250, "baseUrl": "https://dolby.test", "bandwidth": 384000}]}
        self.assertEqual(select_real_stream(data, "HiRes", {}).url, "https://dolby.test")
        data["dash"]["audio"].append({"id": 30251, "baseUrl": "https://hires.test"})
        self.assertEqual(select_real_stream(data, "HiRes", {}).url, "https://hires.test")

    def test_hires_without_playable_stream_still_fails(self):
        for data in ({}, {"is_preview": True}):
            with self.assertRaises(RuntimeError):
                select_real_stream(data, "HiRes", {})

    def test_video_stream_requires_exact_quality_and_complete_mp4(self):
        data = {"quality": 16, "format": "mp4", "durl": [
            {"url": "https://360.test", "size": 1000000, "length": 10000}]}
        self.assertEqual(select_real_stream(data, "360P", {}).format_hint, "mp4")
        data["quality"] = 80
        with self.assertRaises(RuntimeError):
            select_real_stream(data, "360P", {})
        data["quality"] = 16
        data["is_preview"] = True
        with self.assertRaises(RuntimeError):
            select_real_stream(data, "360P", {})
        del data["is_preview"]
        data["durl"] *= 2
        with self.assertRaises(RuntimeError):
            select_real_stream(data, "360P", {})

    def test_1080_selects_best_non_hires_audio_including_dolby(self):
        audio = [
            {"id": 30216, "baseUrl": "https://low.test", "bandwidth": 64000},
            {"id": 30280, "baseUrl": "https://high.test", "bandwidth": 192000},
            {"id": 30251, "baseUrl": "https://hires.test", "bandwidth": 1500000},
        ]
        data = {"dash": {"audio": audio, "flac": {"audio": audio[-1]}}}
        self.assertEqual(select_real_stream(data, "1080P", {}).url, "https://high.test")
        data["dash"]["dolby"] = {"audio": [
            {"id": 30250, "baseUrl": "https://dolby.test", "bandwidth": 384000}]}
        self.assertEqual(select_real_stream(data, "1080P", {}).url, "https://dolby.test")
        data = {"dash": {"audio": [audio[-1]], "flac": {"audio": audio[-1]}}}
        with self.assertRaises(RuntimeError):
            select_real_stream(data, "1080P", {})

    def test_1080_can_use_lower_available_audio_without_hires(self):
        data = {"dash": {"audio": [
            {"id": 30216, "base_url": "https://low.test", "bandwidth": 64000},
            {"id": 30280},
            {"id": 99999, "baseUrl": "https://lossless.test", "codecs": "flac"},
        ]}}
        self.assertEqual(select_real_stream(data, "1080P", {}).url, "https://low.test")

    def test_resolver_passes_real_quality_parameters(self):
        track = Track("BV1234567890", "Track", "Artist", "03:00", "", "")
        for quality, qn in (("360P", 16), ("1080P", 80)):
            data = {"quality": qn, "format": "mp4", "durl": [{"url": "https://video.test"}]}
            if quality == "1080P":
                data = {"dash": {"audio": [{"id": 30280, "baseUrl": "https://audio.test"}]}}
            with patch("echoplay.services.playback._get_json", side_effect=[
                {"pages": [{"cid": 123}]}, {}, data,
            ]) as get_json, patch("echoplay.services.playback._wbi_mixin_key", return_value="key"):
                result = resolve_audio_stream(track, {}, quality)
                query = parse_qs(urlsplit(get_json.call_args.args[1]).query)
                self.assertEqual(query["qn"], [str(qn)])
                self.assertEqual(query["fnval"], ["1" if quality == "360P" else "4048"])
                self.assertEqual(result.url, "https://video.test" if quality == "360P" else "https://audio.test")


class QualitySettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                          cls.directory.name)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setOrganizationName("EchoPlayQualityTests")
        cls.app.setApplicationName("QualityTests")

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_selection_persistence_vip_gating_and_cache_invalidation(self):
        QSettings().clear()
        with tempfile.TemporaryDirectory() as directory:
            store = LibraryStore(Path(directory) / "library.db")
            with patch("echoplay.ui.main_window.load_session", return_value={}):
                window = MainWindow(store)
            try:
                hires = window.quality_buttons["HiRes"]
                self.assertFalse(hires.isEnabled())
                window.quality_buttons["360P"].click()
                self.assertEqual(window.stream_quality, "360P")
                self.assertEqual(QSettings().value("playback/stream_quality"), "360P")
                window.bilibili_cookies = {"SESSDATA": "test"}
                window._account_profile_loaded(("VIP", "", True), dict(window.bilibili_cookies))
                self.assertTrue(hires.isEnabled())
                job = Mock()
                window._next_audio_prefetch = job
                window.quality_buttons["HiRes"].click()
                self.assertEqual(window.stream_quality, "HiRes")
                job.cancel.assert_called_once()
                self.assertIsNone(window._next_audio_prefetch)
                window._account_profile_loaded(("Expired", "", False), dict(window.bilibili_cookies))
                self.assertFalse(hires.isEnabled())
                self.assertEqual(window.stream_quality, "1080P")
                window._set_stream_quality("HiRes")
                self.assertTrue(window.quality_buttons["1080P"].isChecked())
                for skin in ("default", "excel"):
                    window._set_skin(skin)
                    for theme in ("light", "dark"):
                        window._set_theme(theme)
                        self.assertFalse(hires.isEnabled())
                window._set_stream_quality("360P")
            finally:
                window.close()
            with patch("echoplay.ui.main_window.load_session", return_value={}):
                reopened = MainWindow(store)
            try:
                self.assertTrue(reopened.quality_buttons["360P"].isChecked())
                self.assertEqual(reopened.stream_quality, "360P")
            finally:
                reopened.close()
