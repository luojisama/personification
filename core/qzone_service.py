from __future__ import annotations

import asyncio
import base64
import html
import io
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .config_manager import _restrict_sensitive_file_permissions


_AUTH_STATE_LOCK = threading.Lock()
_AUTH_STATE: dict[str, Any] = {
    "status": "unknown",
    "refreshing": False,
    "last_refresh_at": 0.0,
    "last_success_at": 0.0,
    "last_failure_at": 0.0,
    "last_error": "",
    "cooldown_until": 0.0,
}
_AUTH_REFRESH_CACHE_SECONDS = 300
_AUTH_FAILURE_COOLDOWN_SECONDS = 15 * 60
_COOKIE_FILE_LOCK = threading.Lock()


@dataclass(frozen=True)
class QzoneWriteResult:
    status: str
    message: str
    result_code: str = ""
    remote_id: str = ""
    remote_time: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == "succeeded"

    def __iter__(self):
        # Existing command/tests may still unpack ``(ok, message)``.
        yield self.success
        yield self.message


@dataclass(frozen=True)
class QzoneImageUploadResult:
    richval: str
    pic_bo: str
    mime_type: str
    converted: bool = False


class QzoneImageUploadError(RuntimeError):
    def __init__(self, result_code: str, *, detail: dict[str, Any] | None = None) -> None:
        self.result_code = str(result_code or "image_upload_failed")[:64]
        self.detail = dict(detail or {})
        super().__init__(self.result_code)


def get_qzone_auth_status() -> dict[str, Any]:
    with _AUTH_STATE_LOCK:
        state = dict(_AUTH_STATE)
    state["cooldown_remaining_seconds"] = max(0, int(float(state.get("cooldown_until", 0) or 0) - time.time()))
    return state


def _set_qzone_auth_failure(message: Any, *, auth_failure: bool = False) -> None:
    now = time.time()
    with _AUTH_STATE_LOCK:
        _AUTH_STATE.update({
            "status": "login_required" if auth_failure else "refresh_failed",
            "last_failure_at": now,
            "last_error": str(message or "")[:240],
            "cooldown_until": now + _AUTH_FAILURE_COOLDOWN_SECONDS if auth_failure else 0.0,
        })


def _looks_like_qzone_auth_page(raw_text: Any) -> bool:
    text = str(raw_text or "").lstrip("\ufeff\r\n\t ").lower()
    return (
        text.startswith(("<html", "<!doctype"))
        or "安全验证" in text
        or "验证码" in text
        or "login.qzone.qq.com" in text
        or "ptlogin" in text
    )


def _get_g_tk(p_skey: str) -> int:
    hash_val = 5381
    for char in p_skey:
        hash_val += (hash_val << 5) + ord(char)
    return hash_val & 0x7FFFFFFF


def _get_cookie_from_config(plugin_config: Any) -> str:
    for attr in ("personification_qzone_cookie", "qzone_cookie"):
        value = str(getattr(plugin_config, attr, "") or "").strip().strip('"').strip("'")
        if value:
            return value
    return ""


def _persist_cookie_to_env(cookie: str, logger: Any) -> None:
    cookie_line = f"personification_qzone_cookie={json.dumps(str(cookie or ''), ensure_ascii=False)}\n"
    for env_path in (Path(".env.prod"), Path(".env")):
        if not env_path.exists():
            continue
        try:
            with _COOKIE_FILE_LOCK:
                lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
                new_lines = []
                found = False
                for line in lines:
                    if line.strip().startswith("personification_qzone_cookie="):
                        new_lines.append(cookie_line)
                        found = True
                    else:
                        new_lines.append(line)
                if not found:
                    if new_lines and not new_lines[-1].endswith(("\n", "\r\n")):
                        new_lines[-1] = new_lines[-1] + "\n"
                    new_lines.append(cookie_line)
                temporary = env_path.with_name(f".{env_path.name}.qzone.tmp")
                temporary.write_text("".join(new_lines), encoding="utf-8")
                _restrict_sensitive_file_permissions(temporary)
                temporary.replace(env_path)
                _restrict_sensitive_file_permissions(env_path)
            return
        except Exception as e:
            logger.error(f"拟人插件：保存 Qzone Cookie 到 {env_path} 失败: {e}")


