from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
import html
import hashlib
import threading
from collections import OrderedDict
import json
import re
import time

from echoplay.models import Track
from echoplay.services.stream_quality import has_active_vip
from echoplay.services.wbi import mixin_key, sign_query


WBI_SEARCH_URL = "https://api.bilibili.com/x/web-interface/wbi/search/type"
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
HOME_URL = "https://www.bilibili.com/"

WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://search.bilibili.com/",
    "Origin": "https://search.bilibili.com",
}


class BilibiliError(RuntimeError):
    pass


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = Request(url, headers={"User-Agent": "EchoPlay/0.1", **(headers or {})})
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise BilibiliError(f"请求 B 站失败：{exc}") from exc
    if payload.get("code") != 0:
        raise BilibiliError(payload.get("message") or f"B 站返回错误码 {payload.get('code')}")
    return payload.get("data") or {}


def fetch_account_profile(cookies: dict[str, str]) -> tuple[str, str, bool]:
    headers = dict(WEB_HEADERS)
    headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
    data = _get_json(NAV_URL, headers)
    if not data.get("isLogin"):
        raise BilibiliError("B 站登录会话已失效，请重新登录。")
    nickname = str(data.get("uname") or "").strip()
    if not nickname:
        raise BilibiliError("B 站未返回当前账号昵称。")
    avatar_url = str(data.get("face") or "").strip()
    return nickname, avatar_url, has_active_vip(data)


