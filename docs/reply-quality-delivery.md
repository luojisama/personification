# 回复质量与管理台交付记录

## 实施边界（2026-09-20）

默认目标是自然群友、保留角色性格，群聊与私聊同等验收。里程碑顺序：可信基线 → 回复改进 → 四步配置引导与实机验收。沿用现有 NoneBot/Agent/认证/存储/UI 组件，不引入第二套运行框架。

真实评测使用服务器配置中的 `gemini-3.8-flash-high`，开发工具总计最多 1500 次模型请求（包含规划、审阅、评分及重试），并发 1。凭据仅在服务器读取，不写入案例、报告或仓库。真实外发限定用户批准的测试群及管理员账号，未知送达不重放。部署只允许本地提交推送后服务器 Git 拉取。

## 调研与取舍

| 来源 | 已检查内容 | 复用范围与局限 |
| --- | --- | --- |
| Promptfoo | 官方 Python Provider 文档、model-graded 指标文档、MIT LICENSE | 采用开发评测接口与报告；薄适配真实插件入口，不能以回声 fake caller 替代真实链路。实际运行兼容性待隔离实验。 |
| SillyTavern | Character Design 文档、AGPL-3.0 LICENSE | 借鉴稳定人格、场景、示例及高级字段分层，不复制源码。文档对风格效果的说明不是本项目实测。 |
| CharacterEval | 本地上游 README、compute_score.py、MIT LICENSE | 借鉴分维度、多轮、人工标注与分歧复核。其均值评分及历史基准不能证明当前群聊质量，不搬运角色语料或奖励模型。 |
| ChatHaruhi | README、ChatHaruhi2.0/ChatHaruhi/ChatHaruhi.py | 参考人格、故事与历史分别预算；不迁移嵌入模型和角色故事库，避免以他人台词替代自然接话。 |
| AIRI | MIT LICENSE、stage-ui stores/onboarding.ts | 参考配置引导可跳过/重新进入、已配置状态与显示状态分离；不照搬其 localStorage 凭据和跨窗同步体系。 |
| LibreChat | 上游 README、MIT LICENSE | 人格/模型预设仍是候选；须检查具体对应源码后才能声称采用其设计。 |

主要来源：
- https://github.com/promptfoo/promptfoo/blob/main/site/docs/providers/python.md
- https://github.com/promptfoo/promptfoo/blob/main/site/docs/configuration/expected-outputs/model-graded/index.md
- https://docs.sillytavern.app/usage/core-concepts/characterdesign/
- https://github.com/morecry/CharacterEval
- https://github.com/LC1332/Chat-Haruhi-Suzumiya
- https://github.com/moeru-ai/airi
- https://github.com/danny-avila/LibreChat

上游源码与网页只作研究证据。`_reference/` 不入库。本轮未复现外部论文的质量结论，也不以其宣传效果作为本项目验收。

## 基线待验证问题

1. 原回放脚本只生成 metadata fallback plan，不能测真实回复自然度。
2. 群聊问句检测参与改写及静默，需以有用追问/机械抛问的成对案例分辨误伤。
3. normalize_visible_reply_text 包含对口头表达的机械改写，需确认语义完整性与人格影响。
4. 启动需覆盖实际 driver lifespan 和 matcher 注册，不能只依据局部签名测试。

当前阶段仅建设评测与启动检查，以上问题不是未经实测即改动全部行为的理由。每个里程碑完成时补充实际命令、样例和未验证边界。

## 底座验证进展

- Promptfoo 固定 0.120.0（MIT）；本机 Node 22.18.0/npm 10.9.3 已验证 Python 异步 Provider 与 JSON 报告导出，离线 canary 1 项通过。该结果不代表真实回复质量。
- 实际 Agent + 最终对话审阅已接入开发适配器，预置记忆保持不可信角色。模型请求前持久化扣减全局额度，失败不退款；被内层捕获的请求失败也不记为评测成功。
- 定向 Python 检查 11 项通过（评测基础、实际 lifespan、matcher 注册）。lifespan 在临时工作目录屏蔽生产配置读取，外部服务和定时任务为替身，插件注册及自身启动/退出 hook 为实际实现。
- 已形成 80 个案例草案；目前适配器覆盖 Agent 与最终审阅，尚未证明完整普通/YAML、记忆写入、媒体工具和送达路径。真实模型调用尚未开始，费用与质量评分无可报告结果。