def _parse_qzone_cookie(cookie: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for item in str(cookie or "").split(";"):
        name, separator, value = item.strip().partition("=")
        if not separator or not name or not value:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            continue
        values[name] = value.strip()
    return values


def _normalize_qzone_cookie(cookie: str) -> tuple[str, str, str]:
    values = _parse_qzone_cookie(cookie)
    p_skey = values.get("p_skey", "").strip()
    raw_uin = values.get("uin") or values.get("p_uin") or ""
    match = re.fullmatch(r"[o0]*(\d+)", raw_uin.strip())
    if not p_skey:
        raise ValueError("missing_p_skey")
    if match is None:
        raise ValueError("missing_uin")
    qq = match.group(1)
    preferred = ("uin", "p_uin", "skey", "p_skey")
    ordered = [name for name in preferred if values.get(name)]
    ordered.extend(name for name in values if name not in ordered and name not in {"qrsig", "pt_login_sig"})
    normalized = "; ".join(f"{name}={values[name]}" for name in ordered) + ";"
    return normalized, qq, p_skey


async def _probe_qzone_cookie(cookie: str, qq: str, p_skey: str) -> tuple[bool, str]:
    ctx = {
        "cookie": cookie,
        "formatted_cookie": _format_cookie_for_qzone(cookie, qq, p_skey),
        "p_skey": p_skey,
        "qq": qq,
        "g_tk": _get_g_tk(p_skey),
    }
    url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
    params = {
        "uin": qq,
        "ftype": "0",
        "sort": "0",
        "pos": "0",
        "num": "1",
        "replynum": "0",
        "g_tk": str(ctx["g_tk"]),
        "callback": "_Callback",
        "code_version": "1",
        "format": "jsonp",
    }
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
            response = await client.get(url, params=params, headers=_qzone_headers(ctx, referer_uin=qq))
    except Exception:
        return False, "probe_failed"
    if response.status_code != 200 or _looks_like_qzone_auth_page(response.text):
        return False, "auth_blocked"
    payload = _parse_qzone_jsonp(response.text)
    if not payload:
        return False, "probe_failed"
    for key in ("code", "ret", "subcode"):
        if key in payload:
            try:
                if int(payload.get(key) or 0) != 0:
                    return False, "auth_blocked"
            except Exception:
                return False, "probe_failed"
    return True, "ok"


async def install_qzone_cookie(
    *,
    cookie: str,
    expected_bot_id: str,
    plugin_config: Any,
    logger: Any,
    source: str,
    probe: Callable[[str, str, str], Awaitable[tuple[bool, str]]] | None = None,
) -> tuple[bool, str]:
    try:
        normalized, qq, p_skey = _normalize_qzone_cookie(cookie)
    except ValueError as exc:
        return False, str(exc)
    if qq != str(expected_bot_id or "").strip():
        return False, "account_mismatch"
    probe_cookie = probe or _probe_qzone_cookie
    ok, reason = await probe_cookie(normalized, qq, p_skey)
    if not ok:
        _set_qzone_auth_failure(reason, auth_failure=reason == "auth_blocked")
        return False, reason
    plugin_config.personification_qzone_cookie = normalized
    _persist_cookie_to_env(normalized, logger)
    now = time.time()
    with _AUTH_STATE_LOCK:
        _AUTH_STATE.update({
            "status": "healthy",
            "last_success_at": now,
            "last_error": "",
            "cooldown_until": 0.0,
            "source": str(source or "unknown")[:32],
        })
    return True, "ok"


_IMAGE_B64_RE = re.compile(r"\[IMAGE_B64\]([A-Za-z0-9+/=\r\n]+)\[/IMAGE_B64\]")
_IOS_QQ_UA = "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Mobile/15E148 QQ/8.9.28.635"


def _extract_image_b64_markers(text: str) -> tuple[str, list[str]]:
    payloads: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        payload = re.sub(r"\s+", "", match.group(1) or "")
        if payload:
            payloads.append(payload)
        return ""

    cleaned = _IMAGE_B64_RE.sub(_replace, str(text or "")).strip()
    return cleaned, payloads


def _decode_image_b64(payload: str) -> bytes:
    text = str(payload or "").strip()
    if "," in text and text.lower().startswith("data:image/"):
        text = text.split(",", 1)[1]
    text = re.sub(r"\s+", "", text)
    return base64.b64decode(text, validate=True)


def _parse_qzone_jsonp(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _format_cookie_for_qzone(cookie: str, qq: str, p_skey: str) -> str:
    formatted_cookie = f"uin=o{qq}; p_skey={p_skey};"
    if "skey=" in cookie:
        skey_match = re.search(r"skey=([^; ]+)", cookie)
        if skey_match:
            formatted_cookie += f" skey={skey_match.group(1)};"
    return formatted_cookie


def _resolve_qzone_context(plugin_config: Any, bot_id: str) -> tuple[bool, str, dict[str, Any]]:
    auth = get_qzone_auth_status()
    if auth.get("cooldown_remaining_seconds", 0) > 0:
        return False, "Qzone 认证处于冷却期，请刷新 Cookie 后重试", {}
    cookie = _get_cookie_from_config(plugin_config)
    if not cookie:
        return False, "未配置 Qzone Cookie", {}
    pskey_match = re.search(r"p_skey=([^; ]+)", cookie)
    if not pskey_match:
        return False, "Cookie 缺少 p_skey 字段", {}
    p_skey = pskey_match.group(1)
    uin_match = re.search(r"uin=[o0]*(\d+)", cookie)
    qq = uin_match.group(1) if uin_match else str(bot_id)
    expected_bot_id = str(bot_id or "").strip()
    if expected_bot_id and qq != expected_bot_id:
        return False, "Qzone Cookie 与目标 Bot 不匹配", {}
    formatted_cookie = _format_cookie_for_qzone(cookie, qq, p_skey)
    return True, "", {
        "cookie": cookie,
        "formatted_cookie": formatted_cookie,
        "p_skey": p_skey,
        "qq": qq,
        "g_tk": _get_g_tk(p_skey),
    }


def _qzone_headers(ctx: dict[str, Any], *, referer_uin: str) -> dict[str, str]:
    return {
        "Cookie": str(ctx.get("formatted_cookie", "")),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        ),
        "Referer": f"https://user.qzone.qq.com/{referer_uin}",
        "Origin": "https://user.qzone.qq.com",
    }


def _qzone_mobile_headers(ctx: dict[str, Any], *, referer_uin: str) -> dict[str, str]:
    _ = referer_uin
    return {
        "Cookie": str(ctx.get("cookie", "") or ctx.get("formatted_cookie", "")),
        "User-Agent": _IOS_QQ_UA,
        "Referer": "https://m.qzone.qq.com/",
        "Origin": "https://m.qzone.qq.com",
    }


def _clean_qzone_text(value: Any) -> str:
    if isinstance(value, list):
        raw = "".join(str(item.get("text", "") if isinstance(item, dict) else item) for item in value)
    else:
        raw = str(value or "")
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.IGNORECASE)
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _format_qzone_reply_content(text: str, reply_to_comment: dict[str, Any] | None) -> str:
    cleaned = _clean_qzone_text(text)
    if not isinstance(reply_to_comment, dict):
        return cleaned[:80]
    if cleaned.startswith(("@", "回复 ", "回复　")):
        return cleaned[:80]

    nickname = _clean_qzone_text(
        reply_to_comment.get("nickname")
        or reply_to_comment.get("nick")
        or reply_to_comment.get("name")
        or reply_to_comment.get("user_name")
    )
    user_id = str(
        reply_to_comment.get("user_id")
        or reply_to_comment.get("uin")
        or reply_to_comment.get("useruin")
        or ""
    ).strip()
    if not user_id:
        return cleaned[:80]
    # QZone 协议级 @ 富文本，QQ 客户端会渲染为蓝色可点击链接
    nick_for_at = nickname or user_id
    prefix = f"@{{uin:{user_id},nick:{nick_for_at},who:1}} "
    return (prefix + cleaned)[:80]


