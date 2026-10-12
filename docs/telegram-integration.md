# Telegram 双入口

网页托管和原生配置复用 `nonebot-adapter-telegram==0.1.0b20` 的 Bot、Event 和 API，通过同一桥接进入现有普通/YAML 回复链路。连接功能不改变生产表达策略；聊天实验室的 B/C 策略仍为隔离实验。

## 安装与驱动

在宿主 NoneBot 使用的 Python 环境安装一次，然后重启：

```powershell
python -m pip install -r plugin/personification/requirements-telegram.txt
python -m pip install "nonebot2[fastapi,httpx]>=2.4.4"
```

宿主 `.env` 设置 `DRIVER=~fastapi+~httpx`。网页托管不要求额外注册 Telegram Adapter；原生入口需在宿主启动文件中 `driver.register_adapter(nonebot.adapters.telegram.Adapter)`。不要把同一个 Token 同时填入两种入口。

兼容验证使用 PyPI b20 wheel、NoneBot 2.4.4 和 Pydantic 2.12.5。b20 元数据要求 NoneBot >=2.1.3；上游开发分支的依赖声明不等于此发布包要求。未验证其他 beta 版本，升级时应重跑真实 Event/API 契约测试。

## 网页托管

管理台「Telegram 接入」：填 Token、可选代理与 Bot API 地址 → 检查连接 → 填人格说明及私聊/群聊范围 → 保存 → 启用。依赖和驱动就绪后，增删、启停和恢复无需重启宿主。Token 提交仅允许 HTTPS 或本机访问；浏览器不持久保存 Token。

打开启用后提供的 `/start` 深链接可验证实际私聊接收和发送；链接十分钟内一次有效，不授予管理员权限。体检分别列出凭据、接收、发送、群权限和可见性，未观察到证据的项保持“未验证”。该结果不能推断任意群或 Topic 的权限。

