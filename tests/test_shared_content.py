"""Pure shared-card, merged-forward and short-link safety contracts."""
from __future__ import annotations

import json

import pytest

from ._loader import load_personification_module


shared = load_personification_module("plugin.personification.core.shared_content")


def _json_segment(payload: dict) -> dict:
    return {"type": "json", "data": {"data": json.dumps(payload, ensure_ascii=False)}}


def _text_node(node_id: str, text: str, *, sender: str = "10001") -> dict:
    return {
        "id": node_id,
        "sender": {"user_id": sender, "nickname": f"用户{sender}"},
        "time": "1720000000",
        "message": [{"type": "text", "data": {"text": text}}],
    }


def test_json_share_card_normalizes_full_contract() -> None:
    payload = {
        "prompt": "[QQ小程序]示例动态",
        "meta": {
            "detail": {
                "jumpUrl": "https://x.com/example/status/1?from=qq#fragment",
                "desc": "  一段\n摘要  ",
                "preview": "https://cdn.example.com/cover.jpg",
                "author": "作者甲",
                "publishedAt": "2026-08-24T10:00:00+08:00",
                "comments": [
                    {
                        "content": "第一条评论",
                        "sender": {"nickname": "评论者"},
                        "time": "2026-08-24T10:01:00+08:00",
                    }
                ],
                "video": {"url": "https://cdn.example.com/video.mp4"},
            }
        },
    }

    result = shared.parse_onebot_share_card(_json_segment(payload))

    assert isinstance(result, shared.SharedContent)
    assert result.platform == "x"
    assert result.original_url.endswith("#fragment")
    assert result.canonical_url == "https://x.com/example/status/1?from=qq"
    assert result.title == "示例动态"
    assert result.summary == "一段 摘要"
    assert result.cover == "https://cdn.example.com/cover.jpg"
    assert result.author == "作者甲"
    assert result.published_at == "2026-08-24T10:00:00+08:00"
    assert result.evidence_state == "metadata_only"
    assert result.trust == "untrusted_data_only"
    assert result.comments[0].to_dict() == {
        "text": "第一条评论",
        "author": "评论者",
        "published_at": "2026-08-24T10:01:00+08:00",
        "trust": "untrusted_data_only",
    }
    assert {(item.kind, item.ref) for item in result.media_refs} >= {
        ("image", "https://cdn.example.com/cover.jpg"),
        ("video", "https://cdn.example.com/video.mp4"),
    }


def test_native_onebot_share_segment_is_supported() -> None:
    result = shared.parse_onebot_share_card(
        {
            "type": "share",
            "data": {
                "url": "https://mp.weixin.qq.com/s/example#wechat_redirect",
                "title": "公众号文章",
                "content": "文章摘要",
                "image": "https://mmbiz.qpic.cn/cover.jpg",
            },
        }
    )

    assert result.platform == "wechat_official"
    assert result.canonical_url == "https://mp.weixin.qq.com/s/example"
    assert result.title == "公众号文章"
    assert result.summary == "文章摘要"
    assert result.cover == "https://mmbiz.qpic.cn/cover.jpg"


def test_xml_share_card_preserves_metadata_comments_and_media() -> None:
    xml = """
    <msg url="https://mp.weixin.qq.com/s/abc#part" published_at="2026-08-24">
      <item>
        <title>测试文章</title>
        <summary> XML 摘要 </summary>
        <picture cover="https://mmbiz.qpic.cn/abc.jpg" />
      </item>
      <source name="作者乙" />
      <comment author="读者" time="2026-08-24T11:00:00+08:00">公开评论</comment>
      <video url="https://media.example.com/a.mp4" />
    </msg>
    """

    result = shared.parse_onebot_share_card({"type": "xml", "data": {"data": xml}})

    assert result.platform == "wechat_official"
    assert result.canonical_url == "https://mp.weixin.qq.com/s/abc"
    assert result.title == "测试文章"
    assert result.summary == "XML 摘要"
    assert result.author == "作者乙"
    assert result.published_at == "2026-08-24"
    assert result.comments[0].text == "公开评论"
    assert result.comments[0].trust == "untrusted_data_only"
    assert {(item.kind, item.ref) for item in result.media_refs} >= {
        ("image", "https://mmbiz.qpic.cn/abc.jpg"),
        ("video", "https://media.example.com/a.mp4"),
    }


@pytest.mark.parametrize(
    "payload,segment_type",
    [
        ("not-json", "json"),
        ("<msg><broken>", "xml"),
        ('<!DOCTYPE msg [<!ENTITY x "secret">]><msg>&x;</msg>', "xml"),
        ("[" * 2_000 + "{}" + "]" * 2_000, "json"),
        ("", "json"),
    ],
)
def test_bad_or_entity_xml_card_is_explicitly_unavailable(payload: str, segment_type: str) -> None:
    result = shared.parse_onebot_share_card(payload, segment_type)
    assert result.available is False
    assert result.evidence_state == "unavailable"
    assert result.to_dict()["trust"] == "untrusted_data_only"