def _qzone_comment_reply_target(reply_to_comment: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(reply_to_comment, dict):
        return {}
    raw = reply_to_comment.get("raw") if isinstance(reply_to_comment.get("raw"), dict) else {}
    user_id = str(
        reply_to_comment.get("user_id")
        or reply_to_comment.get("uin")
        or reply_to_comment.get("useruin")
        or raw.get("uin")
        or raw.get("useruin")
        or raw.get("replyuin")
        or ""
    ).strip()
    comment_id = str(
        reply_to_comment.get("comment_id")
        or reply_to_comment.get("commentid")
        or reply_to_comment.get("commentId")
        or reply_to_comment.get("replyid")
        or raw.get("commentid")
        or raw.get("commentId")
        or raw.get("replyid")
        or raw.get("id")
        or raw.get("tid")
        or ""
    ).strip()
    nickname = _clean_qzone_text(
        reply_to_comment.get("nickname")
        or reply_to_comment.get("nick")
        or reply_to_comment.get("name")
        or raw.get("nickname")
        or raw.get("nick")
        or raw.get("name")
    )
    return {"user_id": user_id, "comment_id": comment_id, "nickname": nickname}


def _qzone_feed_reply_identity(feed: dict[str, Any]) -> dict[str, str]:
    raw = feed.get("raw") if isinstance(feed.get("raw"), dict) else {}
    owner = str(feed.get("owner_uin") or raw.get("uin") or raw.get("owner_uin") or "").strip()
    feed_id = str(feed.get("feed_id") or raw.get("tid") or raw.get("id") or raw.get("feed_id") or "").strip()
    topic_id = str(feed.get("topic_id") or raw.get("topicId") or raw.get("topicid") or "").strip()
    appid = str(feed.get("appid") or raw.get("appid") or "311").strip() or "311"
    t1_source = str(feed.get("t1_source") or raw.get("t1_source") or "").strip()
    subdotype = str(feed.get("subdotype") or raw.get("subdotype") or raw.get("t1_subtype") or "0").strip() or "0"
    signin = str(feed.get("signin") or raw.get("signin") or "0").strip() or "0"
    sceneid = str(feed.get("sceneid") or raw.get("sceneid") or "100").strip() or "100"
    if not topic_id and owner and feed_id:
        topic_id = f"{owner}_{feed_id}__1"
    return {
        "owner": owner,
        "feed_id": feed_id,
        "topic_id": topic_id,
        "appid": appid,
        "t1_source": t1_source,
        "subdotype": subdotype,
        "signin": signin,
        "sceneid": sceneid,
    }


def _normalize_qzone_image_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://") or url.startswith("data:image/"):
        return url
    return ""


def _extract_qzone_images(feed: dict[str, Any]) -> list[str]:
    images: list[str] = []
    candidates: list[Any] = []
    for key in ("pic", "pics", "images", "picdata"):
        value = feed.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.append(value)
    for item in candidates:
        if isinstance(item, str):
            url = _normalize_qzone_image_url(item)
            if url:
                images.append(url)
            continue
        if not isinstance(item, dict):
            continue
        for key in (
            "url1",
            "url2",
            "url3",
            "url",
            "raw",
            "origin_url",
            "pic_url",
            "photourl",
            "smallurl",
            "bigurl",
            "image_url",
        ):
            url = _normalize_qzone_image_url(item.get(key))
            if url:
                images.append(url)
                break
    seen: set[str] = set()
    unique: list[str] = []
    for url in images:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


_QZONE_COMMENT_LIST_KEYS = ("commentlist", "comments", "comment_list", "replylist", "replys", "replies")


def _raw_qzone_comment_user_id(item: dict[str, Any]) -> str:
    user_obj = item.get("user") if isinstance(item.get("user"), dict) else {}
    return str(
        item.get("uin")
        or item.get("user_id")
        or item.get("useruin")
        or item.get("user_uin")
        or item.get("commentuin")
        or item.get("comment_uin")
        or item.get("replyuin")
        or item.get("reply_uin")
        or item.get("posterid")
        or item.get("poster_id")
        or item.get("poster_uin")
        or item.get("owner")
        or user_obj.get("uin")
        or user_obj.get("id")
        or user_obj.get("user_id")
        or user_obj.get("useruin")
        or ""
    ).strip()


def _raw_qzone_comment_id(item: dict[str, Any]) -> str:
    return str(
        item.get("tid")
        or item.get("id")
        or item.get("commentid")
        or item.get("comment_id")
        or item.get("commentId")
        or item.get("replyid")
        or item.get("reply_id")
        or item.get("replyId")
        or ""
    ).strip()


def _raw_qzone_reply_to_user_id(item: dict[str, Any]) -> str:
    return str(
        item.get("replyuin")
        or item.get("reply_uin")
        or item.get("replyUin")
        or item.get("touin")
        or item.get("toUin")
        or item.get("targetuin")
        or item.get("targetUin")
        or item.get("sourceUin")
        or ""
    ).strip()


def _iter_qzone_comment_candidates(
    container: Any,
    *,
    parent: dict[str, str] | None = None,
) -> list[tuple[dict[str, Any], dict[str, str]]]:
    candidates: list[tuple[dict[str, Any], dict[str, str]]] = []
    if not isinstance(container, dict):
        return candidates
    for key in _QZONE_COMMENT_LIST_KEYS:
        value = container.get(key)
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, dict):
                    continue
                parent_meta = dict(parent or {})
                candidates.append((item, parent_meta))
                child_parent = {
                    "parent_user_id": _raw_qzone_comment_user_id(item),
                    "parent_comment_id": _raw_qzone_comment_id(item),
                    "parent_nickname": _first_text(
                        item,
                        ("nickname", "nick", "name", "username", "postername", "poster_name"),
                    ),
                }
                for nested_item, nested_parent in _iter_qzone_comment_candidates(
                    item,
                    parent=child_parent,
                ):
                    candidates.append((nested_item, nested_parent))
        elif isinstance(value, dict):
            nested = value.get("items") or value.get("list") or value.get("comments")
            if isinstance(nested, list):
                for item in nested:
                    if not isinstance(item, dict):
                        continue
                    parent_meta = dict(parent or {})
                    candidates.append((item, parent_meta))
                    child_parent = {
                        "parent_user_id": _raw_qzone_comment_user_id(item),
                        "parent_comment_id": _raw_qzone_comment_id(item),
                        "parent_nickname": _first_text(
                            item,
                            ("nickname", "nick", "name", "username", "postername", "poster_name"),
                        ),
                    }
                    for nested_item, nested_parent in _iter_qzone_comment_candidates(
                        item,
                        parent=child_parent,
                    ):
                        candidates.append((nested_item, nested_parent))
            else:
                candidates.append((value, dict(parent or {})))
    return candidates


def _extract_qzone_comments(feed: dict[str, Any]) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for item, parent_meta in _iter_qzone_comment_candidates(feed):
        user_obj = item.get("user") if isinstance(item.get("user"), dict) else {}
        user_id = _raw_qzone_comment_user_id(item)
        content = _first_text(
            item,
            (
                "content",
                "con",
                "text",
                "msg",
                "comment",
                "html",
                "ubbContent",
                "ubb_content",
                "richContent",
                "rich_content",
            ),
        )
        if not user_id or not content:
            continue
        comment_id = _raw_qzone_comment_id(item)
        nickname = _first_text(item, ("nickname", "nick", "name", "username", "postername", "poster_name")) or _clean_qzone_text(
            user_obj.get("nickname") or user_obj.get("name") or user_obj.get("nick") or ""
        )
        reply_to_user_id = _raw_qzone_reply_to_user_id(item)
        if not reply_to_user_id:
            reply_to_user_id = str(parent_meta.get("parent_user_id", "") or "")
        created_at = (
            item.get("created_time")
            or item.get("abstime")
            or item.get("time")
            or item.get("create_time")
            or item.get("createTime")
            or item.get("pubtime")
            or item.get("pub_time")
            or 0
        )
        try:
            created_at_int = int(float(created_at or 0))
        except Exception:
            created_at_int = 0
        comments.append(
            {
                "comment_key": f"{user_id}:{comment_id or created_at_int}:{content[:24]}",
                "comment_id": comment_id,
                "user_id": user_id,
                "nickname": nickname or user_id,
                "content": content,
                "created_at": created_at_int,
                "parent_comment_id": str(parent_meta.get("parent_comment_id", "") or ""),
                "parent_user_id": str(parent_meta.get("parent_user_id", "") or ""),
                "parent_nickname": str(parent_meta.get("parent_nickname", "") or ""),
                "reply_to_user_id": reply_to_user_id,
                "raw": item,
            }
        )
    return comments