群内先发送消息使房间出现在列表，再按群/Topic 设置参与模式和主动消息开关。新房间参与模式为 `shadow`，主动消息关闭；直接提及、引用和私聊仍可正常响应。群消息受 [Privacy Mode](https://core.telegram.org/bots/faq#what-messages-will-my-bot-get) 影响：仅收到命令或提及，不能证明能够读取普通群聊。

Telegram 对话不依赖 QQ 群白名单：接入范围允许的私聊、群内明确提及/引用可进入回复链路；群内自主参与仍由房间设置控制。黑名单、静音、来源和权限约束继续生效。这与管理台管理员权限相互独立。

托管连接启动前检测 Webhook，管理员明确迁移才调用删除，且保留待处理更新。轮询 409 冲突、失效 Token 或拒绝访问停止；临时故障有限退避。停止会中止长轮询并等待已开始的处理/发送退出，不取消进行中的外发。未知送达不自动重放。

## QQ 离线时使用 Telegram 登录

配置 `personification_telegram_admins` 为 Telegram 管理员的**数字用户 ID**列表（例如 `["123456789"]`），可在配置中心修改；首次接入也可写入宿主配置。不要填 `@username`，用户名可以改名，必须先核实其对应数字 ID。

管理员先私聊已启用的 Bot 并发送 `/start`，再在管理台登录页选择对应的 Telegram 管理员，验证码由 Bot 私聊发送。无需 QQ 在线，验证码不授予配置之外的身份任何权限。系统内部以 `telegram:<用户ID>` 保存登录身份，不与同号 QQ 管理员合并；沿用浏览器 challenge、验证码有效期、限流、CSRF 和设备撤销机制。删除管理员配置后，其现有设备也会失去访问权限。

## 绑定 QQ 好友与共享资料

1. 在 TG Bot 私聊发送 `/bindqq <QQ号>`。有多个 QQ Bot 时指定 `/bindqq <QQ号> <QQ Bot号>`。
2. 指定 QQ Bot 核验好友关系后，向该 QQ 私聊发送一次性确认凭据。本人在同一 QQ Bot 的好友私聊中发送 `/bindtg <确认凭据>`，十分钟内有效。
3. 用 `/bindstatus` 查询；`/cancelbind` 撤销未完成的请求；`/unbind` 解除绑定。QQ 离线时可以记录待确认请求，上线后重新发起投递；离线不等于确认，未确认送达的凭据不能使用。

确认后，在双方私聊中共享个人资料、关系信息、共同经历、长期记忆与有界的私聊历史。共享内容保留来源，作为参考进入原回复流程；不会把另一端 Bot 的发言当成用户事实。原始记录、更新和纠正仍归属于各自账号；解绑停止后续跨端读取，不删除原数据。若解绑或来源禁读发生在等待模型、重试或发送前，取消已经携带共享资料的本轮草稿；正在发送的动作不取消、不重放。

绑定固定双方平台、Bot 与用户身份，不按昵称或同号合并；群与 Topic 记录、黑名单和管理员权限不随绑定迁移，公开动态也不读取绑定私聊。旧 QQ 用户级基础资料只共享可明确归属于本人的字段，缺少 Bot 来源的旧记忆不自动跨端放行。

## NoneBot 原生入口

宿主按官方配置启动，例如：

```dotenv
TELEGRAM_BOTS=[{"token":"<BotFather Token>","is_webhook":false}]
TELEGRAM_PROXY=http://127.0.0.1:7890
```

Webhook 使用 `is_webhook: true` 和 `TELEGRAM_WEBHOOK_URL=https://<公开域名>`，由官方适配器提供 `/telegram`。原生 Bot 连接后自动列出“由 NoneBot 配置管理”，网页不修改其凭据、生命周期或 Webhook 配置。

**原生连接生命周期由官方 b20 管理。** b20 自身启动会删除旧 Webhook，轮询故障按上游重试；托管入口的 Webhook 迁移确认、持久 offset 和遇冲突停止策略不替换原生生命周期。原生入口也经过插件的持久事件去重和统一回复/回执契约。使用原生入口前应先停用旧接收方。

## 能力与隔离

| 范围 | 当前实现 |
|---|---|
| 对话 | 私聊、群/超级群、Topic、真实提及和引用、相册合批、编辑更新且不再次回复 |
| 输入媒体 | 图片/静态贴纸、GIF、视频、音频/语音经有界下载进入已有理解链路；依赖所选模型及媒体能力 |
| 降级 | 普通文件与 TGS 动画贴纸标记不支持，不伪造理解结果；下载失败也不把带凭据 URL 交给模型 |
| 输出 | 文本、标准媒体 parts、受限 TTS 缓存音频；文本按 Telegram UTF-16 长度限制分段 |
| 管理 | 管理台禁言、解除禁言、移出、撤回和置顶；每次执行重新检查权限 |
| 主动消息 | 群/Topic 空闲文本发话，复用现有审阅/额度/冷却，见下方说明；没有新增私聊主动联系 |
| 平台专属 | 隐藏/过滤 QQ 专属工具；不支持 TG 空间、频道动态或 QQ 空间同步，原 QQ 能力保留 |

平台、Bot、Chat、Topic 共同决定短期历史与房间作用域；用户身份包含平台/Bot，不按昵称或数字 ID 跨 QQ/TG 合并。匿名管理员、频道发言保留 sender_chat 身份。群升级保留对应 Bot 的会话映射；旧 QQ 数据继续兼容读取，无全库重写。人格内容可复用，关系和记忆默认隔离，仅在双方确认绑定后按上述范围读取。

NoneBot 2.4.4 的进程内 Bot 注册表以原始 self_id 为键：若 QQ Bot 本身与 TG Bot 恰好同号，托管入口拒绝启动，须使用不同宿主进程；不会覆盖已有 Bot。此限制不影响同号聊天用户的数据隔离。

托管凭据保存在数据目录 `telegram/connections.secret.json`，使用现有受限文件权限与原子替换；普通 DTO 和导出数据集不包含此文件。持久去重/offset/房间在 `telegram/updates.sqlite3`。更新在处理前认领，因此崩溃时宁可遗漏回复，也不重放可能已经执行的动作；这不是端到端恰好一次保证。

## API 与其他阶段

管理员认证/CSRF 沿用现有管理台。`/api/v2/connections` 提供列表、`probe`、新增/修改/删除、`start`/`stop`/`check`/`status`、`rooms` 和 `moderation`。Native ID 为 `native:<bot_id>`，Bot DTO 的选择 ID 为 `telegram:<bot_id>`，原始 ID 另存 `raw_bot_id`。SSE 使用 `telegram.connection`。

- [聊天实验室](quality-lab.md)：固定快照与请求预算、A/B/C 独立状态、捕获外发、五维盲评。
- [共同经历与发言时效](experience-and-timeliness.md)：两个独立默认关闭的开关；已确认且未过期身份卡才可确定性召回。
- [Telegram 主动消息](telegram-proactive.md)：群/Topic 开关、送达预留与过期草稿撤销。

## 验证边界

隔离测试使用真实 b20 事件解析和 Bot/API 对象配合假传输，覆盖动态启停、Webhook/轮询冲突、重复更新、媒体、实际处理入口、房间隔离、管理鉴权、实验工作进程及发送未知结果；前端执行组件测试、类型检查、构建和浏览器 mock API 流程。

CI 安装 `requirements-telegram.txt` 后执行 `python -m pytest tests`；未安装可选适配器时真实协议测试跳过。隔离 wheel 可通过 `PERSONIFICATION_TELEGRAM_ADAPTERS` 指向其 `nonebot/adapters` 目录，不要求在生产环境安装测试包。

2026-10-10 经用户授权部署到指定 VPS，使用现有监控脚本重启。实机已验证凭据、无 Webhook 冲突、持续长轮询及用户 `/start`/中文消息接收；发现并修复中文 nonce 校验异常和旧 QQ 后台任务误选 TG，补丁隔离回归 66 项通过。刷新 VPS 代理订阅后，真实回复送达已由成功 Trace 与发送回执确认；两条样本处理耗时约 25–26 秒，计入接收合批等待约 40–65 秒，不代表稳定分位数。群权限/可见性、媒体与管理操作尚未完成验收。

合并隔离回归 522 项通过，前端 191 项测试、类型检查、构建及浏览器 mock 流程通过；初轮全量 3989 项通过、1 项失败已修复，未对最新源码再次执行完整全量。真实 30 段失败对话、20 段保留集和人工质量发布判断尚待提供。功能交付不宣称自然度提升，生产表达仍为 A，B/C 保持实验配置。

复用来源（2026-10-10 核对）：[官方适配器](https://github.com/nonebot/adapter-telegram)、[PyPI b20](https://pypi.org/project/nonebot-adapter-telegram/0.1.0b20/)、[Telegram Bot API](https://core.telegram.org/bots/api)、[Promptfoo Python Provider](https://www.promptfoo.dev/docs/providers/python/)。沿用官方适配器 MIT 许可；不引入第二套 Telegram 框架。
