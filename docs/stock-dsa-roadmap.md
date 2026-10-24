# STOCK_DSA GitHub基线与总路线

> 本文是现有DSA机器图、执行合同和运行证据的维护者视图，不是新的Source、策略、数据库、调度器或报告权威。机器可读不变量见 `docs/stock-dsa-stage-baseline-v1.json`；真实状态仍由GitHub代码、CI、运行回执和合法数据证据决定。

<!-- CURRENT_MASTER_CONTROL_START -->
## 当前控制页：总目标、依赖图与退出条件（2026-10-24）

CONTROL_ID = STOCK_DSA_MASTER_ROUTE_20261024_B1_PRODUCTION_WIRING_CI80_ACCEPTANCE
CURRENT_CANDIDATE_HEAD = 26ecd5d0d6141af810504e2092db260ab3898fb6
CURRENT_CANDIDATE_PARENT = c6cce86c8e71a677659510b6e916fd2c41c18d85
LAST_VERIFIED_MAIN_BASELINE = b32570b1562deaefa99f500a9c68eb7d1caf6f56
CURRENT_PR = #31 / Draft / Open / not merged
LATEST_REQUIRED_CI = #80 / Run40055753549 / success on 26ecd5d0 (Linux Offline, governance, Docker, detection, desktop packages)
SOURCE_CHECKPOINT = Current Project 01/05 authority; v3.504 physically Gate B PASS on 2026-10-24; next paired checkpoint follows this accepted route-state sync
AUTHORITY = exact Git/运行证据 > current 01 > 02/03 > 04/05 > Factor > 06 > 旧Chat

**这是一份现有路线的维护者状态投影，不是新架构、第二个Source、第二数据库或调度器。原775边图和V2.5原件保持不变；旧dated checkpoint继续作为历史。**

### 0.1 总目标与成功定义

在DSA/daily_stock_analysis内，做出能够解释“为何入选、为何不选、何时成立、何时失效”的股票/ETF研究，并逐步积累合法、可复现的结果证据，检验净期望与校准质量。不是追求指标最多、测试最多、分数最高或单项胜率最大，也不承诺收益、不自动交易。

产品完成至少有五个相互独立的证明：来源/时点/权利合法；方法数值与确认事件正确；候选与veto/唯一canonical一致；完整Report/Email实际消费且交付证据达到当前要求；学习结论来自合法机会分母及独立时间外验证。工程CI绿不能替代后四者，收到邮件也不能替代前面事实。

五级对齐：Program研究可信与可检验净价值 → Product市场/行业/ETF/股票与完整报告 → Lane B1/B5/M2/B6/B8/L1/M3 → Milestone一个可独立验收的纵向切片 → Atomic实际Owner、consumer、负例、合法nontrigger和回滚。横向始终有Data/Risk/Liquidity/Execution veto、许可/隐私、known-at、版本身份和Stop-Loss。

### 0.2 当前事实与本次纠偏

- 工程与消费者基线：G0 CI #70、AUTO Focus CI #71、路线投影 CI #72、Quality/九类Valuation进入Factor→Brief→Email的CI #73均为CLOSED_SCOPED_NO_REPLAY；它们不证明真实数据、策略动作或邮件交付完成。
- Operation-specific权利纠偏：CI #74对应提交5d1717e5虽为绿色，但独立反例证明其在底层来源条款`NOT_INDEPENDENTLY_VERIFIED`时仍可产生`PRODUCT_ADMITTED`，因此只保留为历史FALSE_POSITIVE。子提交858e0f34删除Provider专用权利服务和AkShare耦合，恢复到同一`data_object_admission` Owner：必须同时绑定当前operation、用途、运行时source/content identity、精确上游条款证据和四类rights；UNKNOWN只保留来源身份。346/346本地回归及CI #75成功，CLOSED_SCOPED_NO_REPLAY。
- 七周期carrier、7×8矩阵与runtime trace保持CLOSED_SCOPED_NO_REPLAY；43 Profile为28 reused/15 deferred，56格不等于全READY。动态获取窗口仍由方法Owner和consumer决定，不统一成一个lookback。
- B1生产接线工程已由26ecd5d0和CI #80闭合：同一DSA入口现在在任何Provider/缓存/DB/模型/报告/通知前校验operation、目标证券、Stock/ETF各自1d+5m、Provider和四类rights，并把实际source/content/单位/复权/revision/vintage交给原Registry→Factor/canonical→原完整NO_SEND报告；无授权或缺任一运行时事实即fail-closed。当前主产品缺口仍是外部事实：真实上游条款包及同一as-of普通股票+ETF实际回执，另有适用业务估值、30m→15m→5m触发与四类veto；M2.3与学习/Promotion继续HOLD。
- Source连续性：当前Project物理checkpoint为配对v3.504且Gate B PASS；CI #80材料性接受和后继路线同步完成后，01/05才成对推进，不重传其余六个Owner。

### 0.3 详细依赖图：实线是数据/决策依赖，虚线是诊断或显式后继反馈

下图为原接受图的可读依赖投影；节点出现不等于节点实际执行。状态和证据见0.4/0.5，运行层仍由原Registry/Factor/Diagnostics负责。

```mermaid
flowchart TD
  ENTRY_AUTO["AUTO_SCREEN：股票/ETF独立候选池"] --> REF["证券身份/行业分类版本/历史成员/交易日历"]
  ENTRY_CODES["SPECIFIED_CODES：绕过粗筛，不占AUTO配额"] --> SELECTED["有限深析对象与必要的市场/行业上下文"]
  REF --> ADMISSION["逐数据对象：provider/known-at/单位/复权/权利/完成bar"]
  ADMISSION --> SHARED["代表市场/行业上下文：共享一次，不逐股票重复抓取"]
  ADMISSION --> COARSE["廉价日线/横截面粗筛：流动性/数据质量/资产适用性"]
  GLOBAL["Global原生频率：只能确认/降级/失效/不足"] --> SHARED
  SHARED --> FINE["Stock：行业/peer RS+质量估值；ETF：peer RS+交易与跟踪质量"]
  COARSE --> FINE
  FINE --> CAP["AUTO各类最多10、重点各3；允许0；预算跳过不等于否决"]
  CAP --> SELECTED
  SHARED --> SELECTED
  SELECTED --> DEMAND["现有MethodWindowProfile：逐资产/周期/方法需求"]
  DEMAND --> PLAN["共享请求规划：窗口、预算、源保留/分页能力"]
  ADMISSION --> PLAN
  PLAN --> BASE_D["实际完成1d：不把尚未收盘数据标完成"]
  PLAN --> BASE_5["实际完成5m：合法连续会话/时段/数据缺口"]
  BASE_D --> DWM["同一Owner：日/周/月；未完成周/月剔除"]
  BASE_5 --> MINUTE["同一Owner：5/15/30/60m；上午下午分别聚合"]
  DWM --> METHODS["每方法：公式/多起点稳定/结构确认/prefix/失效"]
  MINUTE --> METHODS
  METHODS --> MATRIX["asset×timeframe×method：READY/PARTIAL/MISSING/UNKNOWN/N-A"]
  EXTRA["非K线原生证据：宽度、财报vintage、估值、NAV/IOPV、份额、点差/跟踪"] --> EIGHT["八证据族+独立非技术材料；同源同摆动去重"]
  MATRIX --> EIGHT
  EIGHT --> STRATEGY["白盒策略条件：现行stock/ETF路由；不把分数当概率"]
  VETO["Data/Risk/Liquidity/Execution硬否决：不可平均抵消"] --> CANONICAL["唯一canonical：支持/反证/触发/失效/缺失/时间"]
  STRATEGY --> CANONICAL
  CANONICAL --> REPORT["同一完整Report/Email；LLM只解释；V2.5原件不改"]
  REPORT --> DELIVERY["获准发送/实际收件：单独验收，不复活已关闭运输阶段"]
  CANONICAL --> RECORD_GATE["独立存储许可与记录权限；未知即HOLD"]
  RECORD_GATE --> LEDGER["合法不可变Journal/Ledger：TAKE/PASS/WAIT/否决/未触发/未成交"]
  LEDGER --> OUTCOME["成熟Outcome+冻结label/成本/执行规则+PIT"]
  OUTCOME --> OOT["白盒基线/时间外walk-forward/相关组消融/选择偏差审计"]
  OOT --> LOGISTIC["Logistic；仅有增量才LightGBM"]
  LOGISTIC --> CALIB["独立校准+冻结Shadow"]
  CALIB --> PROMOTE["显式Promotion绑定代码/数据/特征/策略/模型/校准器"]
  PROMOTE -. "只产生未来新版本，不改写历史" .-> STRATEGY
  PLAN -. "requested vs returned" .-> OPS["工程旁路：阶段计数/耗时/失败/缺口/预算跳过"]
  MATRIX -. "readiness与consumer缺口" .-> OPS
  COARSE -. "排除理由与预算分开" .-> OPS
  DELIVERY -. "交付证据" .-> OPS
  OPS --> REVIEW["Role Review/同根复发/净负担：KEEP/简化/复用/延期"]
  REVIEW -. "明确批准后改变下一版本" .-> DEMAND
```

