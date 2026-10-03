# 我的股票与 ETF 研究助手

基于 DSA 的个人研究版本。当前重点是 A 股股票与 ETF：先用确定性数据和经典/成熟方法形成多周期证据，再给出唯一 canonical decision，最后把同一事实投影到 Report / Email / Telegram / Web。指定代码不需要先通过自动筛选。

**不是自动交易软件，不连接券商。** 技术评分不等于历史胜率，模型解释不等于市场事实，概率只有在合法 Outcome/PIT 数据、样本外验证和校准完成后才允许出现。

## 先看清当前架构

- **代码 authority**：公开迁移完成后以 `leeningzzu/dsa-market-research` 的 `main` 为唯一 current code authority；它必须连续继承 2026-10-03 已验证的 `factor-decision-v1-r002@2f89eb9d...` 主线，而不是退回旧 private `main`。
- **回滚 predecessor**：原 private 仓库保留 exact SHA 作为审计/回滚依据，但 Promotion 后不再与公开库并列成为第二个 Production authority。
- **代码公开 ≠ 私人数据公开**：API Key、SMTP 授权码、私人持仓、原始 Ledger/Outcome、训练数据、模型状态、私人报告和私有 R2 数据都不得提交到公开 Git。
- **初始 public 只证明 CI**：迁移首阶段仅允许只读 CI；daily live、Email/Telegram、R2/state、release/publish 等要在各自 gate 证明等效后再逐项启用。
- **Production Shell 仍是 DSA**：迁仓不会改变证据族、周期职责、canonical decision、Product、Ledger/PIT 或模型 Promotion 路线。

## 我现在要做什么

