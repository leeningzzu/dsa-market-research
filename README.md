# 我的股票与 ETF 研究助手

基于 DSA 的个人研究版本。主要关注 A 股：先筛选值得研究的股票和 ETF，再结合趋势、量价、估值与风险解释原因，最后通过邮件查看。也可以直接输入代码研究，不需要先通过自动筛选。

**不是自动交易软件，不连接券商。** 不把技术评分称为胜率，也不把模型的判断当成市场事实。

## 我现在要做什么

| 任务 | 从这里开始 |
|---|---|
| 第一次部署、看清 GitHub 两个 Actions 开关 | [部署与首次检查](docs/OPERATOR_RUNBOOK.md#task-start) |
| 添加、删除自选股票或 ETF | [修改自选列表](docs/OPERATOR_RUNBOOK.md#task-watchlist) |
| 暂时排除医药、以后重新纳入 | [筛选偏好：现状与拟实现操作](docs/OPERATOR_RUNBOOK.md#task-preferences) |
| 修改收件邮箱、模型或 API Key | [邮件与模型配置](docs/OPERATOR_RUNBOOK.md#task-config) |
| 配置 Cloudflare R2，弄清它存什么 | [R2 配置和数据边界](docs/OPERATOR_RUNBOOK.md#task-r2) |
| 看报告、修改模板、减少重复内容 | [报告与模板](docs/OPERATOR_RUNBOOK.md#task-report) |
| 以后研究港股、美股 | [多市场扩展](docs/OPERATOR_RUNBOOK.md#task-markets) |
| 运行失败、没收到邮件、回滚 | [排障与恢复](docs/OPERATOR_RUNBOOK.md#task-recovery) |

## 已有能力与尚未完成的部分

文档核对基线：2026-09-21 的私有开发候选。文档不会自动读取仓库当前开关；实时状态以当前分支、Actions 页面和运行结果为准。

| 功能 | 本个人版本的状态 |
|---|---|
| A 股股票、A 股 ETF 自动候选与共用深析 | 已有代码；候选不等于可以买入 |
| 指定代码研究 | 已有入口；与自动候选共用分析流程 |
| 私有 Actions 到邮箱 | 已有一次运行成功和实际收件截图；不等于每天定时已验收 |
| 自然、无重复的邮件 | 本次实收邮件未达标；本地开发候选已做共享投影去重与条件安全根修，仍待完整 CI 和新的消费者验收 |
| 可撤销的医药排除、板块排除和板块优先 | 本地开发候选已接入配置与 AUTO 股票筛选；尚未提交/部署到私有 `main`，现在不要在 GitHub 提前填写 |
| 港股、美股指定代码 | 已有底层识别与数据路径；本私有版本仍需逐市场验收 |
| 港股、美股自动筛选 | 暂不启用，只保留扩展方向和接入说明 |
| R2 跨运行研究状态 | 有代码及历史空状态试验证据；日常真实研究状态仍未启用 |

## 研究怎样变成邮件

自动候选或指定代码 → 已有数据源 → 检查时间、完整性和资产身份 → 确定性分析与风险判断 → 用人话解释 → 保存报告 → Email。

模型负责解释，不另算一套买卖结论。月、周、日使用各自已完成的数据；分钟线不可用时，不编造短线信号。

运行中的 SQLite 由 DSA 管理。R2 只用于另行验收的选择性研究状态保存，不是全市场行情库，也不是把整份私人数据库公开上传。详见 [数据到报告的完整说明](docs/OPERATOR_RUNBOOK.md#task-data)。

## 私有库与自动运行

后续开发、测试和正式邮件以私有库为主；公开库只在需要时保留安全的参考副本。

手动运行可以选开发分支；GitHub 的定时任务运行默认分支。开发分支测试通过后，还需要将验收过的版本正式合入私有 `main`，再启用长期定时运行。不要把启用按钮当成版本晋升。

当前开发代码的晚间计划是北京时间 **19:00**，不是旧版首页写的 18:00。它是触发目标，GitHub 可能排队延迟；早报属于独立待验收功能。日常启用前按 [操作手册](docs/OPERATOR_RUNBOOK.md#task-schedule) 核对实际版本和开关。

## 来源与许可

本版本基于 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)；内嵌筛选选择性复用了 AlphaSift 代码。保留 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 中的版权及第三方声明。

[文档中心](docs/INDEX.md) · [配置参考](docs/full-guide.md) · [常见问题](docs/FAQ.md) · [更新记录](docs/CHANGELOG.md)

英文、繁中旧首页仍作上游能力参考，本轮不把它们当作个人私有部署现状。

研究输出仅供参考，不保证收益。