**不是“大盘必须全好→行业必须全好→ETF必须全好→个股才能分析”的串行等待。** 市场/行业上下文是共享依赖；ETF和个股是兄弟输出路线，不是先选ETF才有资格分析个股。某个required veto缺失时相应策略不得行动，但其他合法证据和SPECIFIED_CODES研究仍可产出不足说明。

### 0.4 九条保留工作线：每次Closure都核对，不以局部替代总体

| 稳定工作线 | 范围与现状 | 必须保留的下一验收证据 |
|---|---|---|
| N1 日→周/月 | 个股/ETF样本与现有动态窗口已有；市场/行业逐对象未全验 | completed与边界、源口径、逐方法warm-up/recursive、真实数值进入同一consumer |
| N2 5m→15/30/60m | 个股样本；ETF部分；指数一次实源32会话但适配后续断连；行业缺源 | requested/returned/required分开，按会话聚合、单位与复权核对、15/5确认仍OPEN |
| N3 Global | 目标完整但各原生数据与known-at未全准入 | 科技/通胀/名实利率/美元人民币/商品/资金风险；不能由Global单独升级BUY |
| N4 A股Market/Breadth及行业 | 指数当前来源与分钟来源级PARTIAL；分类查询不等于行业指数 | 历史Universe、宽度分母、行业价格/成员版本、RS与内部广度；不拿ETF静默当行业指数 |
| N5 ETF-specific | E_PRICE_1D/E_PRICE_5M来源级进展；专属数据多数UNKNOWN | NAV与IOPV区分、价格同一时点/原始价格单位、份额/折溢价/点差/跟踪/篮子 |
| N6 Stock hard gaps | 七周期主干已有；独立确认/估值/财报vintage/执行门禁未全好 | 质量成长/行业RS/适用估值、30m结构→15m确认→5m时点；原stock策略 |
| N7 Extra classic | 43方法登记含延期；不是43方法已算/全部已准入 | 均线/量价/成本/结构优先；Chan窄语义与逐根replay；Wave/其他同类指标按触发延期 |
| N8 B8A完整产品 | 同canonical完整Report/Email本地消费多项通过 | 独立支持/反证/估值/风险/触发/失效/时间原子守恒；实收证据另验，静态原件运输不重跑 |
| N9 Learning/Model | 第二条链设计/代码部分存在；来源许可/PIT/OOT未准入 | 完整机会分母→合法Ledger→Outcome/PIT→白盒OOT→Logistic→条件LightGBM→校准/Shadow/Promotion |

### 0.5 接下来阶段目标、依赖与关闭标准

| 阶段 | 目标/现有Owner | 最小实施与退出证据 | 阻断范围 |
|---|---|---|---|
| G0工程接受 | PR #31候选26ecd5d0；CI70/71/72/73/75/76/78/79保留各自范围；CI80在26ecd5d0上Linux Offline、治理、Docker、检测及桌面打包全部SUCCESS；CI74仍是历史FALSE_POSITIVE、CI77仍是历史RED | 精确HEAD/parent/tree、30路径可回滚提交、非强制push和Git/API/PR独立读回通过；PR仍Draft/Open未merge | B1工程接线CLOSED_SCOPED_NO_REPLAY，不替真实授权包、真实Stock+ETF Product运行或Promotion；merge需要独立批准 |
| B1来源与权利 | 原data_object_admission、daily/intraday identity、Pipeline、Registry、Factor/canonical与原报告消费者 | CI80已证明唯一配置入口、精确operation/目标证券、Stock/ETF各自1d+5m、Provider白名单、运行时source/content/单位/复权/revision/vintage及四类rights可在原链fail-closed接通；UNKNOWN只保留来源身份 | 工程接线PASS_SCOPED；未取得并核验真实上游授权包及同一as-of实际回执时，不能宣称真实Product、持久化、训练、发送或策略准入 |
| B5窗口到方法 | 复用_current_daily_window_plan/_current_intraday_window_plan及Registry | 首个MACD/RSI纵向切片已把required/requested/returned/status/consumer_path接入MTF runtime trace；后继只在合法Stock+ETF实际回执上重算，缺失方法保持MISSING/DEFERRED | 阻断相应方法/策略，不一刀切全部周期，也不把静态56格当READY |
| M2上下文到筛选 | MarketStructure/RS/Screening/Factor | 廉价合法指标缩Universe，有限候选完整7TF；共享指数/行业结果；候选跳过/否决/缺数理由分开 | 不能承诺与全市场深析零漏选；保留研究型审计样本 |
| B6+B8策略与交付 | 原Screening/structure/pattern/Factor/Notification | Focus、Quality/Valuation消费和operation rights门禁已CI通过；后继用合法Stock+ETF实源闭合30m→15m→5m、四类veto和同canonical完整报告 | 不真实发信、交易、落库、训练或merge，除非各自另有明确授权 |
| L1/M3第二链 | 原History/Journal/Ledger/Outcome与后续实验Owner | 合法写读/PIT/冻结label/成本/选择偏差审计；训练/验证/测试/校准分离，Promotion显式 | B1/HOLD时只做schema/合成测试/只读研究，不训练真实数据 |

阶段是依赖关系，不是所有工作线强制串行。优先一个能够暴露共性缺口的真实纵向切片，保留另三类的显式OPEN；不复制四个分析引擎，也不长期停在一个测试模块。

### 0.6 动态窗口与减负的准确位置

窗口由(资产、价格对象、周期、方法、算法/config、as-of、source能力)决定。区分公式rolling窗、递归初始化/多起点比较窗、形态变长/anchor/确认窗、共享抓取跨度、label持有期、保留期、OOT切分。按相同源/口径/时点合并请求，按各自方法判断readiness；接口pagination上限、真实滚动保留底和单次缺包不可混淆。

