# 共同经历与发言时效（实验功能）

两个独立配置默认关闭：`personification_experience_cards_enabled`、`personification_reply_timeliness_enabled`。关闭经历后保留资料和查看/遗忘操作，停止新卡写入及上下文使用。

## 经历接口

`build_experience_router(runtime=runtime)` 注册 `/api/v2/experiences`。所有操作沿用管理员认证。

- GET：查询参数 `platform`、`bot_id` 必填，`group_id`/`user_id` 至少一个，`topic_id` 默认空，`limit` 为 1–200。返回 `{items,enabled}`。
- POST：相同 scope 查询参数；正文 `summary`、`source_refs:[{message_id}]`，可选 `kind=experience|stable_identity`、`confirmation=pending|confirmed`、`expires_at`（Unix 秒）。来源作者和时间由服务端读取，不信任客户端填写。返回卡片。
- PATCH `/{memory_id}`：相同 scope，正文 `summary,expected_revision`，可选 `confirmation,expires_at`。旧版本返回 409，跨作用域返回 404。
- DELETE `/{memory_id}`：相同 scope，`expected_revision` 必填。禁用功能后仍允许遗忘。

卡片字段包括 `memory_id,summary,participants,source_refs,time_created,updated_at,revision,revision_history,confirmation,kind,expires_at` 和 scope。修订记录只留动作/版本/时间，不保留已遗忘正文。

网页/API 可填写原始数字 TG Bot、Chat、用户和 Topic ID，服务端转换为内部作用域，并处理该 Bot 的群升级映射。也兼容直接传内部 namespace：内部 `group_id` 已编码 Topic，此时 `topic_id` 留空；内部 `bot_id` 例如 `p8:telegramb3:123`，用户使用 sender namespace。不要把原始 ID 与内部 ID 混填。

卡片保存在现有 MemoryStore，排除通用全局召回。普通经历经精确 scope 和用户上下文权限过滤后仍经过语义闸门；仅管理员确认、未过期的 stable_identity 可直接进入参考资料。确认身份卡必须设置未来有效期。它们都不是系统指令。

普通/YAML 仅在确认送达后把真实入站 message_id 交给现有 MemoryCurator；可核验来源才生成 pending 卡。来源缺失不制造卡片，保留已有记忆流程。自动卡是可追溯的回合记录，不宣称已经生成高质量故事式总结。Bot 未确认送达的草稿不能作为来源。

修订/遗忘清除有关向量及递归声明了 `source_memory_ids`、`source_refs[].memory_id` 或 `summarized_by` 的派生记忆；词法索引由已有删除触发器清理。不引入经历缓存。未声明来源的历史旧摘要无法可靠追溯，不声称能自动识别其内容依赖；已提交给模型的进行中请求也不能撤回其已读内容。

## 时效边界

普通/YAML 从生成开始记录群历史版本，在**已有**最终审阅请求内加入期间新增/编辑消息；新资料同样经过用户上下文权限过滤。最终审阅决定继续、改写/缩短或沉默，不增加独立评分调用。Trace 记录状态、耗时和审阅后是否再次变化，不输出模型隐藏推理。

审阅后再次变化的自主插话停止；明确请求继续由已有批处理、generation fence 和发送提交锁负责。私聊沿用已有显式轮次逻辑。关闭了最终审阅的路径不会为了此开关额外强制一次请求。已经发出的分段与结果未知的发送不重放。

## Telegram 发送辅助

`send_parts` 预校验全部标准消息 parts，再逐段调用官方 API；文本按 UTF-16 4096 单位分段，保留全部字符。每段返回 SendReceipt；中间失败或送达未知立即停止，聚合结果包含 `message_ids,receipts,partial`。`to_receipt()` 将已部分送达的批次归为 unknown，调用方不能整批重放。

媒体允许公开 HTTP(S) URL、显式 bytes、受大小限制的 base64 data URI 或非路径的 Telegram file_id；拒绝本地路径、file URL、私网 IP、带认证信息和 TG Token 的下载地址。默认单个字节媒体上限 20 MiB。

`moderate` 支持 mute/unmute/kick/delete/pin，调用前实时检查 Bot 权限及目标管理员身份。解除禁言复用群默认权限。移出是 ban 后 unban；第二步失败返回 unknown/partial，不自动重放。调用方仍须验证发起人的管理授权。

本地测试使用隔离数据库和 fake Bot；这些测试不证明真实 Telegram 收发或生产质量已验收。
