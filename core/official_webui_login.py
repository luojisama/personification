"""Browser-bound, passive delivery of WebUI codes through verified QQ links."""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class OfficialWebLogin:
    def __init__(self, path: str, legacy_app: str, *, clock=time.time):
        self.path, self.legacy_app, self.clock = path, legacy_app, clock
        if not path or not legacy_app or not os.path.isfile(path):
            raise ValueError('官方 QQ 登录服务尚未配置')
        with self.tx() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS official_web_login(
                ticket TEXT PRIMARY KEY, qq TEXT, browser TEXT, expires REAL,
                code TEXT, attempts INTEGER DEFAULT 0, app TEXT, subject TEXT)''')

    @contextmanager
    def tx(self):
        with closing(sqlite3.connect(self.path, timeout=15)) as c:
            c.execute('BEGIN IMMEDIATE')
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def linked(self, c, app, subject, qq):
        return c.execute('''SELECT 1 FROM identities o JOIN identities q ON o.account=q.account
            JOIN binding_requests r ON r.app=o.app AND r.subject=o.subject
            WHERE o.platform='qq_official' AND o.app=? AND o.subject=?
            AND q.platform='onebot' AND q.app=? AND q.subject=?
            AND r.legacy_app=q.app AND r.qq=q.subject AND r.status='approved' ''',
            (app, subject, self.legacy_app, qq)).fetchone() is not None

    def create(self, qq, browser):
        if not qq.isascii() or not qq.isdigit() or not browser:
            raise ValueError('请选择已绑定官方 QQ 的 QQ 管理员')
        ticket = secrets.token_urlsafe(24)
        with self.tx() as c:
            c.execute('DELETE FROM official_web_login WHERE expires<=?', (self.clock(),))
            # Only offer delivery for an already reviewed, still-current link.
            identities = c.execute("SELECT app,subject FROM identities WHERE platform='qq_official'").fetchall()
            if not any(self.linked(c, app, subject, qq) for app, subject in identities):
                raise ValueError('该管理员尚未完成人工绑定，请先通过现有方式登录并审核绑定')
            c.execute('DELETE FROM official_web_login WHERE qq=? AND browser=?', (qq,digest(browser)))
            c.execute('INSERT INTO official_web_login(ticket,qq,browser,expires) VALUES (?,?,?,?)',
                      (digest(ticket),qq,digest(browser),self.clock()+300))
        return ticket

    def claim(self, ticket, app, subject):
        with self.tx() as c:
            row = c.execute('SELECT qq,expires,code FROM official_web_login WHERE ticket=?', (digest(ticket),)).fetchone()
            if not row or row[1]<=self.clock() or row[2] is not None or not self.linked(c,app,subject,row[0]):
                raise ValueError('登录请求无效、已领取或当前账号尚未绑定该管理员')
            code = f'{secrets.randbelow(1_000_000):06d}'
            c.execute('UPDATE official_web_login SET code=?,app=?,subject=? WHERE ticket=?',
                      (digest(digest(ticket)+code),app,subject,digest(ticket)))
            return code

    def discard(self, ticket):
        with self.tx() as c:
            c.execute('DELETE FROM official_web_login WHERE ticket=?', (digest(ticket),))

    def consume(self, qq, code, browser):
        with self.tx() as c:
            row = c.execute('SELECT ticket,expires,code,attempts,app,subject FROM official_web_login WHERE qq=? AND browser=?',
                            (qq,digest(browser))).fetchone()
            if not row:
                return False
            ticket, expires, expected, attempts, app, subject = row
            valid = bool(expires>self.clock() and expected and self.linked(c,app,subject,qq)
                         and secrets.compare_digest(expected,digest(ticket+code)))
            if valid or expires<=self.clock() or attempts>=4:
                c.execute('DELETE FROM official_web_login WHERE ticket=?',(ticket,))
            else:
                c.execute('UPDATE official_web_login SET attempts=attempts+1 WHERE ticket=?',(ticket,))
            return valid


def configured_service():
    return OfficialWebLogin(os.environ.get('PERSONIFICATION_BINDING_DB',''),
                            os.environ.get('PERSONIFICATION_BINDING_LEGACY_APP',''))