前序32完整会话示例：1536根5m可推512根15m、256根30m、128根60m。它既不自动满足某60m递归算法，也不意味着5m/15m的每个合法短窗方法都失败。67日是当时最大方法要求的共享请求目标，不是永恒统一最低长度；数值接近之外，还要验证threshold附近信号状态与确认事件不发生不合法回填。不能通过提高容差、拼不同adjustment/source或无限重试伪造稳定。

减负顺序：市场/行业共享一次；只在完成bar或相关数据vintage/配置改变时重算；先粗筛后有限深析；同provider/资产/时点/复权的1d和5m基础帧复用；获独立存储许可前仅run-local重用，不加并行数据库。每次只保留一个新失败族的修复，纯提速不改排序；截断候选要评估漏选，不能声称数学等价。

### 0.7 八族、权重与第二链的边界

八族是解释/组织，不是八票。TREND_RS中的MA/RS、SUPPLY中的量价、COST代理、STRUCTURE中的pivot/Chan、MOMENTUM中的动量/波动、PATTERN事件、MTF职责分别保留。MACD背离/Chan背驰/衰竭若同源同摆动应去重；ATR风险尺度与ROC方向速度不能只因同组就删掉。15个行政相关组不是统计聚类证明。质量成长/估值/ETF专属保持横向原生频率，不能靠七周期K线生成它们。

当前保留已有有界筛选偏好，不增加任意百分比，不把MISSING当利好，不用技术高分抵消veto。未来冗余/权重用合法PIT训练折内的成组消融、条件/分段比较和独立时间外结果决定；反馈处置限KEEP/CONFIRM、SIMPLIFY/MERGE/DROP_FROM_DECISION、CONDITION/SEGMENT。即便从决策中删除低增量指标，真实且要求披露的邮件材料不因此删减。效益指标同时看净期望/成本/回撤/周转/机会覆盖/校准，不以胜率单项决定。

产品链A：source→deterministic evidence→策略/veto→canonical→完整报告。产品链B：同一canonical合法记录→真实Outcome/PIT→OOT→Logistic/条件LightGBM→独立校准→Shadow→显式晋升。工程旁路只有实际计数/耗时/异常→复盘→下一版本；它不能冒充链B、不能把市场结果倒灌为过去特征，也不能自动改参数/阈值。

### 0.8 成熟项目决策与证据落点

| donor/现有能力 | KEEP/ADAPT/DEFER | 落点与限制 |
|---|---|---|
| DSA/已接受AlphaSift派生 | KEEP：原粗细筛/服务/Diagnostics | 真实execution_receipt已接，不新增筛选Shell；本次不扩充复盘Agent |
| LEAN官方market-hour consolidators | ADAPT模式：交易时段锚点、资源释放、公司行动边界 | 原XSHG bar service/period完成负例；不安装其broker/调度栈 |
| Freqtrade recursive/lookahead | ADAPT测试：多长度末端比较＋prefix/信号反例 | 原方法测试；单币/单资产切片不适合直接验证横截面RS；未触发信号不能当无lookahead证明 |
| CNEquity | ADAPT目录概念：history_mode/coverage/backfill vs snapshot-only | 下一四对象source-capability核验；官网开发树不等于本机已安装版本，注册数/文档范围不等于实得/版权 |
| OpenBB标准化 | ADAPT query/data模型分离 | 原typed identities；统一字段不等于来源独立认证；不安装完整平台 |
| CZSC | DEFER执行直到窄Chan适配trigger | FX/BI/ZS、confirmed事件；1.0 Rust和旧Python有接口差异，先pin/version/license/replay |
| Qlib/scikit-learn | DEFER真实学习，ADAPT PIT/成组重要性方法 | PIT需公告日/期末/修订版；不以当前回补替代历史vintage，不新增数据库权威 |

公开一次资料本轮已重新核验（2026-10-17）：LEAN https://www.quantconnect.com/docs/v2/writing-algorithms/consolidating-data/consolidator-types/market-hour-aware-consolidators ；Freqtrade https://www.freqtrade.io/en/stable/recursive-analysis/ 与 https://www.freqtrade.io/en/stable/lookahead-analysis/ ；CNEquity https://rootsunc.github.io/CNEquity/datasets/catalog/ ；OpenBB https://docs.openbb.co/odp/python/developer/standardization ；CZSC https://github.com/waditu/czsc ；Qlib https://qlib.readthedocs.io/en/latest/advanced/PIT.html ；相关特征 https://scikit-learn.org/stable/auto_examples/inspection/plot_permutation_importance_multicollinear.html 。这是模式/文档核验，不是安装、数据许可、接口全审计或收益证明。

### 0.9 Role Review、反例与唯一下一步

Role Review（26ecd5d0 / CI80）：Program继续KEEP唯一DSA；B1不再只是验证器定义，已从正式配置/显式上下文接入Pipeline获取前门禁、Provider白名单、Daily/Intraday Identity、76对象Registry、Factor/canonical和原完整NO_SEND内存报告。Stock/ETF的1d与5m各自独立，缺授权、错Provider/证券/consumer/hash/时钟/单位/复权/revision/vintage均拒绝；Product许可不解锁storage/training/redistribution/策略，Web/API/Bot/Broker/AUTO/通知/History/Ledger/模型旁路被阻断。CI80的Linux Offline、治理、Docker、检测和桌面打包全部成功；PR仍Draft/Open未merge。Product仍OPEN于真实上游授权包、同一as-of 600519+510300实际来源回执、非价格材料、完整触发/veto与真实交付；未使用PAT/安装/force/业务DB/真实Provider/发信/训练/merge，旧WIP与private predecessor未进入提交。

FIXED_STATUS_VECTOR = G0_CI80_B1_PRODUCTION_WIRING_PASS_SCOPED; CI77_HISTORICAL_RED_NO_REPLAY; B5_DYNAMIC_WINDOW_CI78_PASS_SCOPED; B8_FOCUS_CI_PASS_SCOPED; QUALITY_VALUATION_PRODUCT_CI73_PASS_SCOPED; OPERATION_RIGHTS_GATE_CI75_PASS_SCOPED; REAL_UPSTREAM_GRANT_OPEN; SAME_ASOF_STOCK_ETF_RUNTIME_OPEN; 7TF_CARRIER_IMPLEMENTED; 7TF_MATRIX_IMPLEMENTED; 7TF_RUNTIME_TRACE_SCOPED_PASS; METHOD_READINESS_PARTIAL; REAL_INPUT_OPEN; VALUATION_REAL_NUMERICS_OPEN; STRATEGY_TRIGGER_OPEN; M2_3_HOLD; LEARNING_PROMOTION_HOLD.

UNIQUE_NEXT_STEP = 使用CI80已接受的唯一B1生产入口，为同一as-of的一只普通股票和一只普通ETF取得精确上游条款原件/哈希/观察时点与operation-specific四类rights；通过现有data_object_admission后才调用获准Provider取得1d/5m及财务/ETF专属数据，形成真实运行回执并进入Registry→Quality/业务适用Valuation→7TF/veto/canonical→原V2.5 NO_SEND。无合法权利证据即fail-closed，不用SDK可用性、合成值、更多指标、第二Shell或旧WIP绕过。

PASS = exact operation/source/terms/consumer可追、Stock/ETF各自合法正例与缺失/过期/错单位/未来回填/无权利反例守边界、估值区间与敏感性诚实；STOP = 学术声明替代底层条款、Provider可调用替代数据许可、一类资产替两类、目录冒实值、UNKNOWN升READY、统一PE/WACC、技术分抵消veto、未经授权落库/发送/训练/merge。CURRENT_ROUTE_STILL_WORTH_CONTINUING=YES；INSTRUCTIONS_UPDATE_DECISION=NO_DELTA；SOURCES_UPDATE_DECISION=YES_PAIRED_01_05_AFTER_CI80_AND_ROUTE_SYNC_CI。

