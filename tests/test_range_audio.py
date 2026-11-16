import io
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QIODevice

from echoplay.services.playback import AudioStream
from echoplay.services.range_audio import BLOCK_SIZE, RangeAudioDevice


class Response(io.BytesIO):
    def __init__(self, data, start, end):
        super().__init__(data[start:end + 1])
        self.headers = {"Content-Range": f"bytes {start}-{end}/{len(data)}"}

    def getcode(self):
        return 206


class RangeAudioTests(unittest.TestCase):
    def device(self, bandwidth=192000):
        device = RangeAudioDevice(AudioStream("https://audio.test", {}, "test", "m4a",
                                             bandwidth=bandwidth))
        device.open(QIODevice.OpenModeFlag.ReadOnly)
        self.addCleanup(device.close)
        return device

    def test_startup_budget_tracks_bitrate(self):
        self.assertEqual(self.device().stream.startup_bytes, 42000)
        self.assertEqual(self.device(64000).stream.startup_bytes, 14000)
        self.assertEqual(self.device(1000000).stream.startup_bytes, 218750)
        high_res = self.device(3000000)
        self.assertGreaterEqual(high_res._block_size, high_res.stream.startup_bytes)

    def test_startup_and_seek_prioritize_target_before_full_download(self):
        data = bytes(range(256)) * (BLOCK_SIZE * 4 // 256)
        device = self.device()
        requests = []
        ready_bytes = []
        target = BLOCK_SIZE * 3
        def opening(request, **kwargs):
            value = request.get_header("Range").removeprefix("bytes=")
            start, end = map(int, value.split("-"))
            requests.append(start)
            return Response(data, start, min(end, len(data) - 1))
        def ready():
            ready_bytes.append(device.total_received)
            self.assertTrue(device.seek(target))
        with patch.object(device._transport, "open", side_effect=opening):
            received = device.download(ready, lambda *args: None)
        self.assertEqual(len(ready_bytes), 1)
        self.assertEqual(ready_bytes[0], device.stream.startup_bytes)
        self.assertLess(ready_bytes[0], 256 * 1024)
        self.assertEqual(requests[:2], [0, target])
        self.assertEqual(received, len(data))
        self.assertEqual(device.readData(100), data[target:target + 100])
        self.assertTrue(device.seek(0))
        self.assertEqual(device.readData(100), data[:100])

    def test_reader_returns_cached_bytes_without_waiting_for_budget(self):
        device = self.device(64000)
        device._size = BLOCK_SIZE * 2
        device._blocks[0] = bytearray(b"x" * 1000)
        result = []
        entered = threading.Event()
        done = threading.Event()
        def read():
            entered.set()
            result.append(device.readData(100))
            done.set()
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(done.wait(1))
            with device._condition:
                device._blocks[0].extend(b"x" * 13000)
                device._condition.notify_all()
            self.assertTrue(done.wait(1))
            self.assertEqual(result, [b"x" * 100])
            self.assertFalse(device._finished)
        finally:
            device.cancel()
            reader.join(1)

    def test_bad_range_is_rejected_and_waiters_are_released(self):
        device = self.device()
        with patch.object(device._transport, "open",
                   return_value=Response(b"x" * 100, 10, 99)):
            with self.assertRaises(RuntimeError):
                device.download(lambda: None, lambda *args: None)
        self.assertTrue(device._finished)

    def test_short_track_starts_at_end_without_waiting_for_budget(self):
        device = self.device()
        ready = []
        with patch.object(device._transport, "open",
                   return_value=Response(b"x" * 100, 0, 99)):
            self.assertEqual(device.download(lambda: ready.append(True), lambda *args: None), 100)
        self.assertEqual(ready, [True])
