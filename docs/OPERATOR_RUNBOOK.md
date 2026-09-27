# 个人版操作手册：从配置到读懂邮件

本手册服务于 GitHub Actions 上的个人私有部署，不要求另装 Web 服务、Docker 或交易框架。需要其他部署方式时，再从[文档中心](INDEX.md)进入。

核对日期：2026-09-27。**这是一份文档候选，不会替你启用任务、修改密钥或上线未实现功能。** “待实现”章节不能当成已生效的配置说明。

<a id="task-start"></a>
## 1. 第一次部署：先看对地方

准备私有 DSA 仓库、至少一个可用模型服务、一组邮件发送配置；R2 不是发送第一封分析邮件的必需项。已有私有仓库就继续使用，不重新 Fork、不另建第二套系统。

在私有仓库检查三处：

1. **Code**：确认默认分支是准备承载正式版本的 `main`。开发分支用于测试，不等于已经合入 `main`。
2. **Settings → Actions → General**：这是仓库 Actions 总开关。总开关打开，只代表允许工作流运行。
3. **Actions → 每日股票分析**：这是单个工作流开关。黄色提示 `This workflow was disabled manually` 表示它仍被暂停；并不是仓库损坏。

不要点击 `New workflow` 来启动已有任务，也不要把当前运行页的 `Cancel workflow` 当成暂停以后的定时任务。

成功检查应能回答：在哪个私有仓库、测试哪个分支、单个 daily 工作流是否启用。页面与手册不同时先保留截图，不猜按钮。

<a id="task-config"></a>
## 2. 配置邮件和模型

统一入口：**Settings → Secrets and variables → Actions**。有两个标签：

- **Secrets**：API Key、SMTP 授权码等私密值。新建用 `New repository secret`。
- **Variables**：非敏感参数。新建用 `New repository variable`，修改使用已有行的编辑按钮。

`Environment STOCK_LIST` 是工作流使用的环境名称；Repository Variable `STOCK_LIST` 才是这里使用的自选代码列表。它们不是同一个概念。先使用仓库级配置，不为相同参数再在 Environment 建一份；存在同名项时，必须核实际生效来源。

### 最小配置表

| 放置位置 | 名称 | 内容或形状 | 如何核对 |
|---|---|---|---|
| Secret | `GEMINI_API_KEYS` | 一个 Key，或 `KEY_A,KEY_B` | 只看名称存在；真实可用性看已批准运行 |
| Secret | `TAVILY_API_KEYS` | 搜索服务 Key | 不把完整 Key 放到日志或截图 |
| Secret | `EMAIL_PASSWORD` | 邮箱要求的 SMTP 授权码/App Password | 变量名虽叫 PASSWORD，不代表要填网页登录密码 |
| Variable | `EMAIL_SENDER` | `sender@example.com` | 发件邮箱与 SMTP 授权码属于同一账号 |
| Variable | `EMAIL_RECEIVERS` | `first@example.com,second@example.com` | 英文逗号分隔；空值按当前邮件实现使用发件邮箱 |
| Variable | `NOTIFICATION_REPORT_CHANNELS` | `email` | 分析报告只走邮件 |
| Variable | `NOTIFICATION_ALERT_CHANNELS` | `email` | 告警路由；不等于已启用告警任务 |
| Variable | `NOTIFICATION_SYSTEM_ERROR_CHANNELS` | `email` | 系统错误路由；不额外启用服务 |
| Variable | `RESEARCH_STATE_DURABILITY_ENABLED` | `false` | 本阶段保持关闭 |

这不是要求重复填写已经存在的项目。每个名称只维护一处，真实邮箱也不必写进 README。

### 模型以后怎样修改

当前已经证明原生多 Key 配置入口存在。更换 Key：编辑 `GEMINI_API_KEYS`，下一次新运行读取新配置；不会改变已经启动的运行。两个 Key 的存在不证明两个都被使用过，也不保证额度相加。

