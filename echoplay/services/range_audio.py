"""Seekable, sparse audio cache with prioritized HTTP byte-range requests."""
from urllib.error import HTTPError
from urllib.request import Request
import threading

from PySide6.QtCore import QIODevice

from echoplay.services.http_transport import PersistentRangeClient
from echoplay.services.playback import (
    AudioDownloadCancelled,
    AUDIO_READ_TIMEOUT_SECONDS,
    AUDIO_DOWNLOAD_MAX_RETRIES,
    _parse_content_range,
    _is_retryable_audio_error,
)


BLOCK_SIZE = 256 * 1024


class RangeAudioDevice(QIODevice):
    def __init__(self, stream, parent=None):
        super().__init__(parent)
        self.stream = stream
        self._block_size = max(BLOCK_SIZE, stream.startup_bytes)
        self._condition = threading.Condition()
        self._cancel = threading.Event()
        self._blocks = {}
        self._size = 0
        self._position = 0
        self._priority = 0
        self._finished = False
        self._received = 0
        self.player_released = True
        self.producer_done = False
        self._transport = PersistentRangeClient()

    @property
    def total_received(self):
        with self._condition:
            return self._received

    def isSequential(self):
        return False

    def size(self):
        with self._condition:
            return self._size

    def pos(self):
        with self._condition:
            return self._position

    def _available(self):
        block, offset = divmod(self._position, self._block_size)
        return max(0, len(self._blocks.get(block, b"")) - offset)

    def bytesAvailable(self):
        with self._condition:
            return self._available() + super().bytesAvailable()

    def atEnd(self):
        with self._condition:
            return bool(self._size and self._position >= self._size) or self._cancel.is_set()

    def seek(self, position):
        with self._condition:
            if self._cancel.is_set() or position < 0 or position > self._size:
                return False
            self._position = position
            if not self._available():
                self._priority = position // self._block_size
            self._condition.notify_all()
        return super().seek(position)

    def readData(self, max_size):
        if max_size <= 0:
            return b""
        with self._condition:
            while not self._cancel.is_set():
                if self._size and self._position >= self._size:
                    return b""
                block, offset = divmod(self._position, self._block_size)
                # Decoder probes and cached reads must never wait for another
                # full startup budget. Return data as soon as it is available.
                required = 1
                if self._size:
                    required = min(required, self._size - self._position)
                available = self._available()
                if available >= required or (self._finished and available):
                    amount = min(max_size, available)
                    result = bytes(self._blocks[block][offset:offset + amount])
                    self._position += amount
                    return result
                if self._finished:
                    return b""
                self._priority = block
                self._condition.notify_all()
                self._condition.wait()
        return b""

    def writeData(self, data):
        return -1

    def cancel(self):
        self._cancel.set()
        self.finish()

    def finish(self):
        with self._condition:
            self._finished = True
            self._condition.notify_all()
        self.readyRead.emit()

    def clear_cached_data(self):
        with self._condition:
            self._blocks.clear()
            self._received = 0

    def download(self, ready, progress):
        started = False
        try:
            while not self._cancel.is_set():
                with self._condition:
                    block = self._priority
                    if block is None or self._complete(block):
                        block = next((i for i in range((self._size + self._block_size - 1) // self._block_size)
                                      if not self._complete(i)), None) if self._size else 0
                    self._priority = None
                    if block is None:
                        return self._received
                for received, total in self._fetch_block(block):
                    progress(received, total)
                    if not started and (len(self._blocks.get(0, b"")) >= self.stream.startup_bytes
                                        or received == total):
                        started = True
                        ready()
            raise AudioDownloadCancelled("Audio download cancelled")
        finally:
            self._transport.close()
            self.finish()

    def _complete(self, block):
        expected = min(self._block_size, self._size - block * self._block_size)
        return self._size > 0 and len(self._blocks.get(block, b"")) >= expected

    def _fetch_block(self, block):
        urls = tuple(dict.fromkeys((self.stream.url, *self.stream.backup_urls)))
        for attempt in range(AUDIO_DOWNLOAD_MAX_RETRIES + 1):
            if self._cancel.is_set():
                raise AudioDownloadCancelled("Audio download cancelled")
            with self._condition:
                data = self._blocks.setdefault(block, bytearray())
                start = block * self._block_size + len(data)
            end = (block + 1) * self._block_size - 1
            headers = {**self.stream.headers, "Range": f"bytes={start}-{end}"}
            try:
                with self._transport.open(Request(urls[attempt % len(urls)], headers=headers),
                             timeout=AUDIO_READ_TIMEOUT_SECONDS) as response:
                    byte_range = _parse_content_range(response.headers.get("Content-Range", ""))
                    if response.getcode() == 206 and byte_range and byte_range[0] == start:
                        total = byte_range[2]
                    elif response.getcode() == 200 and start == 0:
                        total = int(response.headers.get("Content-Length") or 0)
                    else:
                        raise RuntimeError("Audio server returned an invalid byte range")
                    if not total or (self._size and self._size != total):
                        raise RuntimeError("Audio stream size is missing or changed")
                    with self._condition:
                        self._size = total
                        self._condition.notify_all()
                    remaining = min(end + 1, total) - start
                    close_after_read = response.getcode() == 200 and total > remaining
                    while remaining > 0:
                        if self._cancel.is_set():
                            raise AudioDownloadCancelled("Audio download cancelled")
                        # Stop exactly at the estimated startup budget before
                        # reading more, including low-bitrate audio streams.
                        budget_remaining = self.stream.startup_bytes - len(data)
                        chunk_size = min(16384, remaining)
                        if budget_remaining > 0:
                            chunk_size = min(chunk_size, budget_remaining)
                        chunk = response.read1(chunk_size)
                        if not chunk:
                            raise OSError("Audio range ended prematurely")
                        with self._condition:
                            data.extend(chunk)
                            self._received += len(chunk)
                            remaining -= len(chunk)
                            self._condition.notify_all()
                            received = self._received
                            # Interrupt a background block for a decoder/seek request.
                            preempt = self._priority not in (None, block)
                        self.readyRead.emit()
                        yield received, total
                        if preempt:
                            self._transport.close()
                            return
                    if close_after_read:
                        self._transport.close()
                return
            except AudioDownloadCancelled:
                raise
            except Exception as exc:
                self._transport.close()
                if isinstance(exc, HTTPError):
                    exc.close()
                if not _is_retryable_audio_error(exc) or attempt == AUDIO_DOWNLOAD_MAX_RETRIES:
                    raise
                if self._cancel.wait(min(0.25 * 2 ** attempt, 2)):
                    raise AudioDownloadCancelled("Audio download cancelled")