def _first_text(feed: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = feed.get(key)
        text = _clean_qzone_text(value)
        if text:
            return text
    return ""


def _normalize_qzone_feed(raw_feed: Any, *, target_uin: str) -> dict[str, Any] | None:
    if not isinstance(raw_feed, dict):
        return None
    owner_uin = str(
        raw_feed.get("uin")
        or raw_feed.get("hostuin")
        or raw_feed.get("host_uin")
        or raw_feed.get("owner_uin")
        or target_uin
    ).strip()
    feed_id = str(
        raw_feed.get("tid")
        or raw_feed.get("id")
        or raw_feed.get("feedid")
        or raw_feed.get("feed_id")
        or raw_feed.get("cellid")
        or raw_feed.get("ugc_key")
        or ""
    ).strip()
    content = _first_text(raw_feed, ("content", "con", "summary", "cell_summary", "msg", "text"))
    images = _extract_qzone_images(raw_feed)
    if not feed_id and not content and not images:
        return None
    created_at = raw_feed.get("created_time") or raw_feed.get("abstime") or raw_feed.get("time") or 0
    try:
        created_at_int = int(float(created_at or 0))
    except Exception:
        created_at_int = 0
    appid = str(raw_feed.get("appid") or raw_feed.get("appidlist") or "311").strip() or "311"
    nickname = _first_text(raw_feed, ("nickname", "name", "nick", "username")) or owner_uin
    topic_id = str(raw_feed.get("topicId") or raw_feed.get("topicid") or "").strip()
    if not topic_id and owner_uin and feed_id:
        topic_id = f"{owner_uin}_{feed_id}__1"
    unikey = str(raw_feed.get("unikey") or raw_feed.get("curkey") or "").strip()
    if not unikey and owner_uin and feed_id:
        unikey = f"http://user.qzone.qq.com/{owner_uin}/mood/{feed_id}"
    return {
        "feed_key": f"{owner_uin}:{feed_id or created_at_int}",
        "feed_id": feed_id,
        "owner_uin": owner_uin,
        "nickname": nickname,
        "content": content,
        "images": images,
        "created_at": created_at_int,
        "topic_id": topic_id,
        "unikey": unikey,
        "curkey": unikey,
        "appid": appid,
        "raw": raw_feed,
    }


def _extract_msglist_payload(payload: dict[str, Any]) -> list[Any]:
    for key in ("msglist", "feeds", "feedlist", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for nested_key in ("msglist", "feeds", "feedlist"):
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    return nested
    return []


def _qzone_payload_success(payload: dict[str, Any], raw_text: str = "") -> tuple[bool, str]:
    if _looks_like_qzone_auth_page(raw_text):
        message = "Qzone 返回了登录页面或验证码，请刷新 Cookie"
        _set_qzone_auth_failure(message, auth_failure=True)
        return False, message
    if not payload:
        return False, "Qzone 返回无法解析"
    for key in ("code", "ret", "subcode"):
        if key in payload:
            try:
                code = int(payload.get(key) or 0)
            except Exception:
                code = 0
            if code != 0:
                return False, str(payload.get("message") or payload.get("msg") or payload)[:180]
    return True, "ok"


def _qzone_payload_result_code(payload: dict[str, Any]) -> str:
    for key in ("code", "ret", "subcode"):
        if key in payload:
            return f"{key}_{payload.get(key)}"[:64]
    return ""


def _qzone_payload_remote_result(payload: dict[str, Any]) -> tuple[str, float]:
    nested = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    remote_id = str(
        payload.get("tid")
        or payload.get("id")
        or payload.get("feed_id")
        or nested.get("tid")
        or nested.get("id")
        or nested.get("feed_id")
        or ""
    ).strip()
    raw_time = (
        payload.get("created_time")
        or payload.get("abstime")
        or payload.get("time")
        or nested.get("created_time")
        or nested.get("abstime")
        or nested.get("time")
        or 0
    )
    try:
        remote_time = float(raw_time or 0)
    except Exception:
        remote_time = 0.0
    return remote_id[:160], remote_time


def _safe_qzone_payload_message(payload: dict[str, Any], default: str) -> str:
    message = str(payload.get("message") or payload.get("msg") or "").strip()
    if not message:
        return default
    message = re.sub(
        r"(?i)(p_skey|skey|cookie|token|secret)\s*[=:]\s*[^\s;,]+",
        r"\1=***",
        message,
    )
    return message[:180]


def _classify_qzone_write_response(response: Any, *, action: str) -> QzoneWriteResult:
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {408, 409, 425, 429} or status_code >= 500:
        return QzoneWriteResult(
            "unknown",
            f"outcome_unknown: {action}响应状态无法确认",
            f"http_{status_code}",
        )
    if status_code < 200 or status_code >= 300:
        return QzoneWriteResult(
            "definite_failure",
            f"{action}失败，状态码：{status_code}",
            f"http_{status_code}",
        )
    try:
        raw_text = str(response.text or "")
    except Exception as exc:
        return QzoneWriteResult(
            "unknown",
            f"outcome_unknown: {action}响应读取失败",
            "response_read_failed",
            detail={"exception_type": type(exc).__name__},
        )
    if not raw_text.strip():
        return QzoneWriteResult("unknown", f"outcome_unknown: {action}返回为空", "empty_2xx")
    if _looks_like_qzone_auth_page(raw_text):
        message = "Qzone 返回了登录页面或验证码，请刷新 Cookie 后核对实际结果"
        _set_qzone_auth_failure(message, auth_failure=True)
        return QzoneWriteResult("unknown", f"outcome_unknown: {message}", "auth_page_2xx")
    payload = _parse_qzone_jsonp(raw_text)
    if not payload:
        return QzoneWriteResult("unknown", f"outcome_unknown: {action}返回无法解析", "unparseable_2xx")
    success, payload_message = _qzone_payload_success(payload, raw_text)
    result_code = _qzone_payload_result_code(payload)
    if not success:
        return QzoneWriteResult(
            "definite_failure",
            f"{action}失败：{_safe_qzone_payload_message(payload, '腾讯明确返回失败')}",
            result_code or "explicit_failure",
        )
    if not result_code:
        return QzoneWriteResult(
            "unknown",
            f"outcome_unknown: {action}返回缺少明确结果码",
            "missing_result_code_2xx",
        )
    for key in ("code", "ret", "subcode"):
        if key not in payload:
            continue
        try:
            if int(payload.get(key)) != 0:
                return QzoneWriteResult(
                    "definite_failure",
                    f"{action}失败：{_safe_qzone_payload_message(payload, '腾讯明确返回失败')}",
                    result_code,
                )
        except Exception:
            return QzoneWriteResult(
                "unknown",
                f"outcome_unknown: {action}返回了无效结果码",
                "invalid_result_code_2xx",
            )
    remote_id, remote_time = _qzone_payload_remote_result(payload)
    return QzoneWriteResult(
        "succeeded",
        "ok",
        result_code,
        remote_id=remote_id,
        remote_time=remote_time,
    )


class QzoneSocialService:
    """Read and react to Qzone feeds through the same cookie used by shuoshuo publishing."""

    def __init__(self, plugin_config: Any, logger: Any) -> None:
        self.plugin_config = plugin_config
        self.logger = logger
        self.enabled = bool(getattr(plugin_config, "personification_qzone_enabled", False))

    def _context(self, bot_id: str) -> tuple[bool, str, dict[str, Any]]:
        if not self.enabled:
            return False, "Qzone 功能未启用", {}
        return _resolve_qzone_context(self.plugin_config, bot_id)

    async def fetch_user_feeds(
        self,
        *,
        target_uin: str,
        bot_id: str,
        count: int = 10,
        include_comments: bool = False,
        comment_count: int = 20,
    ) -> tuple[bool, str, list[dict[str, Any]]]:
        ok, msg, ctx = self._context(bot_id)
        if not ok:
            return False, msg, []
        target = str(target_uin or "").strip()
        if not target:
            return False, "目标 QQ 为空", []
        url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
        params = {
            "uin": target,
            "ftype": "0",
            "sort": "0",
            "pos": "0",
            "num": str(max(1, min(40, int(count or 10)))),
            "replynum": str(max(1, min(100, int(comment_count or 20)))) if include_comments else "0",
            "g_tk": str(ctx["g_tk"]),
            "callback": "_Callback",
            "code_version": "1",
            "format": "jsonp",
            "need_private_comment": "1",
        }
        headers = _qzone_headers(ctx, referer_uin=target)
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.get(url, params=params, headers=headers)
        except Exception as exc:
            return False, f"读取动态失败：{exc}", []
        if resp.status_code != 200:
            return False, f"读取动态失败，状态码：{resp.status_code}", []
        payload = _parse_qzone_jsonp(resp.text)
        payload_ok, payload_msg = _qzone_payload_success(payload, resp.text)
        if not payload_ok:
            return False, payload_msg, []
        feeds: list[dict[str, Any]] = []
        for item in _extract_msglist_payload(payload):
            normalized = _normalize_qzone_feed(item, target_uin=target)
            if normalized is not None:
                feeds.append(normalized)
        return True, "ok", feeds

    async def like_feed(self, *, feed: dict[str, Any], bot_id: str) -> tuple[bool, str]:
        ok, msg, ctx = self._context(bot_id)
        if not ok:
            return False, msg
        owner = str(feed.get("owner_uin", "") or "").strip()
        unikey = str(feed.get("unikey", "") or "").strip()
        if not owner or not unikey:
            return False, "动态缺少点赞所需字段"
        url = "https://user.qzone.qq.com/proxy/domain/w.qzone.qq.com/cgi-bin/likes/internal_dolike_app"
        data = {
            "qzreferrer": f"https://user.qzone.qq.com/{owner}",
            "opuin": str(ctx["qq"]),
            "unikey": unikey,
            "curkey": str(feed.get("curkey", "") or unikey),
            "from": "1",
            "appid": str(feed.get("appid", "") or "311"),
            "typeid": "0",
            "abstime": str(feed.get("created_at", "") or ""),
            "fid": str(feed.get("feed_id", "") or ""),
            "active": "0",
            "fupdate": "1",
            "format": "json",
        }
        headers = _qzone_headers(ctx, referer_uin=owner)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, params={"g_tk": str(ctx["g_tk"])}, data=data, headers=headers)
        except Exception as exc:
            return False, f"点赞失败：{exc}"
        if resp.status_code != 200:
            return False, f"点赞失败，状态码：{resp.status_code}"
        payload = _parse_qzone_jsonp(resp.text)
        return _qzone_payload_success(payload, resp.text)

    async def forward_feed(
        self,
        *,
        feed: dict[str, Any],
        bot_id: str,
        content: str = "",
    ) -> QzoneWriteResult:
        ok, msg, ctx = self._context(bot_id)
        if not ok:
            return QzoneWriteResult("definite_failure", msg, "preflight_context")
        feed_identity = _qzone_feed_reply_identity(feed)
        owner = feed_identity["owner"]
        feed_id = feed_identity["feed_id"]
        topic_id = feed_identity["topic_id"]
        appid = feed_identity["appid"] or "311"
        unikey = str(feed.get("unikey", "") or feed.get("curkey", "") or "").strip()
        if not owner or not feed_id or not topic_id:
            return QzoneWriteResult("definite_failure", "动态缺少转发所需字段", "preflight_feed_identity")
        text = _clean_qzone_text(content)[:120]
        full_cookie = str(ctx.get("cookie", "") or ctx.get("formatted_cookie", ""))
        headers = _qzone_headers(ctx, referer_uin=owner)
        headers["Cookie"] = full_cookie
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        base_data: dict[str, str] = {
            "uin": str(ctx["qq"]),
            "hostuin": str(ctx["qq"]),
            "hostUin": str(ctx["qq"]),
            "owneruin": owner,
            "ownerUin": owner,
            "t1_uin": owner,
            "t1_tid": feed_id,
            "tid": feed_id,
            "topicId": topic_id,
            "topicid": topic_id,
            "appid": appid,
            "con": text,
            "content": text,
            "format": "json",
            "feedsType": "100",
            "with_cmt": "0",
            "private": "0",
            "paramstr": "1",
            "plat": "qzone",
            "source": "ic",
            "ref": "feeds",
            "platformid": "52",
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "qzreferrer": f"https://user.qzone.qq.com/{owner}",
        }
        if unikey:
            base_data["curkey"] = unikey
            base_data["unikey"] = unikey

        attempts: list[tuple[str, dict[str, str]]] = [
            (
                "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_forward_v6",
                base_data,
            ),
            (
                "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_re_feeds",
                {**base_data, "forward": "1", "richtype": "", "richval": ""},
            ),
        ]
        last_msg = ""
        for attempt_index, (url, data) in enumerate(attempts):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, params={"g_tk": str(ctx["g_tk"])}, data=data, headers=headers)
            except Exception as exc:
                return QzoneWriteResult(
                    "unknown",
                    f"outcome_unknown: 转发请求异常：{type(exc).__name__}",
                    "dispatch_exception",
                    detail={"exception_type": type(exc).__name__},
                )
            classified = _classify_qzone_write_response(resp, action="转发")
            if classified.status != "succeeded":
                last_msg = classified.message
                if attempt_index == 0 and classified.result_code in {"http_404", "http_405"}:
                    continue
                return classified
            return classified
        return QzoneWriteResult("definite_failure", last_msg or "转发失败", "fallback_exhausted")

    async def _reply_comment_sub(
        self,
        *,
        feed: dict[str, Any],
        ctx: dict[str, Any],
        content: str,
        reply_to_comment: dict[str, Any],
    ) -> tuple[bool, str]:
        """Post a level-2 threaded sub-comment under a parent comment."""
        text = _clean_qzone_text(content)
        if not text:
            return False, "回复内容为空"
        feed_identity = _qzone_feed_reply_identity(feed)
        target = _qzone_comment_reply_target(reply_to_comment)
        owner = feed_identity["owner"]
        feed_id = feed_identity["feed_id"]
        topic_id = feed_identity["topic_id"]
        comment_id = target["comment_id"]
        reply_uin = target["user_id"]
        if not owner or not feed_id or not topic_id or not comment_id or not reply_uin:
            missing = []
            if not owner:
                missing.append("owner")
            if not feed_id:
                missing.append("feedId")
            if not topic_id:
                missing.append("topicId")
            if not comment_id:
                missing.append("commentId")
            if not reply_uin:
                missing.append("replyUin")
            self.logger.warning(f"[qzone] 子评论回复缺少字段: {missing}，feed={feed_identity}，target={target}")
            return False, f"缺少回复留言所需字段: {missing}"

        appid = feed_identity["appid"] or "311"
        full_cookie = str(ctx.get("cookie", "") or ctx.get("formatted_cookie", ""))
        base_data: dict[str, str] = {
            "uin": str(ctx["qq"]),
            "hostUin": owner,
            "hostuin": owner,
            "appid": appid,
            "topicId": topic_id,
            "topicid": topic_id,
            "t1_source": feed_identity["t1_source"],
            "t1_uin": owner,
            "t1_tid": feed_id,
            "t2_uin": reply_uin,
            "t2_tid": comment_id,
            "subdotype": feed_identity["subdotype"],
            "signin": feed_identity["signin"],
            "sceneid": feed_identity["sceneid"],
            "commentUin": reply_uin,
            "commentuin": reply_uin,
            "commentTid": comment_id,
            "commenttid": comment_id,
            "replyId": comment_id,
            "replyid": comment_id,
            "commentId": comment_id,
            "commentid": comment_id,
            "replyUin": reply_uin,
            "replyuin": reply_uin,
            "content": text[:80],
            "private": "0",
            "paramstr": "1",
            "format": "json",
            "feedsType": "100",
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "plat": "qzone",
            "source": "ic",
            "ref": "feeds",
            "platformid": "52",
            "qzreferrer": f"https://user.qzone.qq.com/{owner}",
        }
        if target["nickname"]:
            base_data["replyNick"] = target["nickname"]

        url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_re_feeds"
        headers = _qzone_headers(ctx, referer_uin=owner)
        headers["Cookie"] = full_cookie
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        log_info = getattr(self.logger, "info", None)
        if callable(log_info):
            log_info(
                f"[qzone] subreply re_feeds owner={owner} topicId={topic_id} "
                f"feedId={feed_id} commentId={comment_id} replyUin={reply_uin}"
            )
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, params={"g_tk": str(ctx["g_tk"])}, data=base_data, headers=headers)
        except Exception as exc:
            self.logger.warning(f"[qzone] 子评论回复请求失败: {exc}")
            return False, f"子评论回复请求异常：{exc}"
        if callable(log_info):
            log_info(f"[qzone] subreply re_feeds 状态码={resp.status_code} 响应={resp.text[:400]}")
        if resp.status_code != 200:
            return False, f"子评论回复失败，状态码：{resp.status_code}"
        payload = _parse_qzone_jsonp(resp.text)
        return _qzone_payload_success(payload, resp.text)

    async def comment_feed(
        self,
        *,
        feed: dict[str, Any],
        bot_id: str,
        content: str,
        reply_to_comment: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        text = str(content or "").strip()
        if not text:
            return False, "评论内容为空"
        ok, msg, ctx = self._context(bot_id)
        if not ok:
            return False, msg
        owner = str(feed.get("owner_uin", "") or "").strip()
        topic_id = str(feed.get("topic_id", "") or "").strip()
        if not owner or not topic_id:
            return False, "动态缺少评论所需字段"
        if isinstance(reply_to_comment, dict):
            sub_ok, sub_msg = await self._reply_comment_sub(
                feed=feed,
                ctx=ctx,
                content=text,
                reply_to_comment=reply_to_comment,
            )
            if sub_ok:
                return True, "ok"
            if "请求异常" in sub_msg or "无法解析" in sub_msg or "outcome_unknown" in sub_msg:
                return False, f"outcome_unknown: {sub_msg}"
            self.logger.warning(f"[qzone] 子评论回复失败，回退为顶级 @ 评论: {sub_msg}")

        url = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_re_feeds"
        send_text = _format_qzone_reply_content(text, reply_to_comment)
        data = {
            "uin": str(ctx["qq"]),
            "hostUin": owner,
            "topicId": topic_id,
            "content": send_text[:80],
            "private": "0",
            "paramstr": "1",
            "format": "json",
            "feedsType": "100",
            "plat": "qzone",
            "source": "ic",
            "ref": "feeds",
            "platformid": "52",
            "richtype": "",
            "richval": "",
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "qzreferrer": f"https://user.qzone.qq.com/{owner}",
        }
        if isinstance(reply_to_comment, dict):
            reply_uin = str(reply_to_comment.get("user_id", "") or "").strip()
            reply_id = str(reply_to_comment.get("comment_id", "") or "").strip()
            reply_nick = str(reply_to_comment.get("nickname", "") or "").strip()
            if reply_uin:
                data.update(
                    {
                        "replyUin": reply_uin,
                        "replyuin": reply_uin,
                        "reply_uin": reply_uin,
                        "touin": reply_uin,
                        "toUin": reply_uin,
                        "targetuin": reply_uin,
                        "targetUin": reply_uin,
                        "sourceUin": reply_uin,
                    }
                )
            if reply_id:
                data.update(
                    {
                        "commentid": reply_id,
                        "commentId": reply_id,
                        "replyid": reply_id,
                        "parentid": reply_id,
                    }
                )
            if reply_nick:
                data.update(
                    {
                        "replyNick": reply_nick,
                        "replynick": reply_nick,
                        "reply_nick": reply_nick,
                    }
                )
        headers = _qzone_headers(ctx, referer_uin=owner)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, params={"g_tk": str(ctx["g_tk"])}, data=data, headers=headers)
        except Exception as exc:
            return False, f"评论失败：{exc}"
        if resp.status_code != 200:
            return False, f"评论失败，状态码：{resp.status_code}"
        payload = _parse_qzone_jsonp(resp.text)
        return _qzone_payload_success(payload, resp.text)