需要明确选择 Gemini 模型时，当前工作流已映射 `GEMINI_MODEL` 和 `GEMINI_MODEL_FALLBACK`。先在服务商确认准确模型 ID 和账号权限，再按单独的模型变更检查设置；不要同时更换数据源和通知配置。最新可用型号以服务商为准，不从旧 README 复制型号。

DeepSeek 不是第一次邮件验收的前置。增加其 Key 与配置跨服务商 fallback 是两件事；后者可能增加真实费用，须核对当前 DSA 的原生渠道/回退规则。详见 [模型配置](LLM_CONFIG_GUIDE.md)与[渠道和回退](llm-providers.md)。

已有可用路线出错时，先看认证、额度、限流、超时是哪一类，不通过连续重跑或同时添加多个模型掩盖问题。

<a id="task-watchlist"></a>
## 3. 添加、删除自选股票或 ETF

**自动发现**回答“系统今天筛出了谁”；**我的自选**回答“我主动指定的代码现在怎样”。两者使用同一深析流程，但自选不占自动候选额度。

在私有仓库进入 **Settings → Secrets and variables → Actions → Variables**，找到或新建 `STOCK_LIST`。值是代码列表，用英文逗号分隔。例如：

```text
600519,588000
```

这只是“一个股票代码加一个 ETF 代码”的格式示例，不是推荐。添加代码就追加，取消关注就删掉该代码；保存后对下一次新运行生效。不要为每个代码创建一个 Variable。

本工作流的 `STOCK_LIST_CONFIG` 来自 `vars.STOCK_LIST || secrets.STOCK_LIST`，再供普通分析路径使用。因此不要在 Variables 留空，却忘了 Secrets 中还有旧列表。

没有配置自选时，定时 AUTO 不应把示例默认代码冒充你的自选。普通手动 `stocks-only` 在列表缺失时有默认代码行为，所以手动运行前务必检查实际输入。

**不要用 `p0_stock_codes` 填常规股票/ETF列表。** 它是特殊的一次性有界股票验收入口，不是通用自选框。

已有本地环境的代码研究入口是 `python main.py --stocks <逗号分隔代码>`；没有本地环境时不必为改自选安装环境。GitHub 上普通指定代码分析使用 `STOCK_LIST` 与 `stocks-only`。

<a id="task-preferences"></a>
## 4. 排除医药、排除北交所：要能随时改回来

### 已接受的个人需求

| 对象 | 当前希望 | 性质 |
|---|---|---|
| 自动筛选股票 | 暂时不推荐医药类 | 可撤销的个人排除，不是永久策略定律 |
| 自动筛选股票 | 暂时不考虑北交所 | 硬排除 |
| 自动筛选股票 | 主板、科创板优先 | 排序偏好，不自动等于禁止创业板 |
| 自动筛选 ETF | 暂无新增行业/主题/板块偏好限制 | 不继承股票医药排除；原有风险、流动性和数据要求仍保留 |
| 用户主动指定代码 | 继续能研究被自动筛选排除的资产 | 仍不能跳过真实风险与数据底线 |

### 本地开发候选的最小入口——尚未部署

当前本地开发候选已在现有配置层接入下面三个字段，而不是把“医药”写死在 Python 里：

```text
AUTO_SCREEN_STOCK_EXCLUDED_SECTORS = 医药
AUTO_SCREEN_STOCK_EXCLUDED_BOARDS = 北交所
AUTO_SCREEN_STOCK_PREFERRED_BOARDS = 主板,科创板
```

**这些字段目前只存在于未提交的本地开发候选，不要现在到 GitHub 添加并误以为私有 `main` 已经生效。** 当前候选已经贯通配置解析、工作流变量映射、股票 AUTO 筛选和本地测试；在配置样例、完整 CI、代码评审和部署验收闭合前，仍按“未上线”处理。ETF 不消费这三个股票偏好，SPECIFIED_CODES/watchlist 也不会因为 AUTO 偏好而失去研究入口。

正式部署后的日常操作目标：

