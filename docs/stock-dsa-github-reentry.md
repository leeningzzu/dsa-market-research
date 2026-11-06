# STOCK_DSA 本地／GitHub 无压缩续接入口

REENTRY_ID = STOCK_DSA_LOCAL_GITHUB_REENTRY_V2

本文件解决一个具体问题：换账号、换 Chat 或换电脑后，如何不依赖旧聊天摘要，继续同一个 STOCK_DSA Git 历史、父目标、代码、测试、失败谱系、数据权利边界和唯一后继关口。

它不是第九个 Project Source，不是第二个 Production Shell，也不替代实际 Git、CI、来源权利或运行回执。

## 1. 先看最简单的答案

### 同一台电脑换账号

不需要重新下载项目，不需要搬 ZIP，不需要复述旧聊天。让新账号的 WebCodex：

1. 读取本机安装根的 `STOCK_DSA_CURRENT.json`；
2. 绑定其中 `canonical_local_repo`；
3. 读取仓库根 `AGENTS.md`；
4. 执行 `python scripts/verify_stock_dsa_reentry.py --json`；
5. 继续 `docs/stock-dsa-stage-baseline-v1.json` 中的 `route_decision.next_primary`。

本机当前安装根为 `D:\StockResearchData`。其 `repo\dsa-market-research` 是耐久 Git 仓库；WebCodex 的唯一可写 worktree 与它共享同一 Git common directory。两者是同一个 Git 历史的耐久存储和工作视图，不是两个项目。

### 换电脑

优先从 canonical GitHub exact feature checkpoint clone。若远端尚未包含本地 checkpoint，则使用安装根 `_authority` 目录中的完整 Git bundle 恢复；恢复后仍按 `AGENTS.md` 和 verifier 重新核验。

## 2. Canonical 身份

- Production Shell：`DSA/daily_stock_analysis`
- Canonical repository：`https://github.com/leeningzzu/dsa-market-research.git`
- Production branch：`main`
- 开发分支：`feat/stock-dsa-m2-source-gates-v1`
- 最近已提交本地前驱：`9a7713e9482b95340a58600273e4be892332e5a7`
- 当前 commit：必须运行 `git rev-parse HEAD` 与 `git log -1 --format=%H -- docs/stock-dsa-github-reentry.md` 现场解析
- 原始 V2.5：125919 字节，SHA256 `039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823`
- 接受的原机器图：149628 字节、775 边，SHA256 `b4f69f76807ac44bc0fea1a03f9aa38484b6083ca574a3699220b0564ac55aed`

目录名、旧聊天、缓存 `origin/main`、Project Source 版本和 README 均不能单独证明 currentness。

## 3. 新账号必须读取的仓库文件

1. `AGENTS.md`——Agent 规则唯一真源
2. 本文件
3. `docs/stock-dsa-roadmap.md` 顶部 `CURRENT_MASTER_CONTROL`
4. `docs/stock-dsa-stage-baseline-v1.json`
5. `docs/CHANGELOG.md` 的 `[Unreleased]`
6. 当前关口涉及的源码和独立测试

随后执行：

```bash
python scripts/verify_stock_dsa_reentry.py --json
```

任何 STOP 都必须按输出定位 Git、缺文件、计数、immutable hash 或文档漂移；禁止用旧聊天猜 current。

## 4. 同机新账号首条 Prompt

```text
你是 STOCK_DSA 实施主线助手。使用 WebCodex Local，先读取：
D:\StockResearchData\STOCK_DSA_CURRENT.json

绑定其中 canonical_local_repo；再读取并遵循仓库 AGENTS.md，随后依次读取：
1) docs/stock-dsa-github-reentry.md
2) docs/stock-dsa-roadmap.md 顶部 CURRENT_MASTER_CONTROL
3) docs/stock-dsa-stage-baseline-v1.json
4) docs/CHANGELOG.md [Unreleased]

实际执行 python scripts/verify_stock_dsa_reentry.py --json，并核对 origin、branch、HEAD、worktree、feature ref。任何冲突 fail-closed，不用旧聊天或目录名猜 current。

保持 DSA/daily_stock_analysis 为唯一 Production Shell；不建立第二数据库、调度器、报告或模型权威。原 V2.5 与原机器图保持不可变。Closed/Superseded 不重跑。

当前已证明：35/35 技术数值叶、35/43 方法、189 方法－周期执行对、56/56 七周期×八族合同；最近多对象 operation 为 13/72，另有独立 M_INDEX_CURRENT atomic Product 1/18，禁止跨 operation 相加。完整 FACT_READY=false。

唯一主路线：MARKET_ALL_A_BSE_DENOMINATOR_AND_TURNOVER_GATE。先闭合 SSE+SZSE+BSE 同日证券池、停牌和明确分母，再计算宽度、成交额/换手和涨跌停；指数成交额或单市场子集不得冒充全A。

未经明确授权不得 merge/main、force push、读取 Secret、发送通知、训练或 Promotion。默认执行器只用 WebCodex Local，不要求 GitHub 插件。
```