def test_share_comments_are_bounded_and_always_untrusted() -> None:
    comments = [
        {"content": f"{index}:" + ("评" * 500), "author": f"作者{index}"}
        for index in range(30)
    ]
    result = shared.parse_json_share_card(
        {
            "url": "https://weibo.com/123/456",
            "title": "微博动态",
            "comments": comments,
        }
    )

    assert len(result.comments) == 20
    assert all(len(item.text) <= 200 for item in result.comments)
    assert sum(len(item.text) for item in result.comments) <= 4_000
    assert all(item.trust == "untrusted_data_only" for item in result.comments)


def test_url_canonicalization_rejects_credentials_and_non_http_schemes() -> None:
    assert shared.canonicalize_shared_url("HTTPS://Example.COM:443/a?q=1#part") == "https://example.com/a?q=1"
    assert shared.canonicalize_shared_url("https://user:pass@example.com/a") == ""
    assert shared.canonicalize_shared_url("file:///etc/passwd") == ""


def test_forward_normalizer_preserves_nested_sources_sender_time_and_media() -> None:
    nested_card = _json_segment(
        {
            "url": "https://weibo.com/1/2#share",
            "title": "嵌套分享",
            "summary": "分享摘要",
        }
    )
    payload = {
        "messages": [
            {
                "id": "root-node",
                "sender": {"user_id": "100", "nickname": "根发送者"},
                "time": "1720000000",
                "message": [
                    {"type": "text", "data": {"text": "根文本"}},
                    {"type": "image", "data": {"url": "https://img.example.com/root.jpg"}},
                    nested_card,
                    {
                        "type": "node",
                        "data": {
                            "id": "child-node",
                            "uin": "200",
                            "name": "子发送者",
                            "time": "1720000001",
                            "content": [
                                {"type": "text", "data": {"text": "子文本"}},
                                {"type": "video", "data": {"url": "https://media.example.com/a.mp4"}},
                                {
                                    "type": "node",
                                    "data": {
                                        "id": "grandchild-node",
                                        "content": [{"type": "text", "data": {"text": "孙文本"}}],
                                    },
                                },
                            ],
                        },
                    },
                ],
            }
        ]
    }

    bundle = shared.normalize_merged_forward(payload)

    assert bundle.total_nodes == 3
    assert bundle.unavailable_nodes == 0
    assert bundle.truncated is False
    root = bundle.nodes[0]
    assert root.source_path == ("0",)
    assert root.node_id == "root-node"
    assert root.sender.user_id == "100"
    assert root.sender.nickname == "根发送者"
    assert root.sent_at == "1720000000"
    assert root.text == "根文本"
    assert root.media_refs[0].source_path == ("0", "segment:1")
    assert root.shared_contents[0].platform == "weibo"
    assert root.shared_contents[0].trust == "untrusted_data_only"

    child = root.children[0]
    assert child.source_path == ("0", "segment:3", "0")
    assert child.sender == shared.ForwardSender(user_id="200", nickname="子发送者")
    assert child.text == "子文本"
    assert child.media_refs[0].kind == "video"
    assert child.media_refs[0].source_path == ("0", "segment:3", "0", "segment:1")
    assert child.children[0].node_id == "grandchild-node"
    assert child.children[0].text == "孙文本"


def test_opaque_forward_node_is_not_treated_as_understood() -> None:
    bundle = shared.normalize_merged_forward(
        {"messages": [{"type": "node", "data": {"id": "opaque-resid-only"}}]}
    )

    assert bundle.total_nodes == 1
    assert bundle.unavailable_nodes == 1
    assert bundle.nodes[0].available is False
    assert bundle.nodes[0].unavailable_reason == "forward_content_unavailable"
    assert bundle.nodes[0].node_id == "opaque-resid-only"


def test_malformed_forward_payload_is_explicitly_unavailable() -> None:
    bundle = shared.normalize_merged_forward({"unexpected": "shape"})
    assert bundle.total_nodes == 1
    assert bundle.nodes[0].available is False
    assert bundle.nodes[0].unavailable_reason == "forward_content_unavailable"


def test_forward_depth_is_capped_at_three_levels() -> None:
    level4 = {"type": "node", "data": {"id": "l4", "content": [{"type": "text", "data": {"text": "不应进入"}}]}}
    level3 = {"type": "node", "data": {"id": "l3", "content": [level4]}}
    level2 = {"type": "node", "data": {"id": "l2", "content": [level3]}}
    level1 = {"type": "node", "data": {"id": "l1", "content": [level2]}}

    bundle = shared.normalize_merged_forward([level1])

    assert bundle.total_nodes == 3
    assert bundle.truncated is True
    assert "max_depth_exceeded" in bundle.truncation_reasons
    assert bundle.nodes[0].children[0].children[0].node_id == "l3"
    assert bundle.nodes[0].children[0].children[0].children == ()