_QZONE_IMAGE_MAX_BYTES = 12 * 1024 * 1024


def _prepare_qzone_image(image_b64: str) -> dict[str, Any]:
    try:
        image_bytes = _decode_image_b64(image_b64)
    except Exception as exc:
        raise QzoneImageUploadError(
            "image_invalid_base64",
            detail={"exception_type": type(exc).__name__},
        ) from exc
    if not image_bytes:
        raise QzoneImageUploadError("image_empty")
    if len(image_bytes) > _QZONE_IMAGE_MAX_BYTES:
        raise QzoneImageUploadError("image_too_large")
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        image_format, mime_type, extension = "PNG", "image/png", "png"
    elif image_bytes.startswith(b"\xff\xd8\xff"):
        image_format, mime_type, extension = "JPEG", "image/jpeg", "jpg"
    elif image_bytes.startswith((b"GIF87a", b"GIF89a")):
        image_format, mime_type, extension = "GIF", "image/gif", "gif"
    elif image_bytes.startswith(b"RIFF") and b"WEBP" in image_bytes[:32]:
        image_format, mime_type, extension = "WEBP", "image/webp", "webp"
    else:
        raise QzoneImageUploadError("image_format_unsupported")
    if image_format in {"PNG", "JPEG"}:
        return {
            "data": image_bytes,
            "filename": f"qzone.{extension}",
            "mime_type": mime_type,
            "converted": False,
        }
    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError:
        return {
            "data": image_bytes,
            "filename": f"qzone.{extension}",
            "mime_type": mime_type,
            "converted": False,
        }
    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            image.seek(0)
            converted_image = image.convert("RGBA")
            output = io.BytesIO()
            converted_image.save(output, format="PNG", optimize=True)
    except QzoneImageUploadError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise QzoneImageUploadError(
            "image_format_unsupported",
            detail={"exception_type": type(exc).__name__},
        ) from exc
    converted = output.getvalue()
    if not converted or len(converted) > _QZONE_IMAGE_MAX_BYTES:
        raise QzoneImageUploadError("image_conversion_failed")
    return {
        "data": converted,
        "filename": "qzone.png",
        "mime_type": "image/png",
        "converted": True,
    }