- 想重新关注医药：从行业排除列表删除“医药”；排除列表为空就不做个人行业排除。
- 想额外排除银行：在同一列表追加“银行”，其他条件不变。
- 想加入北交所：从板块排除列表删除“北交所”。
- 想把创业板也列入重点：在板块优先列表追加“创业板”。
- 想只允许某些板块：这与“优先”不同，须明确改为允许范围，不能暗中改变语义。

修改偏好不需要重训模型。必须先看到有效配置和“原候选数 → 各原因排除数 → 剩余数”的检查结果；这个检查不调用模型、不发邮件。字段拼错或分类不足时不能静默忽略。

行业判定优先用已确认的行业分类，不以公司名称有没有“药”来断言属于或不属于医药。启用严格排除时，无法判断的候选不进入重点推荐，并保留原因；未启用该规则时不因这项偏好而额外拒绝全部未知行业。

科创板股票应明确标 `科创板`；ETF 与股票板块身份分开显示。只有跟踪指数/底层暴露证据已确认时，才可进一步写成 `科创指数ETF`；证据不足时保留 ETF 身份并标注待确认，不能按股票号段把 ETF 误写成科创板个股。

<a id="task-run"></a>
## 5. 手动验收一次：与长期自动运行分开

本节描述操作方法，**不授权再跑已经完成的测试**。2026-09-21 已有一次私有 daily 运行成功和实际收件截图，该次不能为了补截图重跑。

新的一次真实测试另行明确范围后：

1. 打开私有库 **Actions → 每日股票分析**。
2. 如有黄色 disabled 提示，点右侧 **Enable workflow**。
3. 点 **Run workflow**，选择批准测试的开发分支，不默认选择 `main`。
4. 自动发现测试填 `mode=auto-screen`，股票上限 `1`、ETF 上限 `1`；`force_run` 不勾；`auto_screen_bounded_live` 不勾；`auto_screen_bounded_model` 和 `p0_stock_codes` 留空；`research_state_smoke_phase` 保持默认。
5. 最终绿色 **Run workflow** 只点一次。若提交结果不明，先查看是否已经出现新 run，不连续点。
6. 在尚未长期上线的阶段，新 run 出现后回到该 workflow 页面，通过 `... → Disable workflow` 暂停以后新运行。不要点击运行页的 `Cancel workflow`。
7. 记录新 run 的数字 ID、实际分支/提交与输入；查看最终状态和收件结果，不自己重新运行失败任务。

`auto_screen_bounded_live=true` 是旧股票专用验收路径，要求股票=1、ETF=0和指定模型。它不是所有小规模测试的通用开关。

绿色 Success 只证明本次工作流按其检查完成；实际收件证明传输；数据时间正确、没有编造和重复、解释有用，才证明这封报告合格。盘中测试不等于收盘后的晚报验收。

<a id="task-schedule"></a>
## 6. 怎样真正每天自动发邮件

开发分支测试 → 代码/报告验收 → 合入私有默认分支 `main` → 确认公开库不再重复发送 → 启用私有 daily → 观察一次自然定时运行。

GitHub 的 `schedule` 只运行默认分支，**不会记住你上次手动 Run 选择的开发分支**。所以“开发分支收到邮件”后不能直接认定以后定时会用同一版代码。

本开发版本 cron 是 `0 11 * * 1-5`，即北京时间工作日19:00；程序还需按交易日规则决定是否分析。GitHub 可能排队延迟，不保证19:00准点到邮箱。早报计划必须等对应实现和验收后再标为可用。

在 `main` 尚未晋升、报告仍未通过时，daily 保持 disabled，不必等到晚上再重复同一个邮件链路测试。长期上线后不需要每天人工 Enable/Disable。

<a id="task-r2"></a>
## 7. Cloudflare R2：先配置，后按范围启用

R2 是保存研究状态包的存储服务，不是 SMTP，也不是模型服务。**配置了凭证不等于已经开始读写。**