## 5. 换电脑恢复

```bash
git clone https://github.com/leeningzzu/dsa-market-research.git
cd dsa-market-research
git fetch --all --prune
git switch feat/stock-dsa-m2-source-gates-v1
python scripts/verify_stock_dsa_reentry.py --json --require-feature-ref
```

若 GitHub feature 尚未包含当前 checkpoint，用完整 Git bundle：

```bash
git clone STOCK_DSA_CURRENT.bundle dsa-market-research
cd dsa-market-research
git switch -c feat/stock-dsa-m2-source-gates-v1 HEAD
python scripts/verify_stock_dsa_reentry.py --json
```

恢复 bundle 后要重新设置并核对 canonical origin；bundle 只保证 Git 历史，不证明远端 currentness 或 CI。

## 6. 当前代码、Product 与关口

- 方法 Profile：43；现有 35；延期 8
- 技术数值叶：35/35
- 七周期×八证据族：56 格
- 方法－周期执行对：189
- 数据对象：76
- 最近多对象 CURRENT operation：13/72
- 独立 Market atomic：`M_INDEX_CURRENT` 1/18 PASS
- 跨 operation 求和：禁止
- 原 V2.5：`FACT_READY=false`

`M_INDEX_CURRENT` 实际 operation 为 `sse-index-current-nosend-20261106T022408Z`。完成日 2026-11-05：上证指数 3689.91、上证50 2811.16、科创50 1286.68。它不准入 SZSE/BSE、全A宽度、全市场成交额、历史PIT、存储、训练或策略升级。

剩余复杂关口：

1. Market：沪深京完成日证券池、停牌、宽度、统一成交额/换手和涨跌停。
2. Sector：有效期成员、行业指数、宽度、成交占比、Leader、估值和七周期。
3. ETF：1d/5m、实际 IOPV、同刻 premium/spread、tracking、fees、流动性和执行 veto。
4. Stock：1d/5m、财报发布/更正版本、质量成长、现金流/资产负债、行业估值和执行风险。
5. Canonical/V2.5：相关性去重、四类 veto 和完整可读事实。
6. Learning：Ledger→Outcome→合法 PIT→OOT→Logistic→独立校准→冻结 Shadow→显式 Promotion。

## 7. GitHub、本地和私人资产的边界

GitHub/完整 bundle 保存：源码、测试、AGENTS、路线图、Stage JSON、失败谱系、不可变 hash、开放关口和恢复顺序。

Public GitHub 不保存：API Key、Secret、私人持仓、原始 Ledger/Outcome/PIT、训练数据、模型状态、邮件内容或未获再分发权利的原始响应。缺失时，代码开发仍可继续；相关 Product、PIT、训练和 Promotion 保持 NOT_ADMITTED。

因此“无压缩无遗漏”是指：开发所需的决定、合同、代码、测试、状态、失败历史和下一关可由 exact Git 与本地指针完整重建；私人和无权公开的运行资产以合法缺失表示，不靠泄露换连续性。

## 8. 每个 material checkpoint 的同步规则

同一 checkpoint 必须同步：

- `AGENTS.md`（仅规则变化时）
- `docs/stock-dsa-roadmap.md`
- `docs/stock-dsa-stage-baseline-v1.json`
- `docs/CHANGELOG.md`
- 对应源码和独立正反测试
- 必要时本文件和 verifier
- 本机外部 `STOCK_DSA_CURRENT.json` 指向 exact repo/branch/HEAD/worktree

提交前运行相关回归、`python scripts/check_ai_assets.py`、`git diff --check` 和 reentry verifier。普通非 force push 只到 current feature；不直接 push/merge `main`。