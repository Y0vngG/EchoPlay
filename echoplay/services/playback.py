"""Resolve Bilibili media streams and buffer completed or prefetched audio."""

from __future__ import annotations

from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
import http.client
import json
import logging
import re
import threading
import time

from PySide6.QtCore import QIODevice

from echoplay.models import Track
from echoplay.services.bilibili import WEB_HEADERS
from echoplay.services.stream_quality import (
    DEFAULT_STREAM_QUALITY,
    STREAM_QUALITIES,
    VIDEO_QUALITY_IDS,
    has_active_vip,
    select_real_stream,
)
from echoplay.services.wbi import mixin_key as _wbi_mixin_key, sign_query as _sign_wbi


NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
VIEW_URL = "https://api.bilibili.com/x/web-interface/view"
PLAY_URL = "https://api.bilibili.com/x/player/wbi/playurl"
REQUEST_TIMEOUT_SECONDS = 8
AUDIO_READ_TIMEOUT_SECONDS = 8
AUDIO_DOWNLOAD_MAX_RETRIES = 4
AUDIO_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class AudioStream:
    url: str
    headers: dict[str, str]
    description: str
    format_hint: str
    backup_urls: tuple[str, ...] = ()
    bandwidth: int = 192000

    @property
    def startup_bytes(self) -> int:
        return max(4096, int(self.bandwidth * 1.75 / 8))


class AudioDownloadCancelled(RuntimeError):
    pass


class StreamingAudioDevice(QIODevice):
    """Thread-safe seekable device that becomes readable as audio chunks arrive."""

    def __init__(self, parent=None, *, initial_data: bytearray | None = None):
        super().__init__(parent)
        self._condition = threading.Condition()
        self._data = initial_data if initial_data is not None else bytearray()
        self._position = 0
        self._expected_size = 0
        self._finished = False
        self._cancelled = False
        self._total_received = len(self._data)
        self.player_released = True
        self.producer_done = False

    @property
    def total_received(self) -> int:
        with self._condition:
            return self._total_received

    def isSequential(self) -> bool:
        return False

    def bytesAvailable(self) -> int:
        with self._condition:
            buffered_bytes = max(0, len(self._data) - self._position)
        return buffered_bytes + super().bytesAvailable()

    def atEnd(self) -> bool:
        with self._condition:
            return self._finished and self._position >= len(self._data)

    def size(self) -> int:
        with self._condition:
            if self._finished or not self._expected_size:
                return len(self._data)
            return self._expected_size

    def pos(self) -> int:
        with self._condition:
            return self._position

    def seek(self, position: int) -> bool:
        with self._condition:
            maximum = (
                self._expected_size
                if self._expected_size and not self._finished
                else len(self._data)
            )
            if self._cancelled or position < 0 or position > maximum:
                return False
            self._position = position
        return super().seek(position)

    def set_expected_size(self, total_bytes: int):
        if total_bytes <= 0:
            return
        with self._condition:
            self._expected_size = total_bytes

    def append(self, chunk: bytes) -> bool:
        with self._condition:
            if self._cancelled or self._finished:
                return False
            self._data.extend(chunk)
            self._total_received += len(chunk)
            self._condition.notify_all()
        self.readyRead.emit()
        return True

    def finish(self):
        with self._condition:
            self._finished = True
            self._expected_size = len(self._data)
            self._condition.notify_all()
        self.readyRead.emit()

    def cancel(self):
        with self._condition:
            self._cancelled = True
            self._finished = True
            self._condition.notify_all()
        self.readyRead.emit()

    def clear_cached_data(self):
        with self._condition:
            self._data.clear()
            self._position = 0
            self._expected_size = 0
            self._total_received = 0
            self._condition.notify_all()

    def readData(self, max_size: int) -> bytes:
        with self._condition:
            while (
                self._position >= len(self._data)
                and not self._finished
                and not self._cancelled
            ):
                self._condition.wait()
            if self._cancelled or self._position >= len(self._data):
                return b""
            end = min(self._position + max_size, len(self._data))
            chunk = bytes(self._data[self._position:end])
            self._position = end
            return chunk

    def writeData(self, data: bytes) -> int:
        return -1