### 在 Cloudflare 找到四项信息

进入 Cloudflare 控制台的 R2 页面，选择专用私有 bucket。记录 bucket 名和该账户的 S3 API Endpoint。管理 R2 API Tokens 时使用限定该 bucket 的权限；只读恢复和允许发布所需权限不同，不选择不必要的全账户 Admin 权限。创建页面显示的 Access Key ID、Secret Access Key 要安全保存，不能贴进 Chat。不同控制台版本入口文字可能不同，遇到不一致先按官方说明或截图确认。

在 GitHub 私有库 **Settings → Secrets and variables → Actions** 填：

| 类型 | 名称 | 来源 |
|---|---|---|
| Variable | `R2_ENDPOINT_URL` | Cloudflare 的 S3 Endpoint；不是 Token，也不是公开访问域名 |
| Variable | `R2_BUCKET_NAME` | 专用 bucket 名 |
| Secret | `R2_ACCESS_KEY_ID` | R2 的 S3 Access Key ID |
| Secret | `R2_SECRET_ACCESS_KEY` | 对应 S3 Secret Access Key |

保留 `RESEARCH_STATE_DURABILITY_ENABLED=false`。这个值只控制正常分析的恢复/发布路径；不要选择特殊 `research-state-smoke` 模式来验证“没有副作用”。后者有独立的空状态测试用途和授权边界。

### 正式启用前核什么

确认实际凭证的 bucket、权限和到期日；名称相同不证明凭证仍有效。历史空状态发布/恢复已完成，不反复重放。真实研究状态须另验导出列、隐私/数据权利、包校验、恢复和失败处理，之后才考虑将开关改为 true。

恢复失败不能靠关闭完整性检查继续。关回 false 可停止后续正常分析路径访问 R2，但不删除已发布包，也不能撤回已经发生的写入。

不要把私人报告、持仓、API Key、原始行情库或完整 SQLite 数据库上传到公开仓库或公开 bucket。

<a id="task-data"></a>
## 8. 数据、数据库、分析、推送分别负责什么

**输入**：自动发现使用筛选范围；指定代码直接进入研究。个人排除作用于自动推荐，不篡改研究事实。

**行情与信息**：DSA 现有适配器获取行情、财务和新闻，保留来源、日期、币种和已完成K线状态。没有可靠数据的部分保持未知。

**分析**：确定性规则整合趋势、相对强弱、量价、结构、波动、基本面、估值和风险。月/周/日互相确认或冲突；同一摆动的多个指标不被当成多份独立证据。

**解释与投递**：由同一份证据/结论生成结论先行简报、同封邮件的材料详细区和完整审计报告；模型可解释原因，不能改买卖方向、添加数字或制造确定性。邮件不以“简短”为由删除 READY 的材料证据；只有更细的审计/实现诊断留在完整报告。

**运行数据库**：DSA SQLite 保存运行中的业务结果和研究身份；GitHub 托管 runner 的本地文件不天然跨运行持久化。一次 SQLite 保存成功不等于下次还能取回。

**可选 R2**：经过单独验收后，仅选择性保存允许导出的研究状态包，并在后续运行校验恢复。它不是第二个可写研究数据库，也不自动保存所有自选变化历史。

**以后模型研究**：不可变预测记录 → 到期结果 → 当时可知的数据集 → 明确标签 → 样本外测试与概率校准 → 前瞻观察 → 批准晋升。当前不能把邮件评分说成历史胜率或校准概率。

### 8.1 本地 Evidence Flywheel 研究入口（开发候选，尚未上线）

该入口只补齐现有 DSA 的研究状态编排，不新建数据库、scheduler 或模型。三步仍严格分时发生：先记录 canonical 决策到 Prediction Ledger；至少三个后续交易时段成熟后，使用冻结的成本/执行身份生成 Outcome；随后冻结一个 `TRAINING_ADMISSION=BLOCKED` 的 PIT manifest receipt。它不会训练 Logistic/LightGBM，也不会把评分改成概率。