<!-- CURRENT_MASTER_CONTROL_END -->

## 1. GitHub是后续工程基线

所有可继续开发的能力必须进入当前公共仓库的功能分支，经Pull Request和阻断CI后才可成为新基线。禁止把长期能力只留在本地WIP、聊天摘要或Project Source中；也禁止直接把未经CI验证的WIP推到 `main`。

每个新基线至少绑定：

- exact origin、父提交、候选提交和工作树状态；
- 机器图与V2.5不可变哈希；
- 已执行测试及明确未执行测试；
- Program→Product→Lane→Milestone→Atomic纵向归属；
- B1权利、B5方法、B8交付、History/PIT和Promotion横向门禁；
- 已证明、未证明、失败谱系、回滚方式和唯一后继关口。

## 2. 三层机器图，不相互冒充

1. **接受的原始图**：149628字节、775边及其SHA；保持不可变。
2. **代码投影**：26研究义务、43方法Profile、13策略绑定、7×8=56技术格、76数据对象；由现有Registry编译，不另建图数据库。
3. **运行回执**：来源、完成bar、known-at、单位、复权、方法Owner和消费者真实执行；只有该层能够把某项状态推进到READY/PARTIAL。

静态计数用于防止能力被删除；不能证明数据已经取得、指标已经收敛或产品已经交付。

## 3. 五级纵向目标

| 层级 | 目标 | 当前证明上限 |
| --- | --- | --- |
| Program | 合法known-at数据→白盒证据→唯一canonical→完整交付→合法Outcome/PIT→独立校准→Shadow→Promotion | 不自动交易，不保证收益 |
| Product | Global→A股市场/行业→ETF/股票→七周期/基本面/估值/风险→FULL Report/Email | 单股票技术证据不能替代市场、ETF和基本面 |
| Lane | B1权利、B5来源/方法、B8完整报告、History/PIT、模型与Promotion | 各Lane独立门禁，不平均抵消 |
| Milestone | 当前控制页0.9指定一个有界纵向切片；下文dated milestone为历史证据 | 不等于43方法全部READY |
| Atomic | 复用既有Owner、消费者、测试和负例的最小改动 | 不建第二套Shell/DB/Registry/Report |

## 4. 阶段基线

### G0 GitHub基线

功能分支、PR、required CI和可回滚提交身份。只有PR阻断检查通过并显式合并，才形成后续开发基线。

### HISTORICAL — G0 PR #30 Linux CI六项失效修复时检查点（2026-10-16）

> 后继证据：PR30随后已在CI 38890306259通过并合并为main b32570b1；以下“尚未证明/等待”描述仅属于当时修复候选，不是当前PR状态。新的M2候选分支必须另验PR/CI。

实际GitHub CI日志为6348 passed / 6 failed / 30 deselected，四组为Backtest真实回测重取、轻量股票名的扩展日线窗口、原生零外部模型P0、市场复盘History/通知诊断三项。它们全部在本地原入口复现RED；根因分类为五处B1未证明存储许可的历史正向预期与一处旧2023-06-01的硬编码起点。不能把新B1存储VETO降级以制造History/Ledger、也不能让旧起点缩短七周期MACD/RSI窗口。

当前改动限定在四份既有原生测试，不改业务代码或不变量：真实隔离临时SQLite中的Backtest合成来源明确为test；轻量名称预取仍不触发实时名查询但要求独立交易日历/方法窗口起点2015-02-02而非已废弃2023-06-01；MarketReview来源未获存储许可时必须保持History 0行，仍保留in-memory通知诊断成功、失败原因和原query_id贯通；零外部模型P0使用真实typed+hash的合成日K验证原生确定性canonical为PROVEN/WAIT，但严格History first-write拒绝未知权利，PredictionLedger不得被调用，隔离库只允许合成StockDaily/基本面测试快照、0外部请求/0通知/0结果文件/0 PIT/0模型训练。旧P0预期的严格Ledger+History正向不可恢复，直到独立GRANT；保留原有独立合法非严格隔离Ledger测试，不修改其真实语义。

自然回归：Backtest/Prefetch 77/77、MarketReview及runtime/lock 48/48、B1/PIT/Flywheel 136/136、Stage Baseline/Pipeline 48/48 =309/309 selected native, windows-local only。受影响四份测试的原生消费者均执行，未删测、xfail或降低失败场景；类型化合成价格准入仅限温度隔离sqlite、不读取Secret、不安装依赖、不使真实供应商历史可写。**尚未证明 GitHub Linux全仓CI通过**：普通功能分支小提交push后必须由同一PR的自然check重新执行，不能因本地PASS直接merge。后续父主线仍为Global/CN/sector/ETF known-at与七周期共同canonical/FULL Report，不为修测试废除风控。Role Review: USER_DISCOVERED_CI_FAILURE=TRUE_POSITIVE、EARLIEST_DETECTION_POINT=PR阻断CI原生日志；PROCESS_CONSUMER/LANE_RECURRENT（旧测试合同）和DATA_MODEL（B1门禁）区分记录，CURRENT_ROUTE_STILL_WORTH_CONTINUING=YES；不扩大Instructions，Source更新等待PR明确检查点。

### B1 来源权利与第一写入

未知第三方数据的存储、训练和再分发权利继续在StockDaily、AnalysisHistory和Ledger最早写入点拒绝。类型化来源身份、qfq、内容SHA和PIT标记都不能代替许可。

### B5 七周期来源和方法

完成月/周/日/60m/30m/15m/5m bar、方法特定动态窗口、递归稳定性、来源身份、MA/MACD/RSI/结构等确定性输出，以及逐周期缺失状态。READY只属于对应方法和输入，不自动获得交易资格。

### B8 唯一canonical与完整无发送报告

76项义务、支持、反证、风险、触发/失效、时点和七周期逐指标明细进入同一FULL Report、Email和HTML。正式报告Email必需，Telegram可选；真实SMTP和收件客户端另验。

### M2 多资产known-at（ACTIVE）

优先接入可独立核验的A股指数/宽度、行业分类与相对强度、ETF价格/NAV/份额/折溢价/跟踪/篮子时点。它们只能确认、降级、否决或保持UNKNOWN，不能单独升级BUY。

运行检查点1已经落到现有DSA：CN MarketReview会在A股复盘中有界读取上证指数已完成日线来源，经原MarketReview→DailyMarketContext→REGIME严格方法回执→76数据对象，把 **M_INDEX_CURRENT** 从UNKNOWN推进为**仅来源身份PARTIAL**。真实2026-10-15只读回执已验证，指数值本身仍不作为独立策略投票；历史MarketReview缓存不得重获该同进程来源准入。

检查点2选择ETF价格而非机械推进指数/宽度：现有BaoStock对 sh.000001 的5m真实探测返回0行，不能用股票5m链伪造 M_INDEX_BARS；受控运行环境又缺AkShare，M_BASIC_BREADTH无法取得真实全A快照，禁止仅凭合成数据晋升。相反，510300在BaoStock实际共同可得日2026-10-15返回430根1d与10个完整交易日480根5m，同provider/qfq/CNY；严格ETF MTF回执现在只把 **E_PRICE_1D / E_PRICE_5M** 推进为来源级PARTIAL，5m量额单位、数据权利、历史PIT仍UNKNOWN，股票财务对象NOT_APPLICABLE，E_NAV_IOPV/E_SHARES/E_PREMIUM/E_SPREAD/E_TRACKING/E_BASKET继续UNKNOWN。下一原子优先补ETF专属NAV/份额/折溢价中可合法known-at的一项，或在AkShare运行能力合法恢复后回到市场宽度；不新增并行ETF技术Shell。

