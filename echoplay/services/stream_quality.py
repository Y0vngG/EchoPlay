"""Real stream specifications and membership validation."""
import time

STREAM_QUALITIES = ("360P", "1080P", "HiRes")
DEFAULT_STREAM_QUALITY = "1080P"
VIDEO_QUALITY_IDS = {"360P": 16, "1080P": 80}


def has_active_vip(data):
    vip = data.get("vip") or {}
    try:
        status = int(data.get("vipStatus", vip.get("status", 0)))
        kind = int(data.get("vipType", vip.get("type", 0)))
        expiry = int(data.get("vipDueDate", vip.get("due_date", 0)) or 0)
    except (TypeError, ValueError):
        return False
    return status == 1 and kind > 0 and (not expiry or expiry > time.time() * 1000)


def select_real_stream(data, quality, headers):
    # Import locally to keep membership parsing independent of Qt.
    from echoplay.services.playback import AudioStream

    if data.get("is_preview"):
        raise RuntimeError("当前账号只能获取试看流，无法播放所选品质的完整内容。")
    if quality == "1080P":
        dash = data.get("dash") or {}
        candidates = list(dash.get("audio") or [])
        dolby = (dash.get("dolby") or {}).get("audio") or []
        candidates.extend(dolby if isinstance(dolby, list) else [dolby])
        candidates = [audio for audio in candidates
                      if audio.get("id") != 30251
                      and not str(audio.get("codecs") or "").lower().startswith(("flac", "alac"))
                      and (audio.get("baseUrl") or audio.get("base_url"))]
        if not candidates:
            raise RuntimeError("该视频没有当前账号可用的非 HiRes 音轨，请选择其他品质。")
        rates = {30216: 64000, 30232: 132000, 30280: 192000, 30250: 384000}
        audio = max(candidates, key=lambda item: (
            item.get("id") == 30250,
            int(item.get("bandwidth") or rates.get(item.get("id"), 0)),
        ))
        bandwidth = int(audio.get("bandwidth") or rates.get(audio.get("id"), 192000))
        name = "杜比全景声" if audio.get("id") == 30250 else f"{bandwidth // 1000} kb/s"
        format_hint = "webm" if "webm" in str(audio.get("mimeType") or "").lower() else "m4a"
        return AudioStream(audio.get("baseUrl") or audio.get("base_url"), headers,
                           f"{name} · 最高非 HiRes 音轨", format_hint,
                           tuple(audio.get("backupUrl") or audio.get("backup_url") or ()), bandwidth)
    if quality == "HiRes":
        dash = data.get("dash") or {}
        candidates = [(dash.get("flac") or {}).get("audio")]
        candidates.extend(dash.get("audio") or [])
        candidates = [item for item in candidates if item and item.get("id") == 30251
                      and (item.get("baseUrl") or item.get("base_url"))]
        if not candidates:
            return select_real_stream(data, "1080P", headers)
        audio = max(candidates, key=lambda item: int(item.get("bandwidth") or 1500000))
        url = audio.get("baseUrl") or audio.get("base_url")
        return AudioStream(url, headers, "HiRes · 无损音轨", "m4a",
                           tuple(audio.get("backupUrl") or audio.get("backup_url") or ()),
                           int(audio.get("bandwidth") or 1500000))
    expected = VIDEO_QUALITY_IDS[quality]
    if data.get("quality") != expected:
        raise RuntimeError(f"当前视频或账号无法获取真实 {quality} 流，请选择其他品质或检查登录权限。")
    parts = data.get("durl") or []
    if len(parts) != 1 or not parts[0].get("url"):
        raise RuntimeError(f"未获取到可直接播放的 {quality} 完整音视频流。")
    if not str(data.get("format") or "").startswith("mp4"):
        raise RuntimeError(f"接口未提供所选 {quality} 的 MP4 音视频流。")
    part = parts[0]
    duration_ms = int(part.get("length") or data.get("timelength") or 0)
    size = int(part.get("size") or 0)
    bandwidth = max(1, size * 8000 // duration_ms) if size and duration_ms else 2000000
    return AudioStream(part["url"], headers, f"{quality} · 原始音视频流", "mp4",
                       tuple(part.get("backup_url") or ()), bandwidth)
