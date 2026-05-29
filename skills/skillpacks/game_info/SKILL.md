# game_info

为拟人插件提供游戏信息聚合工具，覆盖热门网游、3A 大作与小众独立游戏。

公开工具：

- `game_info(game, aspect, query?)`

`aspect` 取值：

- `update`：更新内容 / 补丁公告 / 新版本。优先走 **Steam 官方公告**（store 搜索解析 appid → ISteamNews，免 key）；解析不到（如国服网游）回退社区定向搜索。
- `guide`：攻略 / 流程 / 通关。社区定向搜索（游民星空、3DM、米游社、B站、贴吧、reddit、gamefaqs 等）。
- `story`：剧情 / 设定 / 世界观。优先 `wiki_lookup`（Fandom/萌娘/维基），并补一条社区搜索覆盖小众游戏。
- `tips`：技巧 / 进阶 / 注意事项。社区定向搜索。

数据源与实现复用：

- Steam Store 搜索 + ISteamNews（免 key）
- `resource_collector` 的免 key 多搜索引擎抓取（DuckDuckGo + Bing）
- `wiki_search` 的 `wiki_lookup`
- 若 `runtime.tool_caller` 可用，会把多源结果压缩成一段可读摘要，并附参考来源；否则直接返回结构化结果。

使用原则：

- 「某款游戏 + 某个方面（更新/攻略/剧情/技巧）」的查询统一走 `game_info`
- 纯角色/世界观/设定的百科式查询也可直接 `wiki_lookup`
- 宽泛的资源合集 / 链接收集走 `collect_resources`
- 查不到时如实承认，不要编造攻略或剧情

相关配置（`config.py`）：

- `personification_game_info_enabled`：开关（默认开）
- `personification_game_info_timeout`：Steam API / 搜索超时（默认 15s）
- `personification_game_info_community_sites`：社区站点白名单覆盖（留空用内置默认）