### M2大盘指数5m来源（候选，未晋升算法／策略）

在既有BaoStock sh.000001 5m接口真实返回0行后，对照AKShare公开的 index_zh_a_hist_min_em 东财近期指数分钟接口（https://github.com/akfamily/akshare/blob/main/docs/data/index/index.md）。2026-10-16一次受控直接只读请求实际收到1536根5m、32个完整交易日，8月26日至10月16日。随后同接口以及新适配器的独立实际请求遇到 RemoteDisconnected；不能宣称新适配器已稳定生产取数，更不能将32个交易日补为DSA当前约67日的分钟递归历史。

路线与成熟复用：RULE_EXISTS=YES，CURRENT_CAPABILITY=BaoStock指数已完成日K+原XSHG 5m聚合，DONOR=AKShare仅其指数接口查询结构，KEEP/WRAP=原data_provider最薄Python标准库索引请求；没有AKShare安装、新数据库、第二调度器或另建指数报告。src/services/intraday_bar_service 仍负责完成会话和5m→15m/30m/60m确定性半日内聚合，原DailyMarketContext/Factor/Registry/Report仍是唯一消费者。适配只允许精确sh.000001/sh.000300，单次有界HTTPS、禁止跳转、XSHG 48根完整会话、错指数/乱序/重复/改价/提前观测/混合hash一律拒绝。来源身份标记UNCLASSIFIED，fqt=0请求不等于独立证明响应真实复权基础，分钟量／额单位、货币、数据存储/训练/再分发权利及历史PIT全部UNKNOWN。

生产CN MarketReview只在当前或前一完整交易日有界读取：失败作为合法nontrigger，不改原完整文本。只有当前进程的源收据可进入DailyMarketContext；history缓存、伪造GRANTED/READY、变session/散列不可重新准入。REGIME严格方法回执通过后，76对象中的M_INDEX_BARS仅为来源级PARTIAL；M_INDEX_CURRENT另有BaoStock日线来源，M_BASIC_BREADTH继续UNKNOWN。完整Report／Email／HTML保留M_INDEX_BARS的PARTIAL、源身份、未准入原因及76项义务，Market Light、唯一canonical与买入动作都不变。新原生13项以及相邻市场/通知/聚合等模块受控回归；本地测试是合成Eastmoney接口形状，不能取代实况访问与收件端证明。

Role Review：Program长期DSA单一Research Shell→Product Global/CN市场/行业/ETF/股票全材料→Lane M2源准入及B5七周期→Milestone指数5m来源执行收据→Atomic标准库薄适配与原消费者投射。反对视角包括：上游网络不稳、32<67分钟动态历史、指数月/周/日与四分钟period的源/复权不完全同一口径、无独立历史Universe、未入门的数据权利、15m/5m短周期不应升级交易。借鉴LEAN市场时段合并及Warm Up交易禁入，Freqtrade recursive-analysis及lookahead-analysis的多起点比较/未来信息拒绝，只复用模式，不安装任何并行指标/调度/数据库。CURRENT_ROUTE_STILL_WORTH_CONTINUING=YES，但 FULL_7TF_INDEX_METHODS=NOT_PROVEN；下一合法阶段仍须独立真实长窗口、单位、known-at/PIT或切换行业/ETF已可核证据，不机械重试东财。

### L1 合法Ledger/Outcome/PIT

只有来源权利、as-of/vintage、完整机会分母、未触发/未成交、成熟label和不可变字节均合法时才进入。

### M3 模型和校准

白盒Logistic先行；仅有样本外增量才LightGBM；walk-forward、独立校准、冻结Shadow和显式Promotion分离。

## 4A. 跨Chat父路线复核与两条反馈链（2026-10-17）

### 已证明与未证明，禁止静态合同替代执行

本节是既有路线的执行澄清，不是第二份Source/策略/机器图。复核基点：远端main为b32570b1（PR30已绿色合并）；功能分支9531baf1含指数分钟源与六阶段合同，尚不可把该分支称作main生产能力。Project物理01/05仍v3.468，生成的v3.469不是已安装Owner；代码当前性取exact Git，旧State的WIP SHA不能倒退工程基线。历史资料/个人上下文/当前8 Source和本轮exact code可交叉核验，但未取得所有旧Chat全文或原R019包，不宣称无遗漏的全历史审计。

重要纠偏：compile_screening_funnel_contract只编译设计和既有权重，不实际执行所有六阶段。43方法恰归15组不是统计相关性已检验，注册所有56技术格不是逐资产真实运行通过。旧文中把ATR/ROC/OBV统作同类、或要求粗筛结果永远等于全Universe深析，均不得成为实施验收依据。纯缓存/批量化/重构必须保持结果等价；候选截断改变研究样本，需要记录budget_deferred与拒绝样本漏选审计，不能伪称零漏选。

### 父目标与横纵对齐

Program=合法、白盒、可验证可回滚的股票/ETF研究与独立概率校准，不自动交易/保证收益。Product=Global→大盘→行业→ETF/个股的完整研究和共同canonical/Report/Email。Lane=B1来源权利、B5四资产类七周期方法、M2独立市场/行业/ETF证据、B8无损交付、L1合法历史结果、M3验证校准，互不冒充。Milestone须以真实Owner→消费者→负例→运行回执关闭；Atomic一次关闭一个有界语义缺口。横向始终保留Data/Risk/Liquidity/Execution veto、版本/available_at、隐私/版权、同根复发/用户负担；任何好分数不能抵消veto。

### 四条价格链都要七周期，但不建立四套引擎

| 对象 | 必须绑定的价格对象 | 七周期工作方式 | 不能替代的非价格证据 |
|---|---|---|---|
| 大盘 | 明确全市场/大盘/成长等代表指数及price/total-return口径 | 每个代表指数共用日→周/月、5m→15/30/60m和相同方法Owner；不能只看指数涨幅 | 全市场宽度、成分分母、资金/杠杆、估值风险补偿 |
| 行业 | 正式行业/板块指数及分类版本；合成指数另需当时成员/权重/算法 | 同一七周期引擎；分类标签不能生成行业OHLCV，ETF不能无声明冒充行业指数 | 行业内宽度/中位收益、成员当时归属、行业盈利和估值 |
| ETF | 基金交易价格、份额单位、除权/分红处理与跟踪标的 | 与股票共用完成bar/原生指标，但ETF策略/peer专属 | NAV/IOPV、份额、溢价点差、跟踪、篮子；ETF自身无股票ROE龙头语义 |
| 个股 | 证券身份、同口径价量、公司行动vintage | 月周环境、日setup、60m桥接、30m结构、15m确认、5m时点 | 质量成长/适用估值、财报公告版本、行业/同业RS |

不是每个Universe成员每天计算7×43，也不是每一财务/宏观序列都重采样成七周期。粗筛用合法廉价日线/横截面；代表市场与行业共享计算；股票和ETF各自capped候选进入共同deep engine，SPECIFIED_CODES不经过粗筛也不占AUTO配额。产品上限stock/ETF各10、重点各3，实际运行预算可更小，允许0，禁止凑数。

### 动态窗口：复用已有方法编译器，不再新建registry