def test_forward_node_count_is_capped_at_fifty() -> None:
    bundle = shared.normalize_merged_forward([_text_node(str(index), str(index)) for index in range(75)])
    assert bundle.total_nodes == 50
    assert len(bundle.nodes) == 50
    assert bundle.truncated is True
    assert "max_nodes_exceeded" in bundle.truncation_reasons


def test_forward_clean_text_budget_is_global_and_hard_capped() -> None:
    bundle = shared.normalize_merged_forward(
        [
            _text_node("one", "甲" * 15_000),
            _text_node("two", "乙" * 15_000),
        ]
    )

    rendered_text = "".join(node.text for node in bundle.nodes)
    assert len(rendered_text) == 20_000
    assert bundle.clean_text_chars == 20_000
    assert bundle.truncated is True
    assert "max_text_chars_exceeded" in bundle.truncation_reasons


def test_forward_card_text_also_consumes_global_text_budget() -> None:
    card = _json_segment(
        {
            "url": "https://example.com/a",
            "title": "题" * 300,
            "summary": "摘" * 1_000,
            "comments": [{"content": "评" * 200} for _ in range(20)],
        }
    )
    node = {
        "id": "card-node",
        "message": [card, {"type": "text", "data": {"text": "后" * 19_000}}],
    }
    bundle = shared.normalize_merged_forward([node])

    root = bundle.nodes[0]
    card_chars = len(root.shared_contents[0].title) + len(root.shared_contents[0].summary)
    card_chars += sum(len(item.text) for item in root.shared_contents[0].comments)
    assert card_chars + len(root.text) == 20_000
    assert bundle.clean_text_chars == 20_000
    assert "max_text_chars_exceeded" in bundle.truncation_reasons


def test_short_link_policy_allows_at_most_five_fully_validated_redirects() -> None:
    policy = shared.ShortLinkSafetyPolicy()
    hops = tuple(
        shared.ShortLinkHop(f"https://example.com/{index}#fragment", ("8.8.8.8",))
        for index in range(6)
    )

    validated = policy.validate_redirect_chain(hops)

    assert len(validated) == 6
    assert validated[-1].canonical_url == "https://example.com/5"
    assert policy.to_dict() == {
        "max_redirects": 5,
        "allowed_schemes": ["http", "https"],
        "require_resolved_ips": True,
        "validate_every_hop": True,
        "allow_login_bypass": False,
        "allow_captcha_bypass": False,
    }


def test_short_link_policy_rejects_more_than_five_redirects() -> None:
    policy = shared.ShortLinkSafetyPolicy()
    hops = tuple(shared.ShortLinkHop(f"https://example.com/{index}", ("8.8.8.8",)) for index in range(7))
    with pytest.raises(shared.ShortLinkSafetyError) as exc_info:
        policy.validate_redirect_chain(hops)
    assert exc_info.value.code == "too_many_redirects"


@pytest.mark.parametrize(
    "url,resolved_ips,code",
    [
        ("file:///etc/passwd", (), "scheme_not_allowed"),
        ("https://user:pass@example.com/a", ("8.8.8.8",), "credentials_not_allowed"),
        ("https://localhost/a", ("127.0.0.1",), "host_not_allowed"),
        ("https://127.0.0.1/a", (), "non_public_address"),
        ("https://169.254.169.254/latest/meta-data", (), "non_public_address"),
        ("https://example.com/a", (), "dns_resolution_required"),
        ("https://example.com/a", ("not-an-ip",), "invalid_resolved_ip"),
        ("https://example.com/a", ("8.8.8.8", "10.0.0.1"), "non_public_address"),
    ],
)
def test_short_link_policy_rechecks_scheme_host_and_every_resolved_address(
    url: str,
    resolved_ips: tuple[str, ...],
    code: str,
) -> None:
    with pytest.raises(shared.ShortLinkSafetyError) as exc_info:
        shared.ShortLinkSafetyPolicy().validate_hop(url, resolved_ips)
    assert exc_info.value.code == code


def test_redirect_to_private_host_is_rejected_even_after_public_first_hop() -> None:
    hops = (
        shared.ShortLinkHop("https://example.com/start", ("8.8.8.8",)),
        shared.ShortLinkHop("http://10.0.0.8/internal", ()),
    )
    with pytest.raises(shared.ShortLinkSafetyError) as exc_info:
        shared.ShortLinkSafetyPolicy().validate_redirect_chain(hops)
    assert exc_info.value.code == "non_public_address"


def test_short_link_policy_hard_limit_cannot_be_raised_above_five() -> None:
    with pytest.raises(ValueError, match="between 0 and 5"):
        shared.ShortLinkSafetyPolicy(max_redirects=6)
