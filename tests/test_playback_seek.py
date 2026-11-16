"""Seek regression checks independent of network and audio hardware."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QIODevice
from PySide6.QtMultimedia import QMediaPlayer

from echoplay.services.playback import StreamingAudioDevice
from echoplay.ui.main_window import MainWindow


class PlaybackSeekTests(unittest.TestCase):
    def window(self, playing=True, pending=None, resume=False):
        player = Mock()
        player.isSeekable.return_value = True
        player.playbackState.return_value = (
            QMediaPlayer.PlaybackState.PlayingState if playing
            else QMediaPlayer.PlaybackState.PausedState
        )
        slider = Mock()
        slider.value.return_value = 20
        return SimpleNamespace(
            media_player=player, progress_slider=slider, elapsed_label=Mock(),
            audio_buffer=SimpleNamespace(producer_done=False),
            _seeking=True, _pending_seek_position=pending,
            _resume_after_download=resume, _format_time=MainWindow._format_time,
        )

    def test_seek_while_downloading_applies_immediately(self):
        window = self.window()
        MainWindow._finish_seek(window)
        window.media_player.setPosition.assert_called_once_with(20000)
        window.media_player.play.assert_called_once()
        window.media_player.pause.assert_not_called()
        self.assertIsNone(window._pending_seek_position)
        self.assertFalse(window._seeking)

    def test_paused_seek_keeps_paused(self):
        window = self.window(playing=False)
        MainWindow._finish_seek(window)
        window.media_player.setPosition.assert_called_once_with(20000)
        window.media_player.play.assert_not_called()

    def test_new_seek_replaces_deferred_seek_and_restores_playback(self):
        window = self.window(playing=False, pending=5000, resume=True)
        MainWindow._finish_seek(window)
        window.media_player.setPosition.assert_called_once_with(20000)
        window.media_player.play.assert_called_once()
        self.assertIsNone(window._pending_seek_position)
        self.assertFalse(window._resume_after_download)

    def test_unseekable_source_is_ignored(self):
        window = self.window()
        window.media_player.isSeekable.return_value = False
        MainWindow._finish_seek(window)
        window.media_player.setPosition.assert_not_called()
        self.assertFalse(window._seeking)

    def test_stream_read_resumes_before_full_download(self):
        device = StreamingAudioDevice()
        self.assertTrue(device.open(QIODevice.OpenModeFlag.ReadOnly))
        device.set_expected_size(100)
        device.append(b"0123456789")
        self.assertTrue(device.seek(2))
        self.assertEqual(device.readData(3), b"234")
        self.assertTrue(device.seek(12))
        result = []
        entered = threading.Event()
        def read():
            entered.set()
            result.append(device.readData(2))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            self.assertTrue(entered.wait(1))
            device.append(b"abcdefghij")
            reader.join(1)
            self.assertFalse(reader.is_alive())
            self.assertEqual(result, [b"cd"])
            self.assertFalse(device.producer_done)
            self.assertEqual(device.total_received, 20)
        finally:
            device.cancel()
            reader.join(1)
            device.close()