现有src/core/pipeline.py::_current_daily_window_plan与_current_intraday_window_plan已经按MethodWindowProfile/原方法Owner反推请求。要继续补的是逐资产/来源能力以及实际返回的消费，而非再造同名规划器。最小契约为：asset+instrument+timeframe+method+algorithm/config+target_completed_bar → formula minimum / recursive comparison / structure context / confirmation requirement →同source/复权的共享请求→actual returned bars/session coverage→每个方法独立readiness→已确认事件/失效→canonical消费者。固定rolling、递归初始化、形态变长、事件anchor、抓取跨度、持仓/label horizon、存储保留、OOT切分必须分开。

历史可得性要区分滚动保留天数、固定日期底、每次分页上限、快照only、可回补及独立vintage。分页只能突破单次响应上限，不能突破源端真实保留底。更换来源不能静默拼接口径；合法缓存才可持久化，不为累计长窗口绕过B1。

例如前序东财32个完整交易日1536根5m，可聚合512根15m、256根30m、128根60m。它不满足某些60m递归比较，却不能因此抹除合法短周期MA或已收敛方法；也不能因为5m够长就把60m判READY。67日只是当前某组方法要求的最大共享请求目标，不是所有指标统一最低窗。对数据足够但递归数值/状态仍不稳，仅有界扩窗；无变化/同类失败达到Stop-Loss则保留MISSING，不修改算法容差迎合输出。

### 两条产品因果链与工程旁路

链A（研究/交付）：合法known-at事实→确定性八族/七周期→相关来源与摆动去重→策略条件和独立veto→唯一canonical→原FULL Report/Email。LLM只能解释。

链B（第二条结果反馈链）：当时可存的TAKE/PASS/WAIT/否决/未触发/未成交记录→不可变Journal/Ledger→成熟Outcome及冻结label/成本/执行规则→PIT样本→滚动时间外验证→白盒Logistic→有增量才LightGBM→独立校准→冻结Shadow→显式Promotion。没有合法存储授权和当时版本，不以当前回补财报/行情形成历史PIT，不只采成功邮件。不得让未来Outcome改变当时窗口、特征、样本名单或确认时点。胜率之外须看净期望、回撤、周转/滑点、覆盖及校准；不同预测期限分别定义label。

工程反馈（旁路，不取代链B）：actual stage counts/latency/error/预算跳过→Expected-vs-Observed→TP/FP/FN/LATE+earliest detection→实例/同Lane/跨Lane根因→KEEP/REUSE/SIMPLIFY/DELETE/BYPASS/RECOMPOSE/NARROW/DEFER→独立负例和合法nontrigger。耗时、HTTP错误不能作为市场成功label；历史查询变快不等于胜率提高。自动诊断只生成待审证据，不自动改阈值/策略/模型或训练/Promotion。

### 本轮实际消费者增量

screening.pipeline.screen实际写入ScreenResult.execution_receipt，普通ScreeningService响应和AUTO资产路由响应携带同一元数据。不是在编译器中写几个布尔值自证完成：每次经过snapshot、host_prefilter、snapshot_filter、daily_enrichment、daily_filter、shortlist、post_analysis、selection才记录数量/monotonic耗时；未请求NOT_REQUESTED，提前返回未到达NOT_REACHED；日线预算跳过与实际失败、过滤排除分开；DSA completed/failed/skipped在最终截断前统计。

此回执仅run-local聚合执行元数据，不含原始行情/私人名单/Secret，不新增表或scheduler，不改变分数、候选排序及canonical。provider_http_request_count=None，因为高层fetch调用数不能证明HTTP重试/缓存次数。completed DSA调用也不证明全部7周期READY。当前AUTO便宜筛选入口明确不做DSA deep，后续core pipeline才深析，因此在此回执中NOT_REQUESTED合法；跨core的7TF逐方法manifest join、fatal exception回执、精确网络计数、全Universe耗时与漏选率仍未证明。不得把本轮工程计数宣称为第二链已获得可训练样本。

本轮实际验证：新增4个原入口用例先4 RED（缺execution_receipt），最窄实现后GREEN；进一步检验真实DSA post-analyzer状态和daily全拒绝；AUTO最终provenance曾丢弃内部回执，独立RED后在现有出口保留stock/etf execution_receipts，最终新增7项PASS，另4项既有AUTO测试同次PASS。现有Builtin Screening 31项和Stage Baseline 6项通过。宽范围Screening API+Selection共119项中114 PASS/5 FAIL；5项为旧hotspot/缺AkShare与deadline/fallback路径，完整环境等价性未证明，不能改断言/安装依赖或声称全套CI绿。该宽测与一次模块导入曾意外尝试东财/LiteLLM公开metadata请求并失败，属于F2测试隔离缺口，不是新授权实源/模型调用；最后定向验证已对外部socket拒绝。后续不重跑同族失败碰运气，当前功能分支新PR/Linux CI仍需独立确认。

### 增量价值与非冗余信息

八证据族保留为组织视图。相关性还需至少按经济问题、原始数据、同一摆动/事件、周期职责区分，不能一个粗group抹掉全部维度。ATR/realized volatility用于风险尺度和归一化，ROC描述方向速度，MACD/RSI描述动量位置；CMF/OBV/MFI是量价代理，不等于纯价格动量。Chan分型/笔/中枢属结构，背驰与同摆动MACD背离去重，买卖点还需确认/执行；普通pivot不能冒充Chan。VCP/杯柄/均线粘连可能同一收缩证据，但有效突破和后续回踩是不同已确认事件。统计聚类、消融、条件增量和学习权重只在训练/验证折内做，不能看最终测试集选择指标。

### 分阶段最小路线与退出证据

| 次序/阶段 | 当前最小产物 | 主Owner | 退出证据/不能冒充 |
|---|---|---|---|
| 0 工程续接 | 当前branch/main/PR身份、原图/V2.5、Source checkpoint | Git + 01/05 | exact SHA及绿色CI；不是源数据/收益通过 |
| 1 本轮运行回执 | 八个实际筛选seam计数/耗时，零候选/预算/失败非触发 | screening.pipeline/models/service | 原入口RED→GREEN、排序不变；不是自动调参 |
| 2 四对象来源覆盖 | 代表指数、一个行业指数、ETF、个股的1d/5m可得窗+权利/单位/口径manifest | data_provider + existing identities | 一份真实正例/失败反例/无请求nontrigger；先核缺源，不新装整湖 |
| 3 方法/窗口接线 | 同一asset/timeframe/method实际requested/returned/required/stability/status加入已有MTF诊断 | core pipeline + MTF/Registry | prefix与多起点稳定；不会全因60m缺口取消5m方法 |
| 4 资产选择闭环 | 市场/行业RS宽度与ETF专属资格+候选短名单→同一7TF/canonical | MarketStructure/Screening/Factor | 前后代码、cap、错资产/veto负例；不全市场重跑深析 |
| 5 确认/执行 | 30m结构、15m确认、5m时点、点差/限价/可成交条件 | existing structure/pattern/MTF | origin/confirmed/available/invalidation逐根；不把当前信号当成交 |
| 6 第二链 | 合法完整机会分母、Outcome/PIT/成本、Logistic/OOT/校准 | existing History/Ledger/Outcome | 许可+合法写读+独立时间验证；B1仍HOLD则不训练 |

这些是有依赖的工作队列而非所有Lane强制串行：一个指数分钟源失败不阻止个股/ETF的合法证据、行业标签核验或工程旁路；但缺某策略required source时该策略动作仍WAIT/PASS，不能拿兄弟Lane成功覆盖。

### 本轮成熟项目重查：模式转化而非整栈安装

