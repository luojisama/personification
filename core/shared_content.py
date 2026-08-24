"""Pure normalization contracts for shared cards and merged-forward messages.

This module deliberately performs no network I/O.  It turns OneBot-shaped
payloads into bounded, provenance-preserving data and exposes a fail-closed
short-link policy that an HTTP client can apply to every redirect hop.

All extracted page/card/comment material remains untrusted data.  Parsing a
card proves only that the metadata was present in the inbound payload; it does
not prove that the linked page is reachable or that its claims are true.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree


SHARED_CONTENT_TRUST = "untrusted_data_only"
MAX_FORWARD_DEPTH = 3
MAX_FORWARD_NODES = 50
MAX_FORWARD_TEXT_CHARS = 20_000
MAX_SHARED_COMMENTS = 20
MAX_SHARED_COMMENT_CHARS = 200
MAX_SHARED_COMMENTS_TOTAL_CHARS = 4_000
MAX_SHORT_LINK_REDIRECTS = 5

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")
_CARD_PREFIX_RE = re.compile(r"^\s*\[[^\]\r\n]{1,40}\]\s*")
_XML_DECLARATION_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

_URL_KEYS = (
    "jumpUrl",
    "jump_url",
    "qqdocurl",
    "target_url",
    "targetUrl",
    "share_url",
    "shareUrl",
    "url",
    "href",
    "actionData",
)
_TITLE_KEYS = ("title", "prompt", "brief", "name")
_SUMMARY_KEYS = ("summary", "desc", "description", "content")
_COVER_KEYS = (
    "cover",
    "cover_url",
    "coverUrl",
    "preview",
    "image",
    "image_url",
    "imageUrl",
    "thumbnail",
    "picUrl",
)
_AUTHOR_KEYS = ("author", "author_name", "authorName", "source", "nickname")
_PUBLISHED_KEYS = (
    "published_at",
    "publishedAt",
    "publish_time",
    "publishTime",
    "pubtime",
    "create_time",
    "createTime",
    "time",
)
_COMMENT_CONTAINER_KEYS = ("comments", "comment_list", "commentList", "replies", "discussion")
_COMMENT_TEXT_KEYS = ("text", "content", "comment", "summary", "desc")
_MEDIA_CONTAINER_KEYS = {
    "images": "image",
    "image_list": "image",
    "imageList": "image",
    "pictures": "image",
    "pics": "image",
    "videos": "video",
    "video": "video",
    "video_url": "video",
    "videoUrl": "video",
    "audios": "audio",
    "audio": "audio",
    "audio_url": "audio",
    "audioUrl": "audio",
}
_MEDIA_REF_KEYS = ("url", "src", "file", "file_id", "fileId", "id", "path")

_PLATFORM_HOSTS = (
    (("bilibili.com", "b23.tv"), "bilibili"),
    (("douyin.com", "iesdouyin.com"), "douyin"),
    (("weibo.com", "weibo.cn", "t.cn"), "weibo"),
    (("mp.weixin.qq.com",), "wechat_official"),
    (("x.com", "twitter.com", "t.co"), "x"),
)


def _clean_text(value: Any, limit: int | None = None) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set, frozenset)):
        return ""
    text = html.unescape(str(value))
    text = _CONTROL_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip()
    if limit is not None:
        text = text[: max(0, int(limit))]
    return text


def _strip_card_prefix(value: Any, limit: int) -> str:
    return _CARD_PREFIX_RE.sub("", _clean_text(value)).strip("[] ")[:limit]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _canonical_http_url(value: Any) -> str:
    raw = html.unescape(str(value or "")).strip()
    if not raw or len(raw) > 4_096:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None:
            return ""
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port
    except (UnicodeError, ValueError):
        return ""
    if not host:
        return ""
    rendered_host = f"[{host}]" if ":" in host else host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        rendered_host = f"{rendered_host}:{port}"
    return urlunsplit((scheme, rendered_host, parsed.path or "", parsed.query, ""))[:2_048]


def canonicalize_shared_url(value: Any) -> str:
    """Return a bounded HTTP(S) URL with credentials/fragment removed."""

    return _canonical_http_url(value)


def _platform_from_url(value: str) -> str:
    try:
        host = str(urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return "unknown"
    for suffixes, platform in _PLATFORM_HOSTS:
        if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            return platform
    return "unknown"


@dataclass(frozen=True, slots=True)
class SharedMediaRef:
    kind: str
    ref: str
    source: str
    source_path: tuple[str, ...] = ()
    trust: str = SHARED_CONTENT_TRUST

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "source": self.source,
            "source_path": list(self.source_path),
            "trust": self.trust,
        }


@dataclass(frozen=True, slots=True)
class SharedComment:
    text: str
    author: str = ""
    published_at: str = ""
    trust: str = SHARED_CONTENT_TRUST

    def to_dict(self) -> dict[str, str]:
        return {
            "text": self.text,
            "author": self.author,
            "published_at": self.published_at,
            "trust": self.trust,
        }


@dataclass(frozen=True, slots=True)
class SharedContent:
    platform: str = "unknown"
    original_url: str = ""
    canonical_url: str = ""
    title: str = ""
    summary: str = ""
    cover: str = ""
    author: str = ""
    published_at: str = ""
    comments: tuple[SharedComment, ...] = ()
    media_refs: tuple[SharedMediaRef, ...] = ()
    evidence_state: str = "unavailable"
    trust: str = SHARED_CONTENT_TRUST

    @property
    def available(self) -> bool:
        return self.evidence_state != "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "original_url": self.original_url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "summary": self.summary,
            "cover": self.cover,
            "author": self.author,
            "published_at": self.published_at,
            "comments": [item.to_dict() for item in self.comments],
            "media_refs": [item.to_dict() for item in self.media_refs],
            "evidence_state": self.evidence_state,
            "trust": self.trust,
        }


def _walk_mappings(value: Any, *, max_depth: int = 8, max_items: int = 256) -> list[tuple[tuple[str, ...], Mapping[str, Any]]]:
    output: list[tuple[tuple[str, ...], Mapping[str, Any]]] = []
    stack: list[tuple[tuple[str, ...], Any, int]] = [((), value, 0)]
    visited = 0
    while stack and visited < max_items:
        path, current, depth = stack.pop()
        visited += 1
        if isinstance(current, Mapping):
            output.append((path, current))
            if depth >= max_depth:
                continue
            children = list(current.items())[:80]
            for key, child in reversed(children):
                if isinstance(child, (Mapping, list, tuple)):
                    stack.append(((*path, str(key)[:80]), child, depth + 1))
        elif isinstance(current, (list, tuple)) and depth < max_depth:
            for index, child in reversed(list(enumerate(current[:80]))):
                if isinstance(child, (Mapping, list, tuple)):
                    stack.append(((*path, str(index)), child, depth + 1))
    return output


def _pick_text(mappings: Sequence[tuple[tuple[str, ...], Mapping[str, Any]]], keys: Sequence[str], limit: int) -> str:
    for _path, item in mappings:
        for key in keys:
            value = item.get(key)
            text = _clean_text(value, limit)
            if text:
                return text
    return ""


def _pick_url(mappings: Sequence[tuple[tuple[str, ...], Mapping[str, Any]]], keys: Sequence[str]) -> tuple[str, str]:
    for _path, item in mappings:
        for key in keys:
            raw = _clean_text(item.get(key), 4_096)
            canonical = _canonical_http_url(raw)
            if canonical:
                return raw[:2_048], canonical
    return "", ""


def _comment_from_value(value: Any) -> SharedComment | None:
    if isinstance(value, Mapping):
        text = ""
        for key in _COMMENT_TEXT_KEYS:
            text = _clean_text(value.get(key), MAX_SHARED_COMMENT_CHARS)
            if text:
                break
        author_value = _first_present(value, *_AUTHOR_KEYS)
        if isinstance(author_value, Mapping):
            author_value = _first_present(author_value, "nickname", "name", "display_name", "user_name")
        sender = _mapping(value.get("sender"))
        author = _clean_text(author_value or _first_present(sender, "nickname", "name"), 80)
        published = _clean_text(_first_present(value, *_PUBLISHED_KEYS), 80)
    else:
        text = _clean_text(value, MAX_SHARED_COMMENT_CHARS)
        author = ""
        published = ""
    if not text:
        return None
    return SharedComment(text=text, author=author, published_at=published)


def _extract_comments(mappings: Sequence[tuple[tuple[str, ...], Mapping[str, Any]]]) -> tuple[SharedComment, ...]:
    comments: list[SharedComment] = []
    used_chars = 0
    for _path, item in mappings:
        for key in _COMMENT_CONTAINER_KEYS:
            container = item.get(key)
            values = list(container[:MAX_SHARED_COMMENTS]) if isinstance(container, (list, tuple)) else []
            for value in values:
                comment = _comment_from_value(value)
                if comment is None:
                    continue
                remaining = MAX_SHARED_COMMENTS_TOTAL_CHARS - used_chars
                if remaining <= 0:
                    return tuple(comments)
                text = comment.text[:remaining]
                if not text:
                    return tuple(comments)
                comments.append(replace(comment, text=text))
                used_chars += len(text)
                if len(comments) >= MAX_SHARED_COMMENTS:
                    return tuple(comments)
    return tuple(comments)


def _media_scalar(value: Any) -> str:
    if isinstance(value, Mapping):
        value = _first_present(value, *_MEDIA_REF_KEYS)
    raw = _clean_text(value, 4_096)
    if raw.lower().startswith("data:"):
        return ""
    return _canonical_http_url(raw) or raw[:2_048]


def _extract_media_refs(
    mappings: Sequence[tuple[tuple[str, ...], Mapping[str, Any]]],
    *,
    cover: str,
) -> tuple[SharedMediaRef, ...]:
    refs: list[SharedMediaRef] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, ref: str, source: str, path: tuple[str, ...]) -> None:
        normalized_kind = kind if kind in {"image", "video", "audio", "file"} else "unknown"
        key = (normalized_kind, ref)
        if not ref or key in seen or len(refs) >= 40:
            return
        seen.add(key)
        refs.append(SharedMediaRef(normalized_kind, ref, source, path))

    if cover:
        add("image", cover, "card.cover", ())
    for path, item in mappings:
        for key, kind in _MEDIA_CONTAINER_KEYS.items():
            value = item.get(key)
            if isinstance(value, (list, tuple)):
                candidates = value[:20]
            else:
                candidates = (value,)
            for index, candidate in enumerate(candidates):
                ref = _media_scalar(candidate)
                if ref:
                    add(kind, ref, f"card.{key}", (*path, key, str(index)))
    return tuple(refs)


def _shared_evidence_state(*, canonical_url: str, title: str, summary: str, cover: str, comments: Sequence[SharedComment]) -> str:
    if canonical_url and (title or summary):
        return "metadata_only"
    if canonical_url or title or summary or cover or comments:
        return "partial"
    return "unavailable"


def _build_shared_content(value: Mapping[str, Any]) -> SharedContent:
    mappings = _walk_mappings(value)
    original_url, canonical_url = _pick_url(mappings, _URL_KEYS)
    title = _strip_card_prefix(_pick_text(mappings, _TITLE_KEYS, 300), 300)
    summary = _clean_text(_pick_text(mappings, _SUMMARY_KEYS, 1_000), 1_000)
    cover_raw = _pick_text(mappings, _COVER_KEYS, 4_096)
    cover = _canonical_http_url(cover_raw)
    author = _pick_text(mappings, _AUTHOR_KEYS, 120)
    published_at = _pick_text(mappings, _PUBLISHED_KEYS, 80)
    comments = _extract_comments(mappings)
    media_refs = _extract_media_refs(mappings, cover=cover)
    evidence_state = _shared_evidence_state(
        canonical_url=canonical_url,
        title=title,
        summary=summary,
        cover=cover,
        comments=comments,
    )
    return SharedContent(
        platform=_platform_from_url(canonical_url),
        original_url=original_url,
        canonical_url=canonical_url,
        title=title,
        summary=summary,
        cover=cover,
        author=author,
        published_at=published_at,
        comments=comments,
        media_refs=media_refs,
        evidence_state=evidence_state,
    )


def _unwrap_onebot_card(payload: Any, segment_type: str) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    actual_type = _clean_text(payload.get("type"), 20).lower()
    if actual_type in {"json", "xml", "share"}:
        segment_type = actual_type
        data = payload.get("data")
        if isinstance(data, Mapping):
            if segment_type in {"json", "xml"} and "data" in data:
                return data.get("data")
            return data
        return data
    if segment_type in {"json", "xml"} and set(payload).issubset({"data", "resid"}):
        return payload.get("data")
    return payload


def parse_json_share_card(payload: Any) -> SharedContent:
    """Parse a JSON/share OneBot payload without fetching its target URL."""

    raw = _unwrap_onebot_card(payload, "json")
    if isinstance(raw, str):
        if len(raw) > 1_048_576:
            return SharedContent()
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError, RecursionError):
            return SharedContent()
    if not isinstance(raw, Mapping):
        return SharedContent()
    return _build_shared_content(raw)


def _xml_text(element: ElementTree.Element, limit: int) -> str:
    return _clean_text(" ".join(element.itertext()), limit)


def parse_xml_share_card(payload: Any) -> SharedContent:
    """Parse a bounded QQ XML card; DTD/entity declarations fail closed."""

    raw_value = _unwrap_onebot_card(payload, "xml")
    raw = str(raw_value or "").strip()
    if not raw or len(raw) > 1_048_576 or _XML_DECLARATION_RE.search(raw):
        return SharedContent()
    try:
        root = ElementTree.fromstring(raw)
    except (ElementTree.ParseError, ValueError):
        return SharedContent()

    flattened: list[tuple[tuple[str, ...], Mapping[str, Any]]] = []
    comments: list[SharedComment] = []
    media_refs: list[SharedMediaRef] = []
    comment_chars = 0
    for index, element in enumerate(root.iter()):
        if index >= 256:
            break
        tag = str(element.tag).split("}")[-1].lower()
        attrs = {str(key).split("}")[-1]: value for key, value in element.attrib.items()}
        text = _xml_text(element, 1_000)
        row: dict[str, Any] = dict(attrs)
        if text:
            row.setdefault("content", text)
            if tag in {"title", "summary", "desc", "author", "source"}:
                row.setdefault(tag, text)
        flattened.append(((tag, str(index)), row))

        if tag in {"comment", "reply"} and len(comments) < MAX_SHARED_COMMENTS:
            comment = _comment_from_value({**attrs, "text": text})
            if comment is not None:
                remaining = MAX_SHARED_COMMENTS_TOTAL_CHARS - comment_chars
                if remaining > 0:
                    bounded = replace(comment, text=comment.text[:remaining])
                    if bounded.text:
                        comments.append(bounded)
                        comment_chars += len(bounded.text)

        if tag in {"picture", "image", "video", "audio"}:
            ref = _media_scalar(_first_present(attrs, *_MEDIA_REF_KEYS, *_COVER_KEYS))
            if ref:
                kind = "image" if tag == "picture" else tag
                media_refs.append(SharedMediaRef(kind, ref, f"xml.{tag}", (tag, str(index))))

    root_map = {str(key).split("}")[-1]: value for key, value in root.attrib.items()}
    flattened.insert(0, (("root",), root_map))
    original_url, canonical_url = _pick_url(flattened, _URL_KEYS)
    title = _strip_card_prefix(_pick_text(flattened, ("title", "brief"), 300), 300)
    summary = _pick_text(flattened, ("summary", "desc", "description"), 1_000)
    cover = _canonical_http_url(_pick_text(flattened, _COVER_KEYS, 4_096))
    author = _pick_text(flattened, ("author", "name", "source"), 120)
    published_at = _pick_text(flattened, _PUBLISHED_KEYS, 80)
    if cover and not any(item.ref == cover for item in media_refs):
        media_refs.insert(0, SharedMediaRef("image", cover, "card.cover"))
    deduped_media: list[SharedMediaRef] = []
    seen_media: set[tuple[str, str]] = set()
    for item in media_refs[:40]:
        key = (item.kind, item.ref)
        if key not in seen_media:
            seen_media.add(key)
            deduped_media.append(item)
    evidence_state = _shared_evidence_state(
        canonical_url=canonical_url,
        title=title,
        summary=summary,
        cover=cover,
        comments=comments,
    )
    return SharedContent(
        platform=_platform_from_url(canonical_url),
        original_url=original_url,
        canonical_url=canonical_url,
        title=title,
        summary=summary,
        cover=cover,
        author=author,
        published_at=published_at,
        comments=tuple(comments),
        media_refs=tuple(deduped_media),
        evidence_state=evidence_state,
    )


def parse_onebot_share_card(payload: Any, segment_type: str | None = None) -> SharedContent:
    """Normalize OneBot ``json``, ``xml`` or ``share`` card payloads."""

    resolved_type = _clean_text(segment_type, 20).lower()
    if isinstance(payload, Mapping):
        resolved_type = _clean_text(payload.get("type"), 20).lower() or resolved_type
    if resolved_type == "xml" or (not resolved_type and str(payload or "").lstrip().startswith("<")):
        return parse_xml_share_card(payload)
    if resolved_type in {"json", "share", ""}:
        return parse_json_share_card(payload)
    return SharedContent()


@dataclass(frozen=True, slots=True)
class ForwardSender:
    user_id: str = ""
    nickname: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"user_id": self.user_id, "nickname": self.nickname}


@dataclass(frozen=True, slots=True)
class ForwardNode:
    source_path: tuple[str, ...]
    node_id: str = ""
    sender: ForwardSender = ForwardSender()
    sent_at: str = ""
    text: str = ""
    media_refs: tuple[SharedMediaRef, ...] = ()
    shared_contents: tuple[SharedContent, ...] = ()
    children: tuple["ForwardNode", ...] = ()
    available: bool = True
    unavailable_reason: str = ""
    trust: str = SHARED_CONTENT_TRUST

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": list(self.source_path),
            "node_id": self.node_id,
            "sender": self.sender.to_dict(),
            "sent_at": self.sent_at,
            "text": self.text,
            "media_refs": [item.to_dict() for item in self.media_refs],
            "shared_contents": [item.to_dict() for item in self.shared_contents],
            "children": [item.to_dict() for item in self.children],
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "trust": self.trust,
        }


@dataclass(frozen=True, slots=True)
class ForwardBundle:
    nodes: tuple[ForwardNode, ...]
    total_nodes: int
    clean_text_chars: int
    unavailable_nodes: int
    truncated: bool
    truncation_reasons: tuple[str, ...]
    trust: str = SHARED_CONTENT_TRUST

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [item.to_dict() for item in self.nodes],
            "total_nodes": self.total_nodes,
            "clean_text_chars": self.clean_text_chars,
            "unavailable_nodes": self.unavailable_nodes,
            "truncated": self.truncated,
            "truncation_reasons": list(self.truncation_reasons),
            "trust": self.trust,
        }


@dataclass(slots=True)
class _ForwardState:
    nodes: int = 0
    text_chars: int = 0
    unavailable: int = 0
    truncation_reasons: list[str] | None = None

    def __post_init__(self) -> None:
        if self.truncation_reasons is None:
            self.truncation_reasons = []

    def truncate(self, reason: str) -> None:
        assert self.truncation_reasons is not None
        if reason not in self.truncation_reasons:
            self.truncation_reasons.append(reason)

    def reserve_node(self) -> bool:
        if self.nodes >= MAX_FORWARD_NODES:
            self.truncate("max_nodes_exceeded")
            return False
        self.nodes += 1
        return True

    def consume_text(self, value: Any) -> str:
        text = _clean_text(value)
        remaining = MAX_FORWARD_TEXT_CHARS - self.text_chars
        if remaining <= 0:
            if text:
                self.truncate("max_text_chars_exceeded")
            return ""
        if len(text) > remaining:
            text = text[:remaining]
            self.truncate("max_text_chars_exceeded")
        self.text_chars += len(text)
        return text


def _forward_sender(*values: Any) -> ForwardSender:
    candidates = [_mapping(value) for value in values if isinstance(value, Mapping)]
    for candidate in candidates:
        sender = _mapping(candidate.get("sender")) or candidate
        user_id = _clean_text(_first_present(sender, "user_id", "userId", "uin", "id"), 80)
        nickname = _clean_text(_first_present(sender, "nickname", "name", "display_name"), 120)
        if user_id or nickname:
            return ForwardSender(user_id=user_id, nickname=nickname)
    return ForwardSender()


def _bound_shared_content(content: SharedContent, state: _ForwardState) -> SharedContent:
    title = state.consume_text(content.title)
    summary = state.consume_text(content.summary)
    author = state.consume_text(content.author)
    published = state.consume_text(content.published_at)
    comments: list[SharedComment] = []
    for item in content.comments:
        text = state.consume_text(item.text)
        comment_author = state.consume_text(item.author)
        comment_time = state.consume_text(item.published_at)
        if text:
            comments.append(replace(item, text=text, author=comment_author, published_at=comment_time))
    evidence_state = content.evidence_state
    if content.available and not (content.canonical_url or title or summary or content.cover or comments):
        evidence_state = "partial"
    return replace(
        content,
        title=title,
        summary=summary,
        author=author,
        published_at=published,
        comments=tuple(comments),
        evidence_state=evidence_state,
    )


class _ForwardNormalizer:
    def __init__(self) -> None:
        self.state = _ForwardState()

    def normalize(self, payload: Any) -> ForwardBundle:
        nodes = self._normalize_entries(payload, depth=1, parent_path=())
        if not nodes and payload not in (None, "", [], (), {}):
            unavailable = self._unavailable_node(("0",), "forward_content_unavailable")
            nodes = [unavailable] if unavailable is not None else []
        reasons = tuple(self.state.truncation_reasons or ())
        return ForwardBundle(
            nodes=tuple(nodes),
            total_nodes=self.state.nodes,
            clean_text_chars=self.state.text_chars,
            unavailable_nodes=self.state.unavailable,
            truncated=bool(reasons),
            truncation_reasons=reasons,
        )

    @staticmethod
    def _payload_entries(payload: Any) -> list[Any]:
        if isinstance(payload, Mapping):
            data = _mapping(payload.get("data"))
            for source in (payload, data):
                for key in ("messages", "nodes"):
                    value = source.get(key)
                    if isinstance(value, (list, tuple)):
                        return list(value)
            payload_type = _clean_text(payload.get("type"), 20).lower()
            if payload_type in {"node", "forward"} or any(key in payload for key in ("message", "content", "sender")):
                return [payload]
            return []
        if isinstance(payload, (list, tuple)):
            values = list(payload)
            if not values:
                return []
            if all(
                isinstance(item, Mapping)
                and (
                    _clean_text(item.get("type"), 20).lower() in {"node", "forward"}
                    or any(key in item for key in ("message", "content", "sender"))
                )
                for item in values
            ):
                return values
            return [{"content": values}]
        if isinstance(payload, str):
            return [{"content": payload}]
        return []

    def _normalize_entries(self, payload: Any, *, depth: int, parent_path: tuple[str, ...]) -> list[ForwardNode]:
        if depth > MAX_FORWARD_DEPTH:
            self.state.truncate("max_depth_exceeded")
            return []
        output: list[ForwardNode] = []
        for index, raw in enumerate(self._payload_entries(payload)):
            if self.state.nodes >= MAX_FORWARD_NODES:
                self.state.truncate("max_nodes_exceeded")
                break
            path = (*parent_path, str(index))
            node = self._normalize_node(raw, depth=depth, source_path=path)
            if node is not None:
                output.append(node)
        return output

    def _unavailable_node(self, source_path: tuple[str, ...], reason: str, node_id: str = "") -> ForwardNode | None:
        if not self.state.reserve_node():
            return None
        self.state.unavailable += 1
        return ForwardNode(
            source_path=source_path,
            node_id=node_id,
            available=False,
            unavailable_reason=reason,
        )

    def _normalize_node(self, raw: Any, *, depth: int, source_path: tuple[str, ...]) -> ForwardNode | None:
        if not isinstance(raw, Mapping):
            return self._unavailable_node(source_path, "forward_node_invalid")
        if not self.state.reserve_node():
            return None
        raw_type = _clean_text(raw.get("type"), 20).lower()
        data = _mapping(raw.get("data")) if raw_type in {"node", "forward"} else raw
        node_id = _clean_text(_first_present(data, "id", "message_id", "messageId", "resid"), 160)
        sender = _forward_sender(data, raw)
        sent_at = _clean_text(
            _first_present(data, "time", "timestamp", "sent_at", "sentAt")
            or _first_present(raw, "time", "timestamp", "sent_at", "sentAt"),
            80,
        )
        content = _first_present(data, "content", "message", "messages", "nodes")
        if content is None and data is not raw:
            content = _first_present(raw, "content", "message", "messages", "nodes")
        if content is None:
            self.state.unavailable += 1
            return ForwardNode(
                source_path=source_path,
                node_id=node_id,
                sender=sender,
                sent_at=sent_at,
                available=False,
                unavailable_reason="forward_content_unavailable",
            )

        text, media_refs, shared_contents, children, readable = self._normalize_content(
            content,
            depth=depth,
            source_path=source_path,
        )
        if not readable:
            self.state.unavailable += 1
        return ForwardNode(
            source_path=source_path,
            node_id=node_id,
            sender=sender,
            sent_at=sent_at,
            text=text,
            media_refs=tuple(media_refs),
            shared_contents=tuple(shared_contents),
            children=tuple(children),
            available=readable,
            unavailable_reason="" if readable else "forward_content_unavailable",
        )

    def _normalize_content(
        self,
        content: Any,
        *,
        depth: int,
        source_path: tuple[str, ...],
    ) -> tuple[str, list[SharedMediaRef], list[SharedContent], list[ForwardNode], bool]:
        if isinstance(content, str):
            text = self.state.consume_text(content)
            return text, [], [], [], bool(text)
        if isinstance(content, Mapping):
            segments = [content]
        elif isinstance(content, (list, tuple)):
            segments = list(content)
        else:
            return "", [], [], [], False

        text_parts: list[str] = []
        media_refs: list[SharedMediaRef] = []
        shared_contents: list[SharedContent] = []
        children: list[ForwardNode] = []
        readable = False
        for index, segment in enumerate(segments[:512]):
            segment_path = (*source_path, f"segment:{index}")
            if isinstance(segment, str):
                cleaned = self.state.consume_text(segment)
                if cleaned:
                    text_parts.append(cleaned)
                    readable = True
                continue
            if not isinstance(segment, Mapping):
                continue
            segment_type = _clean_text(segment.get("type"), 20).lower()
            data = _mapping(segment.get("data"))
            if segment_type == "text":
                cleaned = self.state.consume_text(data.get("text"))
                if cleaned:
                    text_parts.append(cleaned)
                    readable = True
                continue
            if segment_type in {"node", "forward"}:
                if depth >= MAX_FORWARD_DEPTH:
                    self.state.truncate("max_depth_exceeded")
                    continue
                nested = self._normalize_entries(segment, depth=depth + 1, parent_path=segment_path)
                if nested:
                    children.extend(nested)
                    readable = True
                continue
            if segment_type in {"json", "xml", "share"}:
                shared = _bound_shared_content(parse_onebot_share_card(segment), self.state)
                shared_contents.append(shared)
                readable = readable or shared.available
                continue
            if segment_type in {"image", "mface", "gif", "video", "record", "audio", "file"}:
                kind = {
                    "record": "audio",
                    "mface": "image",
                    "gif": "image",
                }.get(segment_type, segment_type)
                ref = _media_scalar(_first_present(data, *_MEDIA_REF_KEYS))
                if ref:
                    media_refs.append(
                        SharedMediaRef(kind, ref, f"forward.{segment_type}", segment_path)
                    )
                    readable = True
                continue
            fallback_text = self.state.consume_text(data.get("text"))
            if fallback_text:
                text_parts.append(fallback_text)
                readable = True
        return " ".join(text_parts), media_refs, shared_contents, children, readable


def normalize_merged_forward(payload: Any) -> ForwardBundle:
    """Normalize expanded OneBot merged-forward nodes under hard global bounds.

    Opaque ``id``/``resid`` nodes are not fetched here.  They are returned as
    ``available=False`` with ``forward_content_unavailable`` so callers cannot
    pretend that the nested content was understood.
    """

    return _ForwardNormalizer().normalize(payload)


class ShortLinkSafetyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ShortLinkHop:
    url: str
    resolved_ips: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidatedShortLinkHop:
    url: str
    canonical_url: str
    scheme: str
    host: str
    resolved_ips: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ShortLinkSafetyPolicy:
    """Fail-closed redirect policy intended for integration with ``web_fetch``.

    The caller resolves the actual destination immediately before each request
    and passes every returned address to :meth:`validate_hop`.  A redirect URL
    must be resolved and validated again; approval of the first hop never
    carries over to a later hop.
    """

    max_redirects: int = MAX_SHORT_LINK_REDIRECTS
    allowed_schemes: tuple[str, ...] = ("http", "https")
    require_resolved_ips: bool = True
    blocked_hosts: tuple[str, ...] = (
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
    )
    blocked_host_suffixes: tuple[str, ...] = (".localhost", ".local", ".internal")

    def __post_init__(self) -> None:
        if isinstance(self.max_redirects, bool) or not 0 <= int(self.max_redirects) <= MAX_SHORT_LINK_REDIRECTS:
            raise ValueError("max_redirects must be between 0 and 5")
        schemes = tuple(str(item or "").strip().lower() for item in self.allowed_schemes)
        if not schemes or any(item not in {"http", "https"} for item in schemes):
            raise ValueError("allowed_schemes may only contain http/https")
        object.__setattr__(self, "allowed_schemes", schemes)

    @staticmethod
    def _public_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        try:
            address = ipaddress.ip_address(str(value or "").strip().strip("[]"))
        except ValueError as exc:
            raise ShortLinkSafetyError("invalid_resolved_ip", "resolved address is not an IP literal") from exc
        if not address.is_global:
            raise ShortLinkSafetyError("non_public_address", "target resolves to a non-public address")
        return address

    def validate_hop(self, url: str, resolved_ips: Iterable[str] | None = None) -> ValidatedShortLinkHop:
        raw = html.unescape(str(url or "")).strip()
        if not raw or len(raw) > 4_096:
            raise ShortLinkSafetyError("invalid_url", "URL is empty or too long")
        try:
            parsed = urlsplit(raw)
            scheme = parsed.scheme.lower()
            host = str(parsed.hostname or "").lower().rstrip(".")
            port = parsed.port
        except ValueError as exc:
            raise ShortLinkSafetyError("invalid_url", "URL cannot be parsed") from exc
        if scheme not in self.allowed_schemes:
            raise ShortLinkSafetyError("scheme_not_allowed", "only HTTP(S) links are allowed")
        if not host:
            raise ShortLinkSafetyError("host_missing", "URL has no host")
        if parsed.username is not None or parsed.password is not None:
            raise ShortLinkSafetyError("credentials_not_allowed", "URL credentials are not allowed")
        if port is not None and not 1 <= port <= 65_535:
            raise ShortLinkSafetyError("invalid_port", "URL port is outside the valid range")
        if host in self.blocked_hosts or any(host.endswith(suffix) for suffix in self.blocked_host_suffixes):
            raise ShortLinkSafetyError("host_not_allowed", "local or metadata host is not allowed")

        supplied_ips = tuple(dict.fromkeys(_clean_text(item, 80) for item in (resolved_ips or ()) if _clean_text(item, 80)))
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        if literal is not None:
            self._public_ip(str(literal))
            if not supplied_ips:
                supplied_ips = (str(literal),)
        elif self.require_resolved_ips and not supplied_ips:
            raise ShortLinkSafetyError("dns_resolution_required", "hostname must be resolved before access")
        for address in supplied_ips:
            self._public_ip(address)

        canonical = _canonical_http_url(raw)
        if not canonical:
            raise ShortLinkSafetyError("invalid_url", "URL cannot be canonicalized")
        return ValidatedShortLinkHop(
            url=raw[:2_048],
            canonical_url=canonical,
            scheme=scheme,
            host=host,
            resolved_ips=supplied_ips,
        )

    def validate_redirect_chain(self, hops: Sequence[ShortLinkHop]) -> tuple[ValidatedShortLinkHop, ...]:
        if not hops:
            raise ShortLinkSafetyError("redirect_chain_empty", "redirect chain is empty")
        redirect_count = len(hops) - 1
        if redirect_count > self.max_redirects:
            raise ShortLinkSafetyError("too_many_redirects", "redirect count exceeds the configured limit")
        return tuple(self.validate_hop(hop.url, hop.resolved_ips) for hop in hops)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_redirects": self.max_redirects,
            "allowed_schemes": list(self.allowed_schemes),
            "require_resolved_ips": self.require_resolved_ips,
            "validate_every_hop": True,
            "allow_login_bypass": False,
            "allow_captcha_bypass": False,
        }


__all__ = [
    "ForwardBundle",
    "ForwardNode",
    "ForwardSender",
    "MAX_FORWARD_DEPTH",
    "MAX_FORWARD_NODES",
    "MAX_FORWARD_TEXT_CHARS",
    "MAX_SHARED_COMMENT_CHARS",
    "MAX_SHARED_COMMENTS",
    "MAX_SHARED_COMMENTS_TOTAL_CHARS",
    "MAX_SHORT_LINK_REDIRECTS",
    "SHARED_CONTENT_TRUST",
    "SharedComment",
    "SharedContent",
    "SharedMediaRef",
    "ShortLinkHop",
    "ShortLinkSafetyError",
    "ShortLinkSafetyPolicy",
    "ValidatedShortLinkHop",
    "canonicalize_shared_url",
    "normalize_merged_forward",
    "parse_json_share_card",
    "parse_onebot_share_card",
    "parse_xml_share_card",
]
