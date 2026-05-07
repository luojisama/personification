#!/usr/bin/env python3
"""
QQ 空间子评论接口诊断脚本（纯标准库，无需安装依赖）
在 bot 根目录（含 .env.prod 的那一层）运行：
    python personification/scripts/qzone_diag.py
或在 personification 目录内运行：
    python scripts/qzone_diag.py
"""
import asyncio
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path


# ── 从 .env.prod / .env 读取 cookie ─────────────────────────────────────────
def _load_cookie_from_env() -> str:
    script_path = Path(__file__).resolve()
    # 从脚本位置向上最多搜索 6 层目录
    ancestors = [script_path.parents[i] for i in range(min(6, len(script_path.parents)))]
    search_dirs = list(dict.fromkeys([Path.cwd()] + ancestors))  # 去重，cwd 优先
    print(f"[cookie] 搜索目录: {[str(d) for d in search_dirs]}")
    for d in search_dirs:
        for name in (".env.prod", ".env"):
            path = d / name
            if not path.exists():
                continue
            print(f"[cookie] 找到配置文件: {path}")
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("personification_qzone_cookie"):
                    _, _, value = line.partition("=")
                    cookie = value.strip().strip('"').strip("'")
                    if cookie:
                        print(f"[cookie] 成功读取自 {path}")
                        return cookie
            print(f"[cookie] {path} 中未找到 personification_qzone_cookie 字段")
    return ""


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


def _http_get(url: str, params: dict, headers: dict) -> tuple[int, str]:
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body


