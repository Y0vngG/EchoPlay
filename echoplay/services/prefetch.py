"""A single, bounded next-track cache; background failures never interrupt playback."""
import logging
import threading

from echoplay.services.playback import (
    AudioDownloadCancelled,
    download_audio_data,
    resolve_audio_stream,
)
from echoplay.services.stream_quality import DEFAULT_STREAM_QUALITY

MAX_PREFETCH_BYTES = 32 * 1024 * 1024


class AudioPrefetch:
    def __init__(self, track, cookies, quality=DEFAULT_STREAM_QUALITY):
        self.track = track
        self.cookies = dict(cookies)
        self.quality = quality
        self.cancel_event = threading.Event()
        self._lock = threading.Lock()
        self._result = None

    def cancel(self):
        self.cancel_event.set()
        with self._lock:
            self._result = None

    def peek(self):
        with self._lock:
            return None if self.cancel_event.is_set() else self._result

    def take(self, track):
        with self._lock:
            if self.cancel_event.is_set() or track.playback_id != self.track.playback_id:
                return None
            result, self._result = self._result, None
            return result

    def run(self):
        if self.cancel_event.is_set():
            return
        data = bytearray()
        try:
            stream = resolve_audio_stream(self.track, self.cookies, self.quality)
            def total_size(total):
                if total > MAX_PREFETCH_BYTES:
                    raise AudioDownloadCancelled("Next track exceeds cache limit")
            def receive(chunk, received, total):
                if self.cancel_event.is_set() or received > MAX_PREFETCH_BYTES:
                    return False
                data.extend(chunk)
                return True
            download_audio_data(stream, receive, total_size, self.cancel_event)
            with self._lock:
                if not self.cancel_event.is_set():
                    self._result = (stream, data)
        except AudioDownloadCancelled:
            pass
        except Exception:
            logging.getLogger("echoplay.playback").warning(
                "Next-track prefetch failed; normal playback will retry", exc_info=True
            )
        finally:
            self.cookies.clear()