- DSA自身/已接受AlphaSift派生：保留实际粗筛、cap、post状态、服务响应，是本次唯一代码复用入口。
- LEAN：交易时段聚合/动态Universe资源释放、warm-up与交易隔离，转成原XSHG聚合/prefix负例；不安装交易引擎。官方：https://www.quantconnect.com/docs/v2/writing-algorithms/consolidating-data/consolidator-types/market-hour-aware-consolidators
- Freqtrade：不同startup末端数值比较；lookahead未触发信号会漏检，对横截面排名还需保留同一Universe不能只切成单资产。官方：https://www.freqtrade.io/en/stable/recursive-analysis/ 和 https://www.freqtrade.io/en/stable/lookahead-analysis/
- CNEquity：coverage_start/end、history_mode/backfill_source、snapshot-only不造历史、分钟按需派生；当前官网跟随开发树且可能不同已冻结v0.12，文档窗口不是实测授权/完整数据。本轮未升级或安装。官方：https://rootsunc.github.io/CNEquity/datasets/catalog/
- OpenBB：QueryParams/Data分离及字段验证转为现有typed adapter审计，软件schema不证明源语义或权利。官方：https://docs.openbb.co/odp/python/developer/standardization
- Qlib：财报publication/period/revision PIT模式；文档PIT有季度/年度设计边界，不是通用分钟数据许可。当前仅未来实验donor。官方：https://qlib.readthedocs.io/en/latest/advanced/PIT.html
- CZSC：FX/BI/ZS和多级别bar/event，当前1.0 Rust与旧0.9 Python不同，窄adapter必须固定版本/许可/ABI并逐根重放，不接第二交易器。官方：https://github.com/waditu/czsc
- scikit-learn：相关特征的单变量permutation可低估整体信息，借鉴成组消融/训练折内聚类而非用随机拆分证明金融收益。官方：https://scikit-learn.org/stable/auto_examples/inspection/plot_permutation_importance_multicollinear.html

Role Review结论：PRODUCT=四资产七周期和第二链保持OPEN；PROCESS_CONSUMER=静态清单→实际执行计数本轮改进；DATA_MODEL=时点/单位/授权/label继续硬门禁；DEV_TOOLING=功能分支自然回归再push；AUTHORITY_CURRENTNESS=实际Git领先已安装State需paired checkpoint。USER_DISCOVERY=TRUE_POSITIVE（再次提醒七周期不可被粗筛或工程复盘替代），EARLIEST_DETECTION_POINT=前次六阶段合同说明，AUTO_RETROSPECTIVE_BEFORE_USER_REMINDER=NO仍保留历史；本轮主动找到的边界纠正不构成系统长期自动复盘稳定证明。R019 exact包未恢复，仅复用已在03/Factor授权的父目标/consumer、成熟复用/负例、自然回归/净负担三个模式。CURRENT_ROUTE_STILL_WORTH_CONTINUING=YES；不更新Instructions、不建新Shell，更新必要01/05 checkpoint而非全8份重写。

## 5. 粗筛→细选运行时机器图、证据组合与权重

本节是原775边接受图和Registry的**派生运行投影**，不是第二套机器图、评分器或Product权威。机器可读版本由 `compile_screening_funnel_contract()` 与 `stock-dsa-stage-baseline-v1.json` 共同锁定。

```text
L0 Source Admission
  provider / symbol / completed_bar / known_at / available_at / units / rights
        │  不满足即UNKNOWN或拒绝；不计分
        ▼
L1 Global / Market / Sector Permission
  Global只确认/降级/失效；Market/Sector风险可veto，不独立升级BUY
        │
        ▼
L2 Asset Coarse Filter（便宜、全Universe）
  Stock: currentness + daily bar + liquidity + market + sector classification
  ETF:   currentness + daily bar + liquidity/spread + market + peer universe
        │  只缩小Universe，不跑全量7TF
        ▼
L3 Asset Fine Rank（仍为确定性排序，不是概率）
  Stock: sector/peer RS → quality/growth → valuation → daily setup → execution
  ETF:   peer RS → liquidity/spread → NAV/premium/tracking → shares flow → daily setup
        │
        ▼
L4 Bounded Deep Analysis + 7TF Overlay
  M/W context → D setup → 60/30 bridge/structure → 15/5 confirmation/timing
        │  严格MTF receipt + canonical binding；只对Top-K执行
        ▼
L5 Correlation Dedup + Hard Veto + Canonical + FULL Report/Email
        │
        ▼
Legal Outcome / PIT / OOT / Calibration / Shadow / Promotion
```

### 5.1 邮件中的八个技术证据组如何组合

八组继续保留，但每组只回答一个主问题；质量成长、估值、ETF专属和Data/Risk/Liquidity/Execution不硬塞进八组，而作为独立横向Lane。

动态邮件的阅读顺序固定为：canonical结论 → 市场/行业许可 → 股票质量/估值或ETF专属 → 八证据族 → 七周期职责摘要 → 相关性去重 → Data/Risk/Liquidity/Execution veto → 触发/失效 → 来源/时点/权利/缺失。顺序变化不得删除材料；V2.5静态接受原件保持字节不变。

| 证据组 | 主问题 | 主要内容 | 去重规则 |
| --- | --- | --- | --- |
| REGIME | 当前环境是否许可 | Global、Market Regime、Breadth、Sector Phase | 绿灯只能确认，红灯可降级/否决 |
| TREND_RS | 相对方向是否有优势 | 相对强度、MA排列、斜率、粘连/释放 | MA和RS同属trend_price，不逐指标投票 |
| SUPPLY | 供需/量价是否确认 | 相对量、方向量、CMF、派发风险 | 与OBV/MFI等共属volume_pressure |
| COST | 当前价格相对成本如何 | rolling cost、VWAP/AVWAP/Volume Profile | 同属price_volume_cost，只保留一份独立贡献 |
| STRUCTURE | 已确认价格结构是什么 | pivot、breakout/retest/invalidation、Chan分型/笔/中枢、Wave Shadow | 普通pivot、Chan、Wave共属confirmed_price_swing；Chan不得冒充额外一票 |
| MOMENTUM | 同一摆动的动量/波动怎样 | MACD、RSI、ROC、ATR、confirmed divergence、扩展oscillator | 同一摆动共属same_swing_momentum，最多一份独立贡献 |
| PATTERN | 是否形成可执行形态 | VCP、杯柄、双底、flat/tight base、candlestick | 收缩/底部形态共属price_contraction，不能多次加分 |
| MTF | 各周期职责是否一致 | 月周环境、日setup、60/30结构、15/5确认时点 | 不重算前七组，不作为第九票，只做层级一致性修正 |

Chan仅作为窄 `ChanFeatureAdapter`：分型/笔/中枢进入STRUCTURE；背驰若基于同一摆动进入MOMENTUM相关组；买卖点只有完成bar、confirmed_at、invalidation和算法版本齐全时才可进入PATTERN/MTF。一般pivot不得改名为Chan。

### 5.2 当前实际权重，而不是永久权重

权重分三层：

1. **Gate无权重**：Data/Risk/Liquidity/Execution和hard veto不可被平均抵消；失败直接拒绝或降级。
2. **当前可执行的确定性权重**：只有现有严格7TF overlay已获准使用数值；context `+1.25/-2.0`，daily setup `+1.75/-2.5`，60/30 bridge-structure `+0.75/-1.0`，15/5确认仅在高/日周期支持时 `+0.25`，不利时 `-0.5`，总边界 `[-6,+4]`。
3. **学习型权重**：质量、估值、行业、ETF专属及各技术族的最终权重，必须等合法Outcome/PIT、walk-forward和独立OOT后再由白盒Logistic估计；在此之前只使用优先级、family cap和coverage，不把启发式分数叫胜率或概率。