```text
python -m src.services.evidence_flywheel_runtime record --stocks 600519 --code-sha <exact-40-hex-SHA>
python -m src.services.evidence_flywheel_runtime evaluate-outcome --prediction-hash <64-hex> --cost-identity-file <cost.json> --execution-identity-file <execution.json>
python -m src.services.evidence_flywheel_runtime build-manifest --cost-identity-file <cost.json>
```

`record` 复用既有 P0 有界分析边界，固定 1–2 只沪深普通 A 股、单 worker、保存完整上下文并强制关闭通知。该阶段显式绑定 `p0_model_request_budget=0`，在任何模型 dispatch 前硬阻断，并要求收据同时报告 `model_request_budget=0` 与 `model_request_count=0`；普通 P0 入口仍保留既有最多两次直接请求语义。它继续消费当前 DSA 数据与确定性证据路径，因此真实运行仍须另行批准，不能把本地 prewrite 或单测当成已完成真实采样。三个命令都使用当前 `DATABASE_PATH` 指向的 DSA SQLite；测试或实验应使用隔离数据库，不能直接拿生产库试跑。

成本与执行身份文件只接受有界 UTF-8 JSON object，内容必须满足既有 `cost-identity-v2` / `execution-identity-v1` 合同。Outcome 未成熟、交易日历无法证明、缺 bar 或执行证据未知时保持 `UNMATURED / EVALUATION_BLOCKED / UNLABELABLE`，不补标签。`build-manifest` 在本入口中不能打开训练准入。

该入口不会读取或修改 R2 开关/凭证，不会 restore/publish research-state，不会发 Email/Telegram，不会 commit/push/merge，也不代表 private `main` 已晋升。R2、真实有界运行、自然 CI 与 main Promotion分别走各自授权和证据关口。

### 8.2 GitHub Actions 单股真实记录候选（仍需提交、CI与单独dispatch授权）

既有 `00-daily-analysis.yml` 的开发候选增加手动 `mode=evidence-flywheel-record`，复用 `p0_stock_codes`，但该模式只接受**正好一只**已登记的沪深普通 A 股。它使用 `./data/evidence_flywheel_record.db` 隔离 SQLite，固定零模型请求、单 worker、通知抑制，并关闭实时行情、盘中技术指标、筹码、基本面、市场复盘、公共 SearXNG 与 research-state durability；不会读取模型、搜索、SMTP、Tushare、TickFlow、Longbridge或R2 Secret。运行入口在构造 Pipeline 和访问 provider 前只读检查 SQLite 文件：任何非空业务表或遗留 `-wal/-shm` 都立即拒绝；native 数据库初始化后、`pipeline.run` 前再次检查，运行后仍保留 closed-world 表增量验证。目标股票与既有 `510300` 相对强弱代理仍可通过无 Key 的 DSA 日线 fallback 获取真实完成 bar，因此实际 provider 请求仍属于后续单独授权的真实数据 effect。

成功运行只上传 `data/evidence_flywheel_record_receipt.json`，保留一天，并把同一脱敏 JSON 写入 GitHub Step Summary。收据对 Ledger id、布尔状态、三个 64-hex hash、PIT 理由数组和 `LOCAL_DB_ONLY` 状态逐项校验，只投影固定白名单；不上传 SQLite、原始行情、raw evidence、`reports/`、`logs/` 或额外字段。工作流在运行前注册失败安全的 `EXIT` 清理：成功和失败都删除数据库/WAL/SHM及报告/日志，失败或收据未完整验收时同时删除 partial receipt，只有完整成功才保留收据供 Artifact 使用。该结果只是一次工程 Ledger 记录证据，不证明跨运行持久化、Outcome成熟、策略收益、PIT训练集、概率校准或 Production main。

