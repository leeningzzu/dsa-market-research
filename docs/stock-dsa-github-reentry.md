# STOCK_DSA GitHub 无压缩续接入口

REENTRY_ID = STOCK_DSA_GITHUB_REENTRY_V1

本文件解决一个具体问题：**换账号、换电脑或新 Chat 时，只凭 canonical GitHub 仓库即可继续 STOCK_DSA 的代码实施，不依赖旧聊天摘要，不丢失父目标、当前关口、失败谱系或回滚边界。**

它是恢复入口，不是第九个 Project Source，不是第二个 Production Shell，也不替代实际 Git、CI、数据来源或运行回执。

## 1. Canonical 身份

- Production Shell：`DSA/daily_stock_analysis`
- Canonical repository：`https://github.com/leeningzzu/dsa-market-research.git`
- Production branch：`main`
- 当前开发分支：`feat/stock-dsa-m2-source-gates-v1`
- 本 checkpoint 的父提交：`b8f5d9258b717cf08d9ad6590e6143ee7e0e15ce`
- 当前 commit：不得从本文预填；运行 `git log -1 --format=%H -- docs/stock-dsa-github-reentry.md`
- 原始 V2.5：125919 字节，SHA256 `039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823`
- 接受的原机器图：149628 字节、775 边，SHA256 `b4f69f76807ac44bc0fea1a03f9aa38484b6083ca574a3699220b0564ac55aed`

目录名、旧聊天、旧 branch、缓存 `origin/main`、Project Source 版本和 README 都不能单独证明 currentness。

## 2. 新账号必须按此顺序读取

1. `AGENTS.md`
2. 本文件 `docs/stock-dsa-github-reentry.md`
3. `docs/stock-dsa-roadmap.md` 顶部 `CURRENT_MASTER_CONTROL`
4. `docs/stock-dsa-stage-baseline-v1.json`
5. `docs/CHANGELOG.md` 的 `[Unreleased]`
6. 当前变更涉及的源码和独立测试

随后执行：

```bash
python scripts/verify_stock_dsa_reentry.py --json
```

任何 STOP 都必须先按输出定位 Git、缺文件、计数、immutable hash 或文档漂移；不能用旧聊天猜 current。

## 3. 新电脑／新账号恢复步骤

```bash
git clone https://github.com/leeningzzu/dsa-market-research.git
cd dsa-market-research
git fetch --all --prune
git switch --detach origin/feat/stock-dsa-m2-source-gates-v1
python scripts/verify_stock_dsa_reentry.py --json --require-feature-ref
```

若 verifier PASS，再让新的 Chat/WebCodex 执行下面的首条 Prompt。不要先切 `main`，不要从旧公开 fork、private predecessor 或本地缓存恢复。

## 4. 新账号首条 Prompt

```text
你是 STOCK_DSA 实施主线助手。只使用当前 clone 的 canonical repository：
https://github.com/leeningzzu/dsa-market-research.git

先调用 WebCodex Local，读取并遵循 AGENTS.md；再依次读取：
1) docs/stock-dsa-github-reentry.md
2) docs/stock-dsa-roadmap.md 顶部 CURRENT_MASTER_CONTROL
3) docs/stock-dsa-stage-baseline-v1.json
4) docs/CHANGELOG.md [Unreleased]

实际执行 python scripts/verify_stock_dsa_reentry.py --json，并核对 origin、HEAD、worktree、feature remote ref。任何冲突 fail-closed，不使用旧聊天或目录名猜 current。

保持 DSA/daily_stock_analysis 为唯一 Production Shell；不建立第二套数据库、调度器、报告或模型权威。原 V2.5 必须保持 125919 字节和固定 SHA256。已关闭关口不得重跑。当前代码投影为 43 个方法 Profile、35 个现有、8 个延期、35/35 技术数值叶、56 个七周期×八族格、189 个方法－周期执行对；这不等于真实 Product 全部 READY。真实 CURRENT Product 保持 13/72，Market 0/18，FACT_READY=false，直到同次合法来源、Rights-v2、B1、Registry、canonical 和原 V2.5 真正闭合。

唯一主路线：继续 MARKET_COMPLETED_SESSION_PARENT_GATE，先接入官方 M_INDEX_CURRENT 当前完成日数值，再处理 SSE+SZSE+BSE 全市场宽度和统一成交口径。缺数据或权利时只 STOP 受影响对象，不停止其他独立 Lane。

未经明确授权不得 merge/main、force push、读取 Secret、发送邮件、训练或 Promotion。
```

## 5. 当前代码和机器投影

- 方法 Profile：43
- 现有确定性方法：35
- 延期并有 Owner/Re-entry：8
- 技术数值叶：35/35
- 七周期×八证据族：56 格
- 方法－周期执行对：189
- 数据对象：76
- 当前真实 Product：13/72
- Market：0/18
- 原 V2.5：`FACT_READY=false`

11 个补齐叶为 ADX、+DI、−DI、Bollinger 上下轨、KDJ K/D/J、OBV、ADL、MFI。唯一新增公式 Owner 为 `src/services/extended_indicator_service.py`；消费者仍是原 `volatility_momentum_service→Registry→Factor→V2.5`。

算法闭合不授予数据使用权、BUY、概率、PIT、训练或 Promotion。

## 6. 剩余六个复杂关口

1. Market：完成日指数、全 A+BSE 分母、停牌、宽度、成交额/换手、涨跌停和 Rights-v2。
2. Sector：有效期成员、行业指数、宽度、成交占比、Leader、估值和七周期。
3. ETF：1d/5m、实际 IOPV、同刻 premium/spread、tracking、fees、流动性和执行 veto。
4. Stock：1d/5m、财报发布/更正版本、质量成长、现金流/资产负债、行业估值和执行风险。
5. Canonical/V2.5：相关性去重、Data/Risk/Liquidity/Execution 四类 veto 和完整可读事实。
6. Learning：不可变 Ledger→成熟 Outcome→合法 PIT→OOT→Logistic→独立校准→冻结 Shadow→显式 Promotion。

## 7. Closed-no-replay

以下已有接受证据，不因换账号重跑：

- Global Product5 rebuild
- Product11 current checkpoint
- CSI sector daily RS20 source gate
- BLS known-at clock root fix
- Market ranking stale refresh
- Product13 code consumer CI

新账号只验证这些接受证据的 Git 身份和当前引用，不拿旧运行冒充新 Product。

## 8. GitHub 能保证和不能保证的内容

GitHub 保存：源码、测试、路线图、Stage JSON、机器投影、失败谱系、不可变 hash、开放关口、恢复顺序和首条 Prompt。

Public GitHub 不保存：API Key、Secret、私人持仓、原始 Ledger/Outcome/PIT、训练数据、模型状态、邮件内容或本地临时响应。它们缺失时，代码开发仍可继续；相关真实运行、训练和 Promotion 必须保持 NOT_ADMITTED，直到另行合法恢复。

因此“无压缩无遗漏”是指：**开发所需的决策、合同、代码、测试、状态和下一关全部在仓库内可重建；私人运行资产不以泄露换取连续性。**

## 9. 每个 material checkpoint 的同步规则

同一 commit 必须同步：

- `docs/stock-dsa-roadmap.md`
- `docs/stock-dsa-stage-baseline-v1.json`
- `docs/CHANGELOG.md`
- 对应源码和独立正反测试
- 必要时本文件和 verifier

提交前运行相关回归、`python scripts/check_ai_assets.py`、`git diff --check` 和 reentry verifier。普通非 force push 到 current feature 后，再核对远端 ref；不直接 push/merge `main`。