MISSING/UNKNOWN不是0分好消息，也不是负分；它们是`zero vote + lower coverage`。同一correlation group无论有多少指标最多贡献一次。报告必须同时显示score和coverage，避免缺失数据通过归一化反而提高排名。

### 5.3 是否必须先把七周期全部跑通才讨论筛选

不需要等待43种方法和56格全部READY才设计粗筛/细选；但**任何被消费的周期/指标必须先有完成bar、来源身份、方法回执和readiness**。当前顺序是：

- 先用便宜L0-L3缩到有限候选；
- 只对Top-K执行7TF深析；
- 已READY方法进入相应family，MISSING/UNKNOWN原样保留；
- 当月RSI、15m确认、5m时点或单位未来补齐时，自动增加coverage，不改机器图或另建评分器。

因此“先完成全部7TF再筛选”会浪费资源；“没验证7TF就先加权”同样错误。正确做法是**架构先定、方法逐项准入、权重后验校准**。

### 5.4 数据库到指标的最简实现链

```text
Provider Adapter
→ Standard Data Object（OpenBB式Query/Data标准化思想）
→ content identity / known_at / available_at / rights
→ completed-bar aggregation（LEAN式market-hours-aware consolidation）
→ method-specific dynamic window
→ recursive stability / lookahead negatives（Freqtrade模式）
→ 8-family evidence + correlation dedup
→ asset route + hard veto
→ canonical / FULL Report / Email
→ 仅合法Ledger/Outcome/PIT（Qlib思想延后复用）
```

当前不安装OpenBB、LEAN、Freqtrade、Qlib或CZSC作为并行运行栈；只吸收标准化、coarse/fine、warm-up、recursive/lookahead、PIT和窄Chan adapter模式。原DSA继续拥有数据、调度、Registry、canonical、Report和History权威。

## 6. 当前路线选择

**主路线选择M2多资产known-at，而不是先把17个延期技术方法全部开发完。**

理由：七周期核心来源、方法回执和完整展示已经形成一条可运行链；父产品最大的当前缺口是市场、行业、ETF和基本面时点，而不是再增加同源相关指标。继续批量堆指标会提高局部深度，却无法关闭市场许可、资产适用性和完整产品门禁。

七周期现在也进入现有筛选消费者，但只位于便宜L1/L2之后的有界DSA候选短名单：`snapshot/daily prefilter → capped DSA deep analysis → strict MTF role overlay → final selection`。它不让全市场逐标的无界执行七周期，也不新增第二筛选器。严格MTF回执和canonical binding通过后，七个周期按四个职责组消费，而不是七票相加：

- `context`：月线长期环境与周线主趋势；
- `setup`：日线主要setup；
- `bridge_structure`：60m桥接与30m主结构；
- `confirmation_timing`：15m确认与5m时点。

筛选偏好限定在 -6至+4，低周期只能在context和daily setup均支持时给最多+0.25确认，任何canonical PASS/hard veto都阻断正向加分。MISSING/UNKNOWN不转换为中性票，来源回执过期、候选代码错配或签名篡改均为0分拒绝。真实2026-10-15完整七周期只读验收中，600519因月周与日线动量不利、但60m/30m和15m/5m局部支持，筛选偏好为-3.75；510300在周/日及60m/30m不利、仅5m局部支持时触及-6下限。两者均未被低周期反转；这证明层级消费有效，不代表该数值是永久或全市场结论。

技术指标继续作为有界子Lane：

- 月线RSI：只有合法更长历史和交易日历可证明时推进；
- 15m确认、5m执行时点：必须绑定已确认结构及执行veto；
- 分钟量/额单位：来源未证明前不得消费量价方法；
- ADX/DMI、Bollinger、KDJ、OBV/ADL、MFI、VWAP/AVWAP、Chan/Wave：只有产品消费者或独立增量触发时重入。

## 7. 横向对齐

- **B1**：来源和持久化权利为不可抵消veto。
- **B5**：指标只由确定性Owner生成；LLM/Email不生成数值、价位、概率或意图。
- **市场/行业/ETF**：资产专属语义和known-at身份独立；ETF不套股票估值/Leader逻辑。
- **B8**：同一canonical生成FULL Report/Email；图片、短摘要和聊天回复不能替代正式报告。
- **History/PIT**：未来结果只进入Outcome/label，不倒灌历史证据。
- **Promotion**：绑定exact代码、数据、特征、策略、模型和校准器，并需显式批准。

## 8. 成熟项目复用边界

复用顺序保持：DSA现有能力→DSA upstream→接受的成熟Sibling→窄Donor→最薄Adapter。

- Freqtrade类递归启动/未来信息检测：只借鉴多起点敏感性和lookahead负例，不复用交易动作或第二策略框架。
- LEAN类warm-up和多分辨率聚合：只借鉴按实际订阅分辨率规划历史、避免循环重复History，不并存第二数据调度器。
- AlphaSift类级联筛选：只复用本仓已有的便宜L1/L2→有界L3深析顺序；七周期不在全市场入口重复计算，也不允许LLM文字参与排名。
- Qlib：仅在合法PIT数据集、rolling实验和独立模型阶段使用。
- CZSC：仅可作为窄ChanFeatureAdapter；普通pivot不得冒充Chan笔段/背驰。
- AlphaEvo/LightGBM：仅在有独立样本外增量的冻结Shadow阶段。

## 9. 防退化和防返工

CI必须同时保护：

- 43方法、26复用/17延期、13策略绑定、56技术格、76数据对象；
- V2.5和接受机器图哈希；
- Email必需/TG可选、无自动交易、无LLM数值生产；
- 类型化来源身份、错时点、未知单位、未来回填、资产错配和渠道替代负例；
- 七周期同一方法Owner数值进入FULL Report/Email/HTML，无第二计算；
- 七周期筛选必须严格MTF回执+canonical binding、四职责组、-6/+4边界、低周期不可独立升级；
- 旧golden不得为候选输出降低，MISSING/UNKNOWN不得补造。

失败按PRODUCT、DEV_TOOLING、AUTHORITY_CURRENTNESS、ENVIRONMENT、ADAPTER_INTERFACE、PROCESS_CONSUMER、DATA_MODEL分类；同Gate连续两次同类失败即Parent-Route Re-entry，不机械追加补丁。

## 10. GitHub发布节奏

1. 冻结当前父提交与回滚包；
2. 建功能分支，不直接推main；
3. 本地运行AI governance、syntax、flake8、deterministic和offline suite；
4. 提交一组可解释的baseline commit；
5. 推送分支并建立PR；
6. 等待ai-governance、backend-gate、docker-build等阻断检查；
7. 记录CI结果、未证明项和Role Review；
8. 显式merge后，该commit才成为新的Production GitHub基线。

历史PR #30已绿色合并为main b32570b1。当前feat/stock-dsa-m2-source-gates-v1中的指数源、筛选合同和运行回执属于后继候选；截至本控制页核验，不能引用旧PR30的绿灯替代新分支PR/Linux CI。功能分支push不等于CI执行，更不等于main已包含该能力。PR30曾经历通知/历史测试修复，其失败与修复记录保留于dated章节，不再充当当前待办。

M2候选实现可以在同一已推送功能分支继续，并持续以小提交同步GitHub；但未merge的分支能力只能称候选基线，不能冒充main/Production。任何本地WIP、ZIP、Project Source或聊天结论都不能替代GitHub分支、CI与最终merge形成的工程基线。
