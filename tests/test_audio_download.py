import threading
import unittest
from unittest.mock import Mock, patch

from echoplay.models import Track
from echoplay.services.playback import AudioStream, AudioDownloadCancelled, download_audio_data
from echoplay.services.prefetch import AudioPrefetch, MAX_PREFETCH_BYTES


def response(chunks, total, start=None):
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.getcode.return_value = 200 if start is None else 206
    result.headers = {"Content-Length": str(total if start is None else total - start)}
    if start is not None:
        result.headers["Content-Range"] = f"bytes {start}-{total - 1}/{total}"
    result.read1.side_effect = chunks
    return result


class AudioDownloadTests(unittest.TestCase):
    def setUp(self):
        self.stream = AudioStream("https://primary.test/audio", {}, "test", "m4a",
                                  ("https://backup.test/audio",))

    def test_timeout_switches_cdn_and_resumes_at_received_offset(self):
        chunks = []
        def receive(chunk, received, total):
            chunks.append(chunk)
            return True
        with patch("echoplay.services.playback.urlopen", side_effect=[
            response([b"abc", TimeoutError("slow")], 6),
            response([b"def", b""], 6, start=3),
        ]) as opening:
            self.assertEqual(download_audio_data(self.stream, receive), 6)
        self.assertEqual(b"".join(chunks), b"abcdef")
        request = opening.call_args_list[1].args[0]
        self.assertEqual(request.full_url, self.stream.backup_urls[0])
        self.assertEqual(request.get_header("Range"), "bytes=3-")

    def test_wrong_resume_offset_is_rejected(self):
        with patch("echoplay.services.playback.urlopen", side_effect=[
            response([b"abc", TimeoutError("slow")], 6),
            response([b"abcdef", b""], 6, start=0),
        ]):
            with self.assertRaises(RuntimeError):
                download_audio_data(self.stream, lambda *args: True)

    def test_cancel_prevents_connection(self):
        cancelled = threading.Event()
        cancelled.set()
        with patch("echoplay.services.playback.urlopen") as opening:
            with self.assertRaises(AudioDownloadCancelled):
                download_audio_data(self.stream, lambda *args: True, cancel_event=cancelled)
            opening.assert_not_called()

    def test_prefetch_is_consumed_once(self):
        track = Track("BV1234567890", "Track", "Artist", "03:00", "", "")
        job = AudioPrefetch(track, {})
        def download(stream, receive, total, cancelled):
            total(3)
            receive(b"abc", 3, 3)
        with patch("echoplay.services.prefetch.resolve_audio_stream", return_value=self.stream):
            with patch("echoplay.services.prefetch.download_audio_data", side_effect=download):
                job.run()
        self.assertEqual(job.take(track)[1], bytearray(b"abc"))
        self.assertIsNone(job.take(track))

    def test_prefetch_limit(self):
        track = Track("BV1234567890", "Track", "Artist", "03:00", "", "")
        job = AudioPrefetch(track, {})
        def download(stream, receive, total, cancelled):
            total(MAX_PREFETCH_BYTES + 1)
            self.fail("oversized prefetch must stop")
        with patch("echoplay.services.prefetch.resolve_audio_stream", return_value=self.stream):
            with patch("echoplay.services.prefetch.download_audio_data", side_effect=download):
                job.run()
        self.assertIsNone(job.take(track))
        job.cancel()
        self.assertTrue(job.cancel_event.is_set())