普通 `stocks-only` P0 仍保持1–2只股票和既有最多两次直接模型请求语义；19:00 schedule、V2.5 `baseline-transport` 与 `research-state-smoke` 不消费本模式。工作流当前若为 disabled，不因代码候选存在而自动启用；真实 Run workflow 仍须绑定 exact branch/SHA、输入、数据effect与证据surface后另行批准。

<a id="task-markets"></a>
## 9. 以后添加港股、美股

现在主线仍是 A 股。底层已有港股、美股指定代码识别，例如 `hk00700`、`AAPL`；完整支持还取决于数据源、时区、交易日、币种、复权和已完成K线，不是名称能解析就算验收。

未来先用一个指定股票/ETF样本检查：身份 → 数据与币种 → 同一分析 → 同一简报 → 收件。准确支持范围见 [市场支持](market-support.md)，但上游功能列表不等于本私有版本全部已验证。

自动筛选是另一条能力：美股已有 snapshot/universe 原语，但现有内嵌策略限定 cn；港股当前 pipeline 未支持；海外 ETF 自动筛选也未准入。以后按需增加市场候选来源、策略范围、过滤和时钟测试，再通过原有深析/报告，不另建三套系统。

**现在没有一个已经接通的“美股/港股自动推荐开关”。** 本轮只保留扩展契约；将来接通后，本节必须给出实际入口、示例、费用和撤销方法，不能只让用户填一个尚未被代码读取的变量。

<a id="task-report"></a>
## 10. 邮件太长、模板怎么改

> **2026-09-26 当前优先：V2.5 原件运输先行。** 唯一已接受原件是 `templates/v2_5/STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html`，固定为 125919 bytes / SHA256 `039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823`。当前只允许 `read_bytes → identity guard → existing EmailSender`；HTML 字句、日期、示例值、DOM、CSS、空白均不得变化。原件含模拟事实，运输验收主题必须明确“模拟事实/非当日研究”。动态 Market/AUTO/WATCHLIST renderer、slot projection、Markdown fallback 都不是当前原件运输入口；本节下方 R004/动态模板说明仅作历史/未来设计背景，不能覆盖本条当前运输限制。GitHub 真实收件验收只复用既有 `00-daily-analysis.yml` 的手动 `baseline-transport` 模式；该模式不执行正常分析/研究状态读写，实际 dispatch 与 SMTP 仍需独立授权。


现有 `REPORT_SHOW_LLM_MODEL=false` 可以隐藏普通报告里的模型署名，工作流已有映射。但隐藏一行署名解决不了重复指标和机械语言，不必现在为它单独跑一次邮件。

当前用户侧基线是已接受的 R004 information-dense Gold Master。邮件应先回答：**现在怎么看，为什么，什么变化会让我改判断。** 第一屏可以结论先行，但同一封邮件后续必须让全部材料证据可达；“compact/mobile”只允许调整层级和去重，不允许删除已 READY 的重要周期、量价/形态、估值、触发、失效或风险证据，也没有固定整封邮件长度上限。稳定/无事件状态可只说明一次；缺数据只说明一次，不能由模型补齐。

晨报的人话增量必须直接建立在这份 R004 上，而不是另做更短模板。第一屏优先回答“今天的大环境、海外风险有没有升高、A股内部是否健康、强势和可核资金线索在哪里、哪些方向先别追、什么变化会推翻判断”；市场宽度第一次出现时解释成“有多少股票一起上涨”，VIX解释成“美股未来约30天预期波动/风险压力”。价格领先、交易活跃和可核资金证据分开写，不能把板块涨幅或成交热度改名为主力净流入。原有八段详细区、全部材料冲突、条件、风险、数据时点和缺失说明继续保留。

三个 envelope 共享同一 canonical evidence/decision：开盘前 `MARKET_REGIME_BRIEF`、晚间 `ASSET_RESEARCH_BRIEF_AUTO`、条件触发的 `ASSET_RESEARCH_BRIEF_WATCHLIST`。AUTO 的重点 Top 3 保持完整深析；4–10 可以紧凑，但至少保留现有 canonical 结论、核心理由、主要风险和失效/下一触发。WATCHLIST 即使结论为 WAIT/AVOID/失效，也不能降低研究完整度。文本 Email 与内联图片 Email 应消费同一 envelope-aware subject，不能因为走不同发送形式退回通用标题。