def _http_post(url: str, params: dict, data: dict, headers: dict) -> tuple[int, str]:
    full_url = url + "?" + urllib.parse.urlencode(params)
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(full_url, data=encoded, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body


def main() -> None:
    cookie = _load_cookie_from_env()
    if not cookie:
        print("ERROR: 未找到 personification_qzone_cookie，请确认 .env.prod 已配置")
        sys.exit(1)

    pskey_m = re.search(r"p_skey=([^; ]+)", cookie)
    if not pskey_m:
        print("ERROR: cookie 中找不到 p_skey")
        sys.exit(1)
    p_skey = pskey_m.group(1)

    skey_m = re.search(r"(?<![a-zA-Z_])skey=([^; ]+)", cookie)
    skey = skey_m.group(1) if skey_m else ""

    uin_m = re.search(r"uin=[o0]*(\d+)", cookie)
    QQ = uin_m.group(1) if uin_m else ""
    if not QQ:
        print("ERROR: cookie 中找不到 uin")
        sys.exit(1)

    g_tk = _g_tk(p_skey)
    g_tk_skey = _g_tk(skey) if skey else 0
    print(f"QQ={QQ}")
    print(f"g_tk(p_skey)={g_tk}  g_tk(skey)={g_tk_skey}  skey存在={bool(skey)}")

    base_headers = {
        "Cookie": cookie,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": f"https://user.qzone.qq.com/{QQ}",
        "Origin": "https://user.qzone.qq.com",
    }

    # ── Step 1: 获取动态列表 ─────────────────────────────────────────────────
    print("\n=== Step 1: 获取动态列表 ===")
    status, text = _http_get(
        "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6",
        params={
            "uin": QQ, "ftype": "0", "sort": "0", "pos": "0",
            "num": "5", "replynum": "10",
            "g_tk": str(g_tk), "callback": "_Cb",
            "code_version": "1", "format": "jsonp", "need_private_comment": "1",
        },
        headers=base_headers,
    )
    print(f"HTTP {status}")
    data = _parse_jsonp(text)
    code = data.get("code", data.get("ret", "?"))
    print(f"API code={code}  message={data.get('message', '')}")
    if code != 0:
        print("Cookie 可能已过期，请重新获取后更新 .env.prod")
        print("原始响应:", text[:300])
        return

    msglist = data.get("msglist", [])
    print(f"获取到 {len(msglist)} 条动态")

    test_feed = test_comment = None
    # 选取目标评论：优先选他人发的（uin != QQ），避免选到自己之前发的诊断测试评论
    for feed in msglist:
        cl = feed.get("commentlist", [])
        for c in cl:
            if str(c.get("uin", "")) != QQ and "[诊断" not in str(c.get("content", "")):
                test_feed = feed
                test_comment = c
                break
        if test_feed:
            break
    # 兜底：找任意有评论的动态
    if not test_feed:
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

    print(f"\n选取的动态: uin={feed_uin} tid={feed_tid} appid={appid}")
    print(f"  topicId(API 返回)={topic_id_api!r}")
    print(f"  topicId(计算值) ={feed_uin}_{feed_tid}__1")
    print(f"  实际使用: topicId={topic_id!r}")

    # 打印动态对象的所有顶层字段，便于排查
    print("\n动态对象顶层字段:")
    for k, v in test_feed.items():
        if k == "commentlist":
            print(f"  {k}: <list len={len(v)}>")
            continue
        sv = repr(v)
        if len(sv) > 120:
            sv = sv[:120] + "..."
        print(f"  {k} = {sv}")

    print("\n评论对象完整结构:")
    print(json.dumps(test_comment, ensure_ascii=False, indent=2)[:1500])

    # 评论 ID 字段尝试候选：tid / commentid / id
    candidate_id_fields = ("commentid", "commentId", "id", "tid")
    c_id = ""
    c_id_field = ""
    for field in candidate_id_fields:
        v = test_comment.get(field)
        if v not in (None, "", 0, "0"):
            c_id = str(v)
            c_id_field = field
            break
    c_uin = str(test_comment.get("uin", ""))
    c_nick = str(test_comment.get("name") or test_comment.get("nickname") or "")
    print(f"\n评论 ID 选取: 字段={c_id_field!r} 值={c_id!r}")
    print(f"评论 uin={c_uin}  nick={c_nick!r}")

    if not c_id or not c_uin:
        print("评论缺少 ID/uin，无法测试")
        return

    # ── Step 2: 扫描子回复候选端点 ────────────────────────────────────────────
    post_headers = {**base_headers, "Content-Type": "application/x-www-form-urlencoded"}

    base_post = {
        "uin": QQ,
        "hostUin": feed_uin,
        "appid": appid,
        "topicId": topic_id,
        "commentUin": c_uin,
        "commentTid": c_id,
        "replyUin": c_uin,
        "replyNick": c_nick,
        "replyId": c_id,
        "commentId": c_id,
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

    # ── 对照实验：故意不存在的 cgi 名，看是否返回相同 -3000 ─────────────────
    print("\n=== [对照] 故意不存在的 cgi 名 emotion_cgi_NOTEXIST_xxxxx ===")
    bogus_url = "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_NOTEXIST_xxxxx"
    try:
        s, t = _http_post(bogus_url, params={"g_tk": str(g_tk)}, data=base_post, headers=post_headers)
        print(f"HTTP {s}  响应: {t[:200] if t else '<空>'}")
        d = _parse_jsonp(t)
        print(f"对照 code={d.get('code', 'N/A')} message={d.get('message','')} subcode={d.get('subcode','')}")
        print("→ 若与 addreply_v6 返回完全相同，说明 addreply_v6 也不存在；否则说明它真实存在。")
    except Exception as exc:
        print(f"对照异常: {exc}")

    # ── 候选：H5 webapp 接口（QZone 移动 H5 实际使用的 RESTful 接口）─────────
    print("\n=== 扫描 h5.qzone.qq.com/webapp/json/... 候选接口 ===")
    h5_candidates = [
        "https://h5.qzone.qq.com/webapp/json/mqzone_main/replyComment",
        "https://h5.qzone.qq.com/webapp/json/mqzone_feedlist/replyComment",
        "https://h5.qzone.qq.com/webapp/json/mqzone_main/comment",
        "https://h5.qzone.qq.com/webapp/json/qzoneFeedV2/comment",
        "https://h5.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_addreply_v6",
    ]

    success_url = None
    for idx, url in enumerate(h5_candidates):
        print(f"\n--- [{idx + 1}/{len(h5_candidates)}] {url} ---")
        data = {**base_post, "content": f"[诊断 #{idx + 1}，请忽略]"}
        try:
            status, resp_text = _http_post(
                url, params={"g_tk": str(g_tk)}, data=data, headers=post_headers,
            )
            print(f"HTTP {status}")
            preview = (resp_text[:300] + "...") if len(resp_text) > 300 else resp_text
            print(f"响应: {preview if preview else '<空>'}")
            d = _parse_jsonp(resp_text)
            api_code = d.get("code", d.get("ret", "N/A"))
            msg = d.get("message", d.get("msg", ""))
            print(f"解析 code={api_code} message={msg}")
            if status == 200 and api_code == 0:
                success_url = url
                print(f"⭐ 命中！")
                break
        except Exception as exc:
            print(f"请求异常: {exc}")

    if not success_url:
        print("\n⚠️ 所有候选端点都失败，仍需进一步逆向")
        print("=== 诊断完成 ===")
        return

    # ── Step 3: 验证目标评论的 reply_num 是否增加 ────────────────────────────
    print(f"\n=== Step 3: 验证 {success_url} 是否产生了真正的子回复 ===")
    status2, text2 = _http_get(
        "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6",
        params={
            "uin": QQ, "ftype": "0", "sort": "0", "pos": "0",
            "num": "20", "replynum": "100",
            "g_tk": str(g_tk), "callback": "_Cb",
            "code_version": "1", "format": "jsonp", "need_private_comment": "1",
        },
        headers=base_headers,
    )
    data2 = _parse_jsonp(text2)
    msglist2 = data2.get("msglist", [])
    new_feed = next((f for f in msglist2 if str(f.get("tid", "")) == feed_tid), None)
    if not new_feed:
        print("未找到原动态")
        return
    new_comments = new_feed.get("commentlist", [])
    print(f"cmtnum={new_feed.get('cmtnum')}  commentlist len={len(new_comments)}")
    target_comment = next((c for c in new_comments if str(c.get("tid", "")) == c_id), None)
    if not target_comment:
        print(f"未在 commentlist 中找到 tid={c_id} 的评论；可能被分页隔开")
        return
    replies = (
        target_comment.get("replyList")
        or target_comment.get("list_3")
        or target_comment.get("replies")
        or target_comment.get("comment_list")
        or []
    )
    print(f"\n目标评论 reply_num={target_comment.get('reply_num')} 子回复列表长度={len(replies)}")
    if replies:
        print("✅ 子回复嵌套确认成功，端点和参数正确！")
        print("子回复完整结构样例:")
        print(json.dumps(replies[-1], ensure_ascii=False, indent=2)[:800])
        print("\n目标评论顶层字段:")
        for k in target_comment:
            print(f"  - {k}")
    else:
        print("⚠️ 目标评论 reply_num 仍为 0，没有产生嵌套；该端点也是顶级评论")
        print("评论结构：")
        print(json.dumps(target_comment, ensure_ascii=False, indent=2)[:800])

    print("\n=== 诊断完成 ===")


if __name__ == "__main__":
    main()
