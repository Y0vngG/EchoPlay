"""Shared WBI signing for search and playback requests."""

from urllib.parse import urlencode
import hashlib
import time

WBI_MIXIN_KEY_ENC_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)


def mixin_key(nav_data: dict) -> str:
    image_info = nav_data.get("wbi_img") or {}
    img_url = image_info.get("img_url", "")
    sub_url = image_info.get("sub_url", "")
    if not img_url or not sub_url:
        raise RuntimeError("B 站未返回 WBI 签名信息。")
    img_key = img_url.rsplit("/", 1)[-1].split(".", 1)[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".", 1)[0]
    raw_key = img_key + sub_key
    try:
        return "".join(raw_key[index] for index in WBI_MIXIN_KEY_ENC_TAB)[:32]
    except IndexError as exc:
        raise RuntimeError("B 站 WBI 签名信息格式异常。") from exc


def sign_query(params: dict, mixin_key: str, *, clean_values: bool = True) -> str:
    params = {**params, "wts": int(time.time())}
    cleaned = {
        key: "".join(char for char in str(value) if char not in "!'()*")
        for key, value in sorted(params.items())
    }
    query = urlencode(cleaned)
    signed = cleaned if clean_values else dict(params)
    signed["w_rid"] = hashlib.md5(f"{query}{mixin_key}".encode("utf-8")).hexdigest()
    return urlencode(signed)

