# STOCK_DSA GitHub基线与总路线

> 本文是现有DSA机器图、执行合同和运行证据的维护者视图，不是新的Source、策略、数据库、调度器或报告权威。机器可读不变量见 `docs/stock-dsa-stage-baseline-v1.json`；真实状态仍由GitHub代码、CI、运行回执和合法数据证据决定。

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
| Milestone | 本轮为真实七周期原生方法逐项进入同一报告消费者 | 不等于43方法全部READY |
| Atomic | 复用既有Owner、消费者、测试和负例的最小改动 | 不建第二套Shell/DB/Registry/Report |

## 4. 阶段基线

### G0 GitHub基线

功能分支、PR、required CI和可回滚提交身份。只有PR阻断检查通过并显式合并，才形成后续开发基线。

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

### L1 合法Ledger/Outcome/PIT

只有来源权利、as-of/vintage、完整机会分母、未触发/未成交、成熟label和不可变字节均合法时才进入。

### M3 模型和校准

白盒Logistic先行；仅有样本外增量才LightGBM；walk-forward、独立校准、冻结Shadow和显式Promotion分离。

## 5. 当前路线选择

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

## 6. 横向对齐

- **B1**：来源和持久化权利为不可抵消veto。
- **B5**：指标只由确定性Owner生成；LLM/Email不生成数值、价位、概率或意图。
- **市场/行业/ETF**：资产专属语义和known-at身份独立；ETF不套股票估值/Leader逻辑。
- **B8**：同一canonical生成FULL Report/Email；图片、短摘要和聊天回复不能替代正式报告。
- **History/PIT**：未来结果只进入Outcome/label，不倒灌历史证据。
- **Promotion**：绑定exact代码、数据、特征、策略、模型和校准器，并需显式批准。

## 7. 成熟项目复用边界

复用顺序保持：DSA现有能力→DSA upstream→接受的成熟Sibling→窄Donor→最薄Adapter。

- Freqtrade类递归启动/未来信息检测：只借鉴多起点敏感性和lookahead负例，不复用交易动作或第二策略框架。
- LEAN类warm-up和多分辨率聚合：只借鉴按实际订阅分辨率规划历史、避免循环重复History，不并存第二数据调度器。
- AlphaSift类级联筛选：只复用本仓已有的便宜L1/L2→有界L3深析顺序；七周期不在全市场入口重复计算，也不允许LLM文字参与排名。
- Qlib：仅在合法PIT数据集、rolling实验和独立模型阶段使用。
- CZSC：仅可作为窄ChanFeatureAdapter；普通pivot不得冒充Chan笔段/背驰。
- AlphaEvo/LightGBM：仅在有独立样本外增量的冻结Shadow阶段。

## 8. 防退化和防返工

CI必须同时保护：

- 43方法、26复用/17延期、13策略绑定、56技术格、76数据对象；
- V2.5和接受机器图哈希；
- Email必需/TG可选、无自动交易、无LLM数值生产；
- 类型化来源身份、错时点、未知单位、未来回填、资产错配和渠道替代负例；
- 七周期同一方法Owner数值进入FULL Report/Email/HTML，无第二计算；
- 七周期筛选必须严格MTF回执+canonical binding、四职责组、-6/+4边界、低周期不可独立升级；
- 旧golden不得为候选输出降低，MISSING/UNKNOWN不得补造。

失败按PRODUCT、DEV_TOOLING、AUTHORITY_CURRENTNESS、ENVIRONMENT、ADAPTER_INTERFACE、PROCESS_CONSUMER、DATA_MODEL分类；同Gate连续两次同类失败即Parent-Route Re-entry，不机械追加补丁。

## 9. GitHub发布节奏

1. 冻结当前父提交与回滚包；
2. 建功能分支，不直接推main；
3. 本地运行AI governance、syntax、flake8、deterministic和offline suite；
4. 提交一组可解释的baseline commit；
5. 推送分支并建立PR；
6. 等待ai-governance、backend-gate、docker-build等阻断检查；
7. 记录CI结果、未证明项和Role Review；
8. 显式merge后，该commit才成为新的Production GitHub基线。

当前公开GitHub门禁对象为PR #30。功能分支push本身不会触发本仓CI，因为`ci.yml`只对`main` push或面向`main`的Pull Request运行；因此Actions首页最初只显示main定时任务不代表分支未推送。PR首次运行中ai-governance、docker-build和桌面打包通过，backend-gate在Offline suite发现17条历史Pipeline通知测试仍期待微信/飞书/NTFY/Gotify/上下文替代正式Email。生产合同没有放宽；旧测试被改为Email REQUIRED、Telegram OPTIONAL及其他渠道不替代Report的准确正负例，本地相关模块42/42与Notification/Schedule 203/203通过，等待新分支提交触发远端复验。

M2候选实现可以在同一已推送功能分支继续，并持续以小提交同步GitHub；但未merge的分支能力只能称候选基线，不能冒充main/Production。任何本地WIP、ZIP、Project Source或聊天结论都不能替代GitHub分支、CI与最终merge形成的工程基线。