改展示的入口：

| 想改什么 | 现有代码位置 |
|---|---|
| 人类解释、跨周期融合、关注与失效条件 | `src/services/factor_decision_summary.py` |
| 简报正文布局 | `templates/report_brief.j2` |
| 完整报告布局 | `templates/report_markdown.j2` |
| Python 渲染、AUTO/WATCHLIST 分组与 Email/Telegram 拼装 | `src/notification.py` |
| Email 文本/内联图片主题 | `src/notification_sender/email_sender.py` + `src/core/pipeline.py` |
| Market 保存/通知/复用投影 | `src/core/market_review.py` + `main.py` |
| 参数说明和 Actions 映射 | `.env.example`、`.github/workflows/00-daily-analysis.yml` |

修改报告时不能只检查“用了同一个 `AnalysisResult`”或某个字段/字符串是否存在；必须用同一组 canonical 输入比较保存报告、Jinja/Python compact、Email/Telegram 的实际输出，确认材料事实没有在最终消费者丢失。R004 exact Gold Master fixture 是内容/结构回归基线，不代表真实数据、策略效果、胜率或概率已经得到证明。

实现前保留当前收到的错误邮件作为反例。离线检查第一屏可读性、完整详细区、事实和数字一致、重复预算、missingness、材料事件、风险否决、AUTO 4–10、自选触发以及 subject/body 一致性。任何晨报模板增量在 commit/push 前还必须生成一份由 exact R004 完整 HTML 增量得到的模拟版，让用户实际打开核查；没有用户接受，不得把代码测试或 CI 绿灯扩大成邮件体验通过。之后才以 private natural CI 做完整依赖环境验证，再经单独授权的 bounded consumer acceptance 验证真实收件效果。

<a id="task-recovery"></a>
## 11. 常见问题和恢复

| 现象 | 先检查什么 | 不要做什么 |
|---|---|---|
| Actions 绿了但没邮件 | 实际是否选出候选、通知步骤、SMTP结果、收件箱/垃圾箱 | 不把绿色等同发送成功 |
| 多个 Gemini Key 已填，日志仍说未配置 | 旧检查行只看单数 Key；核实际模型调用 | 不直接判定所有 Key 失效 |
| 收到了但太长、重复、像说明书 | 最终简报生成和两种渲染路径 | 不靠换模型或删掉全部风险信息 |
| 到19:00没邮件 | daily是否启用、默认分支、交易日、排队、运行结果 | 不连续手动触发补跑 |
| R2名称都在仍报错 | Endpoint、bucket、凭证范围/到期、实际模式和开关 | 不扩大到全账户权限来排错 |
| 改了配置却没生效 | 是否映射到该workflow、是否被同名配置覆盖、是否为新运行 | 不在多个位置重复堆同名项 |

只退回最后一项已知可用设置；涉及发布版本则退回已记录版本，不覆盖或删除原始失败证据。完整排障参考 [FAQ](FAQ.md)。

## 12. 手册维护与一手参考

改功能时同时改“在哪里设置、有效值、示例、如何验证、如何撤销”，避免 README 说已经能用而运行路径未接通。个人中文入口本轮更新，英文/繁中上游文档保留作参考，不作为本私有版本上线证据。

- [GitHub 工作流触发与默认分支](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [Cloudflare R2 凭证与权限](https://developers.cloudflare.com/r2/api/tokens/)
- [Jinja 模板复用](https://jinja.palletsprojects.com/en/stable/templates/)
- [Freqtrade 的候选列表与排除方法](https://www.freqtrade.io/en/stable/plugins/)：仅借用配置/过滤方法，不安装交易框架。

上面的外部说明核对于2026-09-21；真实 UI、服务套餐和模型可用性以后仍需重新核对。