def download_audio_data(
    stream: AudioStream,
    chunk_callback: Callable[[bytes, int, int], bool],
    total_callback: Callable[[int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> int:
    """Stream audio and resume from the last received byte after transient errors."""
    received_bytes = 0
    total_bytes = 0
    retries = 0
    urls = tuple(dict.fromkeys((stream.url, *stream.backup_urls)))
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise AudioDownloadCancelled("Audio download cancelled")
        headers = dict(stream.headers)
        if received_bytes:
            headers["Range"] = f"bytes={received_bytes}-"
        request = Request(urls[retries % len(urls)], headers=headers)
        try:
            with urlopen(request, timeout=AUDIO_READ_TIMEOUT_SECONDS) as response:
                status = response.getcode()
                content_range = response.headers.get("Content-Range")
                if received_bytes:
                    if status != 206 or not content_range:
                        raise RuntimeError(
                            "B 站音轨服务器不支持断点续传，无法安全恢复下载。"
                        )
                    byte_range = _parse_content_range(content_range)
                    if (
                        byte_range is None
                        or byte_range[0] != received_bytes
                    ):
                        raise RuntimeError(
                            "B 站音轨续传位置与已接收数据不一致，已停止下载以避免音频损坏。"
                        )
                    response_total = (
                        byte_range[2]
                        if byte_range[2] is not None
                        else received_bytes
                        + int(response.headers.get("Content-Length") or 0)
                    )
                    if total_bytes and response_total and response_total != total_bytes:
                        raise RuntimeError("B 站音轨续传时音频总长度发生变化。")
                    total_bytes = total_bytes or response_total
                elif status == 206 and content_range:
                    byte_range = _parse_content_range(content_range)
                    if byte_range is None or byte_range[0] != 0:
                        raise RuntimeError("B 站返回的音轨起始位置无效。")
                    total_bytes = (
                        byte_range[2]
                        if byte_range[2] is not None
                        else int(response.headers.get("Content-Length") or 0)
                    )
                else:
                    total_bytes = int(response.headers.get("Content-Length") or 0)
                if total_callback:
                    total_callback(total_bytes)
                while chunk := response.read1(AUDIO_CHUNK_SIZE):
                    if cancel_event is not None and cancel_event.is_set():
                        raise AudioDownloadCancelled("Audio download cancelled")
                    received_bytes += len(chunk)
                    if not chunk_callback(chunk, received_bytes, total_bytes):
                        raise AudioDownloadCancelled("音频下载已取消。")
                if total_bytes and received_bytes < total_bytes:
                    raise http.client.IncompleteRead(
                        b"", expected=total_bytes - received_bytes
                    )
                break
        except AudioDownloadCancelled:
            raise
        except Exception as exc:
            if isinstance(exc, HTTPError):
                exc.close()
            if total_bytes and received_bytes >= total_bytes:
                break
            if not _is_retryable_audio_error(exc) or retries >= AUDIO_DOWNLOAD_MAX_RETRIES:
                raise RuntimeError(
                    f"读取 B 站音轨失败（已接收 {received_bytes} 字节，"
                    f"重试 {retries} 次）：{exc}"
                ) from exc
            retries += 1
            delay = 0 if len(urls) > 1 else min(0.25 * 2 ** (retries - 1), 2)
            logging.getLogger("echoplay.playback").warning(
                "Audio stream interrupted after %d bytes; retry %d/%d in %s seconds: %s",
                received_bytes,
                retries,
                AUDIO_DOWNLOAD_MAX_RETRIES,
                delay,
                exc,
            )
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    raise AudioDownloadCancelled("Audio download cancelled")
            else:
                time.sleep(delay)
    if not received_bytes:
        raise RuntimeError("B 站返回了空音轨。")
    return received_bytes


def _parse_content_range(value: str) -> tuple[int, int, int | None] | None:
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", value.strip())
    if match is None:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    total = int(match.group(3)) if match.group(3) != "*" else None
    if end < start or (total is not None and end >= total):
        return None
    return start, end, total


def _is_retryable_audio_error(error: Exception) -> bool:
    if isinstance(error, HTTPError):
        return error.code in (408, 429, 500, 502, 503, 504)
    if isinstance(error, URLError):
        return isinstance(error.reason, OSError)
    return isinstance(error, (OSError, http.client.HTTPException))


def _get_json(opener, url: str, *, referer: str = "https://www.bilibili.com/",
              cookies: dict[str, str] | None = None) -> dict:
    headers = {**WEB_HEADERS, "Referer": referer}
    if cookies:
        headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    request = Request(url, headers=headers)
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read(2 * 1024 * 1024 + 1).decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"请求 B 站接口失败：{exc}") from exc
    if payload.get("code") != 0:
        message = payload.get("message") or f"错误码 {payload.get('code')}"
        raise RuntimeError(f"B 站接口返回：{message}")
    return payload.get("data") or {}


def resolve_audio_stream(
    track: Track, cookies: dict[str, str] | None = None,
    quality: str = DEFAULT_STREAM_QUALITY,
) -> AudioStream:
    """Resolve real streams according to the selected quality policy."""
    if quality not in STREAM_QUALITIES:
        raise ValueError("Unknown stream quality")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    referer = track.page_url or "https://www.bilibili.com/"

    # The view endpoint supplies the CID needed by the DASH play URL endpoint.
    cookies = cookies or {}
    view = _get_json(opener, f"{VIEW_URL}?{urlencode({'bvid': track.bvid})}", referer=referer,
                     cookies=cookies)
    pages = view.get("pages") or []
    page_values = parse_qs(urlparse(track.page_url).query).get("p", [])
    try:
        page_number = int(page_values[0]) if page_values else 1
    except ValueError as exc:
        raise RuntimeError("视频分 P 参数无效。") from exc
    if page_number < 1 or page_number > len(pages):
        raise RuntimeError("该视频分 P 不存在。")
    page = pages[page_number - 1]
    if not page.get("cid"):
        raise RuntimeError("B 站没有返回该视频的分 P 信息。")
    cid = page["cid"]

    nav_data = _get_json(opener, NAV_URL, referer=referer, cookies=cookies)
    if quality == "HiRes" and not has_active_vip(nav_data):
        quality = "1080P"
    params = {
        "bvid": track.bvid,
        "cid": cid,
        "qn": VIDEO_QUALITY_IDS.get(quality, 80),
        "fnval": 1 if quality == "360P" else 4048,
        "fnver": 0,
        "fourk": 1,
    }
    signed_query = _sign_wbi(params, _wbi_mixin_key(nav_data))
    play_data = _get_json(opener, f"{PLAY_URL}?{signed_query}", referer=referer, cookies=cookies)
    headers = {"User-Agent": WEB_HEADERS["User-Agent"], "Referer": referer}
    if cookies:
        headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    return select_real_stream(play_data, quality, headers)
