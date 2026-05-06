#!/usr/bin/env python3
"""
QQ 空间子评论接口诊断脚本
在 bot 服务器上运行: python scripts/qzone_diag.py
"""
import asyncio
import json
import re
import sys

try:
    import httpx
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx"])
    import httpx  # type: ignore

# ─── 修改此处 ───────────────────────────────────────────────────────────────
COOKIE = (
    "pt2gguin=o3204586647; uin=o3204586647; skey=@9AFUa5wSp; "
    "pt_recent_uins=fb2cec2009a3eae9081f239a6bb19d77948a660ad2a9fd16f12c8fa56ec5b3114bb5930f292d59cec38d4b7834958d2b962bd39d91f12266; "
    "RK=9Lo3veFVyF; ptnick_3204586647=e799bde592b2e79c9fe5afbbe69cba; "
    "ptcz=abaaa54874c185f3673379a192efc8713e352b1dd920c02419e6544343f94d22; "
    "p_uin=o3204586647; pt4_token=7sPprwTfo*6lqGoHiIAZCaNqj0949NHjU-YRLWKW5lE_; "
    "p_skey=3ge8D1s-IV90zp*LxE8q9X32Y6YxpT9vQFrgQxIC42s_"
)
QQ = "3204586647"
# ────────────────────────────────────────────────────────────────────────────


def _g_tk(p_skey: str) -> int:
    h = 5381
    for c in p_skey:
        h += (h << 5) + ord(c)
    return h & 0x7FFFFFFF


def _parse_jsonp(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {}


async def main() -> None:
    pskey_m = re.search(r"p_skey=([^; ]+)", COOKIE)
    if not pskey_m:
        print("ERROR: cookie 中找不到 p_skey")
        return
    p_skey = pskey_m.group(1)
    g_tk = _g_tk(p_skey)
    print(f"QQ={QQ}  g_tk={g_tk}")

    pc_headers = {
        "Cookie": COOKIE,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://user.qzone.qq.com/{QQ}",
        "Origin": "https://user.qzone.qq.com",
    }

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:

        # ── Step 1: 获取动态列表 ───────────────────────────────────────────
        print("\n=== Step 1: 获取动态列表 ===")
        r = await client.get(
            "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6",
            params={
                "uin": QQ, "ftype": "0", "sort": "0", "pos": "0",
                "num": "5", "replynum": "10",
                "g_tk": str(g_tk), "callback": "_Cb",
                "code_version": "1", "format": "jsonp", "need_private_comment": "1",
            },
            headers=pc_headers,
        )
        print(f"HTTP {r.status_code}")
        data = _parse_jsonp(r.text)
        code = data.get("code", data.get("ret", "?"))
        print(f"API code={code}  message={data.get('message', '')}")
        if code != 0:
            print("Cookie 可能已过期，请刷新后重试")
            print("原始响应:", r.text[:300])
            return

        msglist = data.get("msglist", [])
        print(f"获取到 {len(msglist)} 条动态")

        # 找第一条有评论的动态
        test_feed = test_comment = None
        for feed in msglist:
            cl = feed.get("commentlist", [])
            if cl:
                test_feed = feed
                test_comment = cl[0]
                break

        if not test_feed or not test_comment:
            print("没有找到有评论的动态，无法测试子评论接口")
            return

        feed_uin = str(test_feed.get("uin", QQ))
        feed_tid = str(test_feed.get("tid", ""))
        appid = str(test_feed.get("appid", "311"))
        topic_id_api = str(test_feed.get("topicId") or test_feed.get("topicid") or "")
        topic_id = topic_id_api or f"{feed_uin}_{feed_tid}__1"
        c_tid = str(test_comment.get("tid", ""))
        c_uin = str(test_comment.get("uin", ""))
        c_nick = str(test_comment.get("nickname", ""))

        print(f"\n选取的动态: uin={feed_uin} tid={feed_tid} appid={appid}")
        print(f"  topicId(API)={topic_id_api!r}  computed={feed_uin}_{feed_tid}__1")
        print(f"  评论: uin={c_uin} tid={c_tid} nick={c_nick!r}")
        print(f"  将使用 topicId={topic_id!r}")

        # ── Step 2: 测试各子评论接口 ──────────────────────────────────────
        post_data = {
            "uin": QQ,
            "hostUin": feed_uin,
            "appid": appid,
            "topicId": topic_id,
            "replyId": c_tid,
            "commentId": c_tid,
            "replyUin": c_uin,
            "replyNick": c_nick,
            "content": "【测试子回复，请忽略】",
            "private": "0",
            "paramstr": "1",
            "format": "json",
            "inCharset": "utf-8",
            "outCharset": "utf-8",
            "plat": "qzone",
            "source": "ic",
            "ref": "feeds",
            "platformid": "52",
            "qzreferrer": f"https://user.qzone.qq.com/{feed_uin}",
        }
        post_headers = {**pc_headers, "Content-Type": "application/x-www-form-urlencoded"}

        endpoints = [
            "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_reply_v6",
            "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_reply_v6",
            "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_re_feeds",
        ]

        for url in endpoints:
            print(f"\n=== POST {url} ===")
            try:
                resp = await client.post(
                    url,
                    params={"g_tk": str(g_tk)},
                    data=post_data,
                    headers=post_headers,
                )
                print(f"HTTP {resp.status_code}")
                print(f"响应: {resp.text[:500]}")
                d = _parse_jsonp(resp.text)
                api_code = d.get("code", d.get("ret", "N/A"))
                print(f"解析 code={api_code}  msg={d.get('message', d.get('msg', ''))}")
            except Exception as exc:
                print(f"请求异常: {exc}")

        print("\n=== 诊断完成 ===")
        print("请把以上输出发给开发者以确定正确接口")


if __name__ == "__main__":
    asyncio.run(main())