| 任务 | 从这里开始 |
|---|---|
| 先弄清 public 代码、private 数据和回滚关系 | [当前 authority 与隐私边界](docs/OPERATOR_RUNBOOK.md#task-authority) |
| 第一次部署、确认 current `main` 与 Actions | [部署与首次检查](docs/OPERATOR_RUNBOOK.md#task-start) |
| 添加、删除自选股票或 ETF | [修改自选列表](docs/OPERATOR_RUNBOOK.md#task-watchlist) |
| 暂时排除医药、以后重新纳入 | [筛选偏好：配置、验证与撤销](docs/OPERATOR_RUNBOOK.md#task-preferences) |
| 修改收件邮箱、模型或 API Key | [邮件与模型配置](docs/OPERATOR_RUNBOOK.md#task-config) |
| 配置 Cloudflare R2，弄清它存什么 | [R2 配置和数据边界](docs/OPERATOR_RUNBOOK.md#task-r2) |
| 看报告、修改模板、减少重复内容 | [报告与模板](docs/OPERATOR_RUNBOOK.md#task-report) |
| 以后研究港股、美股 | [多市场扩展](docs/OPERATOR_RUNBOOK.md#task-markets) |
| 运行失败、没收到邮件、回滚 | [排障与恢复](docs/OPERATOR_RUNBOOK.md#task-recovery) |

## 5 分钟部署路径

1. 打开 `leeningzzu/dsa-market-research`，确认当前默认分支是 `main`；迁移尚未 Promotion 时，仍以记录的 private predecessor exact SHA 为 current authority。
2. 先看 **Actions → CI**。公开库首次上线只要求 `push(main)` / `pull_request(main)` 的只读 CI 通过；不要因为看见其它 workflow 文件就认为 live 分析已经启用。
3. 只有 live-analysis gate 另行通过后，才在实际运行仓库的 **Settings → Secrets and variables → Actions** 配置模型、SMTP、通知和需要的 Variables；Secret 值永远不写进 README、issue、日志或 Git。
4. 真实运行必须绑定 exact branch/SHA、输入和成本/数据 effect；已有成功验收不为了补截图机械重跑。
5. 长期定时只运行默认分支。当前晚间研究目标是北京时间 **19:00**；在 public live parity 尚未证明前，不同时启用 private/public 两套 daily。

完整逐步操作、验证、撤销和排错见 [个人版操作手册](docs/OPERATOR_RUNBOOK.md)。

## 已有能力与尚未完成的部分

文档核对基线：2026-10-03，迁移父基线为已验证的 `factor-decision-v1-r002@2f89eb9d...`。README 只给用户侧状态；exact SHA、CI、OPEN/DEFERRED/CLOSED 和 Promotion 证据由 Project Sources / Git / CI receipt 管理。

| 功能 | 当前状态 |
|---|---|
| A 股股票、A 股 ETF 自动候选与共用深析 | 已有代码；候选只是研究入口，不等于可以买入 |
| 指定代码研究 | 已有入口；与自动候选共用同一 deep-analysis / canonical / report consumer |
| 月/周/日 MA 结构 | readiness-aware MA 叶已通过自然 CI；20/26/40 分别承担 level、slope/cross、完整 compression context，分钟周期仍不能冒充 READY |
| GitHub Actions | public migration 初始只开放只读 CI；live daily / Email / Telegram 等不是因为代码公开就自动启用 |
| 医药/板块排除与板块优先 | 当前代码已接入配置、Actions 映射和 AUTO 股票筛选；配置是否生效取决于实际运行仓库/版本与 live gate，不需要重训模型 |
| V2.5 邮件 | accepted exact baseline 继续作为格式/逻辑 parent；完整动态七周期和所有实际消费者仍按独立证据推进，不因迁仓重画模板 |
| Ledger / Outcome / PIT | 已有第一条有界真实 Ledger vertical；成熟 Outcome/PIT cohort 和训练准入仍未完成 |
| Logistic / LightGBM / 概率校准 | 设计路线已冻结但当前 NOT_DUE；先有合法 PIT 数据和样本外基线，再谈模型增量与 Promotion |
| R2 跨运行研究状态 | 代码与历史试验边界存在；私人 state 继续独立 gate，不进入公开 Git |
| 港股、美股 | 底层能力选择性存在，完整指定代码/自动筛选仍按市场数据、时区、币种和 completed-bar 逐项验收 |

## 研究怎样变成邮件

自动候选或指定代码 → 已有数据源 → 检查时间、完整性和资产身份 → 确定性分析与风险判断 → 用人话解释 → 保存报告 → Email。

模型负责解释，不另算一套买卖结论。月、周、日使用各自已完成的数据；分钟线不可用时，不编造短线信号。

运行中的 SQLite 由 DSA 管理。R2 只用于另行验收的选择性研究状态保存，不是全市场行情库，也不是把整份私人数据库公开上传。详见 [数据到报告的完整说明](docs/OPERATOR_RUNBOOK.md#task-data)。

## 公开代码、私人状态与自动运行

公开迁移完成后，`dsa-market-research/main` 承担 current code authority；原 private 仓库只保留 predecessor / rollback 身份。**公开的是代码与安全文档，不是私人运行数据。** Repository Secrets 的值即使以后配置在 public repo 也不会进入 Git；初始迁移阶段不复制任何 Secret。

GitHub `schedule` 只运行默认分支，因此“某个临时 feature branch 测试通过”不等于长期定时已经升级。未来开发使用短期 feature branch → PR → `main` → 删除 branch；是否启用 live workflow 仍是独立 Promotion/consumer gate，不能把“代码在 main”与“生产定时已经启用”混成一件事。

当前晚间研究目标是北京时间 **19:00**。GitHub 可能排队延迟；早报属于独立待验收能力。迁移期间 private live 与 public live 不得同时启用，避免重复邮件和双 scheduler authority。日常启用前按 [操作手册](docs/OPERATOR_RUNBOOK.md#task-schedule) 核对实际 repo、SHA、workflow 和开关。

## 出问题先从哪里查

| 现象 | 第一检查点 | 继续定位 |
|---|---|---|
| public push 后 CI 红了 | Actions → CI → 第一个失败 job | [排障与恢复](docs/OPERATOR_RUNBOOK.md#task-recovery) |
| CI 绿了但 daily 没跑 | 当前 public 阶段是否只开放 CI、daily job 是否仍被 guard | [长期定时](docs/OPERATOR_RUNBOOK.md#task-schedule) |
| 没收到邮件 | 本次是否真的执行 live analysis、候选数、SMTP结果、收件箱/垃圾箱 | [邮件与模型配置](docs/OPERATOR_RUNBOOK.md#task-config) |
| 数据/周期看起来不对 | 数据来源、目标日期、completed bar、分钟周期是否真实可用 | [数据流程](docs/OPERATOR_RUNBOOK.md#task-data) |
| 报告重复/缺材料 | canonical 输入、factor summary、模板、Notification 最终消费者 | [报告与模板](docs/OPERATOR_RUNBOOK.md#task-report) |
| 改了配置却没生效 | 改的是哪个 repo、Secret/Variable、哪个 workflow、是否新运行 | [排障与恢复](docs/OPERATOR_RUNBOOK.md#task-recovery) |
| 需要回滚 | exact commit / predecessor receipt，不删除失败证据 | [排障与恢复](docs/OPERATOR_RUNBOOK.md#task-recovery) |

## 来源与许可

本版本基于 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)；内嵌筛选选择性复用了 AlphaSift 代码。保留 [LICENSE](LICENSE) 和 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 中的版权及第三方声明。

[文档中心](docs/INDEX.md) · [配置参考](docs/full-guide.md) · [常见问题](docs/FAQ.md) · [更新记录](docs/CHANGELOG.md)

英文、繁中旧首页仍作上游能力参考，不作为本个人 current authority、public migration 或 Production 验收证据。

研究输出仅供参考，不保证收益。