def _extract_qzone_pic_bo(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"(?:^|[?&])(?:bo|pic_bo|picbo)=([^&\s]+)", text)
    return match.group(1).strip() if match else ""


def _build_qzone_image_richval(payload: dict[str, Any]) -> str:
    album_id = str(payload.get("albumid") or payload.get("album_id") or "").strip()
    lloc = str(payload.get("lloc") or "").strip()
    sloc = str(payload.get("sloc") or "").strip()
    if not album_id or not lloc or not sloc:
        return ""
    image_type = str(payload.get("type") or payload.get("phototype") or "1").strip() or "1"
    height = str(payload.get("height") or payload.get("h") or "0").strip() or "0"
    width = str(payload.get("width") or payload.get("w") or "0").strip() or "0"
    return ",".join(("", album_id, lloc, sloc, image_type, height, width, "", height, width))


async def _upload_qzone_image(
    *,
    image_b64: str,
    cookie: str,
    qq: str,
    p_skey: str,
    logger: Any,
) -> QzoneImageUploadResult:
    try:
        prepared = await asyncio.to_thread(_prepare_qzone_image, image_b64)
    except QzoneImageUploadError:
        raise
    except Exception as exc:
        raise QzoneImageUploadError(
            "image_prepare_failed",
            detail={"exception_type": type(exc).__name__},
        ) from exc

    g_tk = _get_g_tk(p_skey)
    cookie_values = _parse_qzone_cookie(cookie)
    skey = str(cookie_values.get("skey") or p_skey)

    url = f"https://up.qzone.qq.com/cgi-bin/upload/cgi_upload_image?g_tk={g_tk}"
    data = {
        "filename": prepared["filename"],
        "uin": qq,
        "p_uin": qq,
        "skey": skey,
        "p_skey": p_skey,
        "zzpaneluin": qq,
        "zzpanelkey": "",
        "qzonetoken": str(cookie_values.get("qzonetoken") or cookie_values.get("g_qzonetoken") or ""),
        "uploadtype": "1",
        "albumtype": "7",
        "exttype": "0",
        "refer": "shuoshuo",
        "output_type": "json",
        "charset": "utf-8",
        "output_charset": "utf-8",
        "upload_hd": "1",
        "hd_width": "2048",
        "hd_height": "10000",
        "hd_quality": "96",
        "backUrls": (
            "http://upbak.photo.qzone.qq.com/cgi-bin/upload/cgi_upload_image,"
            "http://119.147.64.75/cgi-bin/upload/cgi_upload_image"
        ),
        "url": url,
        "base64": "1",
        "picfile": base64.b64encode(prepared["data"]).decode("ascii"),
        "qzreferrer": f"https://user.qzone.qq.com/{qq}",
    }
    headers = {
        "Cookie": str(cookie),
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://user.qzone.qq.com/{qq}",
        "Origin": "https://user.qzone.qq.com",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            resp = await client.post(url, data=data, headers=headers)
    except Exception as exc:
        raise QzoneImageUploadError(
            "image_upload_transport_failed",
            detail={"exception_type": type(exc).__name__},
        ) from exc
    if resp.status_code != 200:
        raise QzoneImageUploadError(
            "image_upload_http_error",
            detail={"status_code": int(resp.status_code)},
        )
    if _looks_like_qzone_auth_page(resp.text):
        _set_qzone_auth_failure("Qzone 配图上传返回登录页面或验证码", auth_failure=True)
        raise QzoneImageUploadError("image_upload_auth_page")
    payload = _parse_qzone_jsonp(resp.text)
    if not payload:
        raise QzoneImageUploadError("image_upload_invalid_response")
    result_code = payload.get("ret", payload.get("code", 0))
    try:
        upload_ok = int(result_code or 0) == 0
    except (TypeError, ValueError):
        upload_ok = False
    if not upload_ok:
        raise QzoneImageUploadError("image_upload_rejected")
    upload_data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    richval = str(upload_data.get("richval") or payload.get("richval") or "").strip()
    if not richval:
        richval = _build_qzone_image_richval(upload_data)
    pic_bo = str(
        upload_data.get("pic_bo")
        or upload_data.get("picbo")
        or upload_data.get("bo")
        or payload.get("pic_bo")
        or payload.get("picbo")
        or ""
    ).strip()
    if not pic_bo:
        for key in ("url", "origin_url", "pre", "raw_url"):
            pic_bo = _extract_qzone_pic_bo(upload_data.get(key) or payload.get(key))
            if pic_bo:
                break
    if not richval:
        raise QzoneImageUploadError("image_upload_missing_richval")
    if not pic_bo:
        raise QzoneImageUploadError("image_upload_missing_pic_bo")
    log_info = getattr(logger, "info", None)
    if callable(log_info):
        log_info(
            f"[qzone] image upload ready mime={prepared['mime_type']} converted={prepared['converted']}"
        )
    return QzoneImageUploadResult(
        richval=richval,
        pic_bo=pic_bo,
        mime_type=str(prepared["mime_type"]),
        converted=bool(prepared["converted"]),
    )


def build_qzone_services(
    plugin_config: Any,
    logger: Any,
) -> tuple[bool, Callable[[str, str], Awaitable[QzoneWriteResult]], Callable[..., Awaitable[tuple[bool, str]]]]:
    qzone_enabled = bool(getattr(plugin_config, "personification_qzone_enabled", False))
    async def update_qzone_cookie(bot: Any, *, force: bool = False) -> tuple[bool, str]:
        """自动获取并刷新 Qzone Cookie，供定时任务或手动命令调用。"""
        if not qzone_enabled:
            return False, "Qzone 功能未启用"
        now = time.time()
        with _AUTH_STATE_LOCK:
            if _AUTH_STATE["refreshing"]:
                return False, "Qzone Cookie 正在刷新"
            if (
                not force
                and _AUTH_STATE["status"] == "healthy"
                and _AUTH_STATE["last_success_at"]
                and now - float(_AUTH_STATE["last_success_at"]) < _AUTH_REFRESH_CACHE_SECONDS
            ):
                return True, "cached"
            _AUTH_STATE["refreshing"] = True
            _AUTH_STATE["last_refresh_at"] = now
        try:
            cookies_resp = await bot.get_cookies(domain="qzone.qq.com")
            cookie = str(cookies_resp.get("cookies", "") or "").strip()
            if not cookie:
                _set_qzone_auth_failure("自动获取 Cookie 失败，返回结果为空")
                return False, "自动获取 Cookie 失败，返回结果为空"
            if "uin=" not in cookie:
                cookie = f"uin=o{bot.self_id}; {cookie}"
            return await install_qzone_cookie(
                cookie=cookie,
                expected_bot_id=str(bot.self_id),
                plugin_config=plugin_config,
                logger=logger,
                source="onebot",
            )
        except Exception as e:
            _set_qzone_auth_failure(e)
            return False, str(e)
        finally:
            with _AUTH_STATE_LOCK:
                _AUTH_STATE["refreshing"] = False

    async def publish_qzone_shuo(content: str, bot_id: str) -> QzoneWriteResult:
        if not qzone_enabled:
            return QzoneWriteResult("definite_failure", "Qzone 功能未启用", "preflight_disabled")
        cookie = _get_cookie_from_config(plugin_config)
        if not cookie:
            return QzoneWriteResult("definite_failure", "未配置 Qzone Cookie", "preflight_cookie_missing")

        post_started = False
        try:
            content_without_image_markers, image_payloads = _extract_image_b64_markers(str(content or ""))
            cleaned_content = re.sub(
                r"\[图片(?:·[^\]]+)?\]|\[表情\]|\[动画表情\]",
                "",
                content_without_image_markers,
            ).strip()
            if not cleaned_content:
                return QzoneWriteResult(
                    "definite_failure",
                    "说说内容不能为空（已过滤图片和表情）",
                    "preflight_content_empty",
                )

            pskey_match = re.search(r"p_skey=([^; ]+)", cookie)
            if not pskey_match:
                return QzoneWriteResult(
                    "definite_failure",
                    "Cookie 缺少 p_skey 字段",
                    "preflight_p_skey_missing",
                )
            p_skey = pskey_match.group(1)

            uin_match = re.search(r"uin=[o0]*(\d+)", cookie)
            qq = uin_match.group(1) if uin_match else str(bot_id)
            expected_bot_id = str(bot_id or "").strip()
            if expected_bot_id and qq != expected_bot_id:
                return QzoneWriteResult(
                    "definite_failure",
                    "Qzone Cookie 与目标 Bot 不匹配",
                    "preflight_account_mismatch",
                )

            g_tk = _get_g_tk(p_skey)
            image_upload: QzoneImageUploadResult | None = None
            if image_payloads:
                try:
                    image_upload = await _upload_qzone_image(
                        image_b64=image_payloads[0],
                        cookie=cookie,
                        qq=qq,
                        p_skey=p_skey,
                        logger=logger,
                    )
                except QzoneImageUploadError as exc:
                    log_warning = getattr(logger, "warning", None)
                    if callable(log_warning):
                        log_warning(f"[qzone] image upload failed code={exc.result_code}")
                    return QzoneWriteResult(
                        "definite_failure",
                        "Qzone 配图上传失败，尚未提交说说",
                        exc.result_code,
                        detail={
                            "image_requested": True,
                            "image_uploaded": False,
                            **exc.detail,
                        },
                    )
            url = (
                "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/"
                "cgi-bin/emotion_cgi_publish_v6"
            )
            data = {
                "syn_tweet_verson": "1",
                "paramstr": "1",
                "who": "1",
                "con": cleaned_content,
                "feedversion": "1",
                "ver": "1",
                "ugc_right": "1",
                "to_sign": "0",
                "hostuin": qq,
                "code_version": "1",
                "issyncweibo": "0",
                "format": "json",
                "qzreferrer": f"https://user.qzone.qq.com/{qq}",
            }
            if image_upload is not None:
                data.update({
                    "pic_template": "",
                    "richtype": "1",
                    "subrichtype": "1",
                    "richval": image_upload.richval,
                    "pic_bo": image_upload.pic_bo,
                })
            headers = {
                "Cookie": str(cookie),
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
                ),
                "Referer": f"https://user.qzone.qq.com/{qq}",
                "Origin": "https://user.qzone.qq.com",
            }

            async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
                post_started = True
                resp = await client.post(
                    url,
                    params={"g_tk": str(g_tk), "uin": qq},
                    data=data,
                    headers=headers,
                )
            classified = _classify_qzone_write_response(resp, action="发布")
            publish_detail = {
                "image_requested": bool(image_payloads),
                "image_uploaded": image_upload is not None,
            }
            if image_upload is not None:
                publish_detail.update({
                    "image_mime_type": image_upload.mime_type,
                    "image_converted": image_upload.converted,
                })
            publish_detail.update(classified.detail)
            if classified.status == "succeeded":
                return QzoneWriteResult(
                    "succeeded",
                    "发布成功",
                    classified.result_code,
                    remote_id=classified.remote_id,
                    remote_time=classified.remote_time,
                    detail=publish_detail,
                )
            return QzoneWriteResult(
                classified.status,
                classified.message,
                classified.result_code,
                detail=publish_detail,
            )
        except Exception as e:
            if post_started:
                return QzoneWriteResult(
                    "unknown",
                    f"outcome_unknown: 发布请求异常：{type(e).__name__}",
                    "dispatch_exception",
                    detail={"exception_type": type(e).__name__},
                )
            return QzoneWriteResult(
                "definite_failure",
                f"发布前校验异常：{type(e).__name__}",
                "preflight_exception",
                detail={"exception_type": type(e).__name__},
            )

    return qzone_enabled, publish_qzone_shuo, update_qzone_cookie


def build_qzone_social_service(plugin_config: Any, logger: Any) -> QzoneSocialService:
    return QzoneSocialService(plugin_config=plugin_config, logger=logger)