def fetch_account_avatar(url: str) -> bytes:
    if not url:
        raise BilibiliError("B 站未返回当前账号头像地址。")
    if url.startswith("//"):
        url = f"https:{url}"
    if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
        raise BilibiliError("B 站返回的账号头像地址无效。")
    request = Request(
        url,
        headers={
            "User-Agent": WEB_HEADERS["User-Agent"],
            "Referer": HOME_URL,
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            image_data = response.read(4 * 1024 * 1024 + 1)
    except Exception as exc:
        raise BilibiliError(f"下载 B 站账号头像失败：{exc}") from exc
    if len(image_data) > 4 * 1024 * 1024:
        raise BilibiliError("B 站账号头像文件超过 4 MB。")
    if not image_data:
        raise BilibiliError("B 站返回了空账号头像。")
    return image_data


def fetch_video_from_url(video_url: str) -> Track:
    parsed_url = urlparse(video_url.strip())
    hostname = (parsed_url.hostname or "").lower()
    if (
        parsed_url.scheme not in {"http", "https"}
        or not (hostname == "bilibili.com" or hostname.endswith(".bilibili.com"))
    ):
        raise BilibiliError("请输入有效的 B 站视频链接。")

    match = re.fullmatch(r"/video/(BV[0-9A-Za-z]{10}|av[0-9]+)/?", parsed_url.path)
    if not match:
        raise BilibiliError("无法识别该视频链接，请使用 B 站视频播放页链接。")

    video_id = match.group(1)
    query_key = "bvid" if video_id.startswith("BV") else "aid"
    query_value = video_id[2:] if query_key == "aid" else video_id
    query = urlencode({query_key: query_value})
    data = _get_json(f"https://api.bilibili.com/x/web-interface/view?{query}")
    bvid = str(data.get("bvid") or "")
    title = str(data.get("title") or "").strip()
    if not bvid or not title:
        raise BilibiliError("B 站未返回完整的视频信息，无法加入曲库。")

    cover_url = str(data.get("pic") or "")
    if cover_url.startswith("//"):
        cover_url = f"https:{cover_url}"
    duration_seconds = int(data.get("duration") or 0)
    hours, remainder = divmod(duration_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    duration = (
        f"{hours}:{minutes:02d}:{seconds:02d}"
        if hours
        else f"{minutes}:{seconds:02d}"
    )
    owner = data.get("owner") or {}
    return Track(
        bvid=bvid,
        title=title,
        author=str(owner.get("name") or ""),
        duration=duration,
        cover_url=cover_url,
        page_url=f"https://www.bilibili.com/video/{bvid}",
        video_title=title,
    )


def fetch_video_pages(track: Track) -> list[Track]:
    data = _get_json(
        f"https://api.bilibili.com/x/web-interface/view?{urlencode({'bvid': track.bvid})}"
    )
    pages = data.get("pages") or []
    if not pages:
        raise BilibiliError("B 站未返回该视频的分 P 信息。")

    video_title = str(data.get("title") or track.title).strip()
    owner = data.get("owner") or {}
    author = str(owner.get("name") or track.author)
    page_tracks: list[Track] = []
    for page_data in pages:
        page_number = int(page_data.get("page") or 0)
        part_title = str(page_data.get("part") or "").strip()
        if page_number < 1:
            continue
        title = f"{video_title} - P{page_number}"
        if part_title and part_title != video_title:
            title = f"{title} {part_title}"
        duration_seconds = int(page_data.get("duration") or 0)
        hours, remainder = divmod(duration_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration = (
            f"{hours}:{minutes:02d}:{seconds:02d}"
            if hours
            else f"{minutes}:{seconds:02d}"
        )
        page_tracks.append(
            Track(
                bvid=track.bvid,
                title=title,
                author=author,
                duration=duration,
                cover_url=track.cover_url,
                page_url=f"https://www.bilibili.com/video/{track.bvid}/?p={page_number}",
                page_number=page_number,
                part_title=part_title,
                video_title=video_title,
            )
        )
    if not page_tracks:
        raise BilibiliError("B 站返回的分 P 信息无效。")
    return page_tracks


class _SearchSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.opener = None
        self.key = None
        self.expires_at = 0


_SEARCH_SESSIONS = OrderedDict()
_SEARCH_SESSIONS_LOCK = threading.Lock()
_SEARCH_SESSION_TTL = 600
_COVER_CACHE = OrderedDict()
_COVER_CACHE_LOCK = threading.Lock()
_COVER_CACHE_BYTES = 16 * 1024 * 1024


def _search_session(cookies):
    identity = hashlib.sha256(json.dumps(cookies or {}, sort_keys=True).encode()).digest()
    with _SEARCH_SESSIONS_LOCK:
        session = _SEARCH_SESSIONS.pop(identity, None) or _SearchSession()
        _SEARCH_SESSIONS[identity] = session
        while len(_SEARCH_SESSIONS) > 4:
            _SEARCH_SESSIONS.popitem(last=False)
    return session


def cached_cover(url):
    with _COVER_CACHE_LOCK:
        data = _COVER_CACHE.pop(url, None)
        if data is not None:
            _COVER_CACHE[url] = data
        return data


def _cache_cover(url, data):
    with _COVER_CACHE_LOCK:
        _COVER_CACHE.pop(url, None)
        _COVER_CACHE[url] = data
        total = sum(len(value) for value in _COVER_CACHE.values())
        while total > _COVER_CACHE_BYTES or len(_COVER_CACHE) > 128:
            _, removed = _COVER_CACHE.popitem(last=False)
            total -= len(removed)


def search_videos(
    keyword: str,
    limit: int = 30,
    cookies: dict[str, str] | None = None,
    page: int = 1,
) -> list[Track]:
    if page < 1:
        raise ValueError("搜索页码必须大于零。")
    session = _search_session(cookies)

    try:
        request_headers = dict(WEB_HEADERS)
        if cookies:
            request_headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
        with session.lock:
            if session.opener is None or time.monotonic() >= session.expires_at:
                session.opener = build_opener(HTTPCookieProcessor(CookieJar()))
                with session.opener.open(Request(HOME_URL, headers=request_headers), timeout=15) as response:
                    response.read(4096)
                session.key = mixin_key(_open_nav_data(session.opener, request_headers))
                session.expires_at = time.monotonic() + _SEARCH_SESSION_TTL
            opener, key = session.opener, session.key
            params = {
                "search_type": "video",
                "keyword": keyword,
                "page": page,
                "page_size": limit,
                "order": "totalrank",
                "web_location": 1550101,
                "wts": int(time.time()),
            }
            query = sign_query(params, key, clean_values=False)
            data = _open_api_json(opener, f"{WBI_SEARCH_URL}?{query}", request_headers)
    except HTTPError as exc:
        session.expires_at = 0
        if exc.code == 412:
            raise BilibiliError(
                "B 站仍拦截了访客搜索请求（HTTP 412）。请稍后重试；此错误不代表需要登录。"
            ) from exc
        raise BilibiliError(f"B 站搜索请求失败：HTTP {exc.code}") from exc
    except BilibiliError:
        session.expires_at = 0
        raise
    except Exception as exc:
        session.expires_at = 0
        raise BilibiliError(f"无法准备 B 站访客搜索请求：{exc}") from exc

    results = data.get("result") or []
    tracks: list[Track] = []
    for item in results:
        bvid = item.get("bvid")
        if not bvid:
            continue
        title = html.unescape(re.sub(r"</?em[^>]*>", "", str(item.get("title", ""))))
        duration = str(item.get("duration", ""))
        cover = str(item.get("pic", ""))
        if cover.startswith("//"):
            cover = f"https:{cover}"
        tracks.append(Track(
            bvid=bvid,
            title=title,
            author=str(item.get("author", "")),
            duration=duration,
            cover_url=cover,
            page_url=f"https://www.bilibili.com/video/{bvid}",
        ))
    return tracks


def _open_api_json(opener, url: str, headers: dict[str, str] | None = None) -> dict:
    request = Request(url, headers=headers or WEB_HEADERS)
    try:
        with opener.open(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 412:
            raise
        raise BilibiliError(f"B 站接口返回 HTTP {exc.code}") from exc
    except Exception as exc:
        raise BilibiliError(f"请求 B 站搜索接口失败：{exc}") from exc
    if payload.get("code") != 0:
        code = payload.get("code")
        if code == -412:
            raise BilibiliError("B 站拦截了访客搜索请求（-412）。请稍后重试。")
        raise BilibiliError(payload.get("message") or f"B 站返回错误码 {code}")
    return payload.get("data") or {}


def _open_nav_data(opener, headers: dict[str, str] | None = None) -> dict:
    request = Request(NAV_URL, headers=headers or WEB_HEADERS)
    try:
        with opener.open(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 412:
            raise
        raise BilibiliError(f"获取 B 站搜索签名信息失败：HTTP {exc.code}") from exc
    except Exception as exc:
        raise BilibiliError(f"获取 B 站搜索签名信息失败：{exc}") from exc
    data = payload.get("data") or {}
    if not data.get("wbi_img"):
        raise BilibiliError("B 站未返回 WBI 搜索签名信息。")
    return data


def fetch_cover(track: Track) -> tuple[str, bytes]:
    if not track.cover_url:
        return track.bvid, b""
    existing = cached_cover(track.cover_url)
    if existing is not None:
        return track.bvid, existing
    request = Request(
        track.cover_url,
        headers={"User-Agent": WEB_HEADERS["User-Agent"], "Referer": HOME_URL},
    )
    try:
        with urlopen(request, timeout=10) as response:
            image_data = response.read(4 * 1024 * 1024 + 1)
        if len(image_data) > 4 * 1024 * 1024:
            image_data = b""
        if image_data:
            _cache_cover(track.cover_url, image_data)
        return track.bvid, image_data
    except Exception:
        return track.bvid, b""
