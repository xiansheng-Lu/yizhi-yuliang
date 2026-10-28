# M4-01b 分析计算、比较期与实际阶段

2026-10-28（Asia/Shanghai）；BE负责人，协作F08；WAITING_REVIEW（本地技术验证通过，人工未签字）。本批基线739b721，迁移0058_analysis_comparisons；真实平台E01/E02口径与G0/E08门禁继续保留。

## 范围与完成边界

直接依赖M4-02覆盖守卫，使用M1-10实际报表同步/规范事实与M1-12有效CompassContract投影。当前支持已具有规范来源的广告和小店经营维度；直播/视频缺必需来源仍明确422。只产所选维度，广告归因GMV不作店铺经营GMV；来源去重支付订单不冒充“未关闭有效订单”。NET_OF_REFUND成交金额动态标明退款处理后口径，缺毛额依据时标准客单价/退款率不算。缺归因订单CPA为空，无成本不制造利润。

本卡早期只有本期计算，曾明确comparison NOT_IMPLEMENTED；本批已补实际比较窗口读取、覆盖核验、快照冻结、确定性聚合和真实TS消费验证，不再把未实现当缺数据。

## 本期与比较期

所有读取固定原task.account_scope。新增可见账户不能扩大旧任务；当前撤权仍拒绝。阶段40同一REPEATABLE READ视图核对本期、等长前期与去年期规范事实/终页/合同，保存来源ID、修订、run/page及原生分子/分母。相同账户集合、单位/币种、归因和退款口径才比较；实际缺日、缺指标或不可比时previous/yearAgo为null、序列为空并附明确原因。

前期对齐现有TS previousRange，按等长紧邻窗口，完整月的前期可跨自然月；同比按去年起日夹至有效日，日/周保持等长，完整自然月按去年同月，不自动日均归一化。例：2024-02-29同比2023-02-28；2024-02自然月同比2023-02共28天，前期为2024-01-03～31共29天。政策写入结果coverage/章节限制。

指标由Decimal总分子/总分母计算；ratio元数据与可相加基数供现有前端groupAnalysisPoints/pointValue使用，不把日比例相加/平均。当前/前期/同比各自冻结并重算，规则洞察只引用本期事实ID，比较期事实不得混入本期引用。

## 阶段与恢复

真实阶段10领取、40冻结、75聚合、90校验、100不可变结果与成功同事务。120秒fence租约；旧fence/NULL/未claim拒绝，任意任务/结果UPDATE与INSERT未授权。失联重投在旧租约未过期时保持原任务重排，过期恢复不覆盖本期或比较期旧快照；计算异常固定failed，数据库错误重排原task。

历史0055在途任务可能只有本期快照。0058仅保留旧本期，在SQL内部标NOT_CAPTURED_LEGACY并给“旧在途快照未记录当时比较期，需新任务”原因；不回抓今天的比较值冒充当时输入。新任务stage40仍必须经同事务三期冻结，不能降级绕过。

## 实际验证

环境：WSL Ubuntu，UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv。所有HTTP上游、身份与独立口径证明均为合成；PG/Redis/RabbitMQ/HTTP服务为真实本地设施，不等于平台P验收。平台/模型付费调用0。

| 实际命令 | 实际结果 |
|---|---|
| uv run --frozen python scripts/test_infrastructure.py --group analysis-comparisons --details | 12通过，38.13秒；包含三类语义冲突、RR并发改源、旧快照恢复、SQL/Python闰日一致 |
| PYTEST_ADDOPTS='-k actual_compass' 同上述命令 | 新增经营来源专项1通过、12排除，4.95秒；共13个不同比较用例已执行 |
| PYTEST_ADDOPTS='-k actual_previous' 同上述命令 | 加强本期/比较期引用ID不交叉断言，1通过、12排除，7.84秒 |
| uv run --frozen python scripts/test_infrastructure.py --group analysis --details | 最新26回归通过，42.78秒 |
| uv run --frozen python scripts/test_infrastructure.py --group report-drafts --details | 4通过，9.20秒 |
| uv run --frozen pytest tests/unit/test_analysis_comparisons.py -q | 6窗口/闰年单元通过，0.57秒 |
| Ruff本卡源码/测试；mypy分析/覆盖/worker | PASS；16源码无错误 |

广告完整三期ROI为3/2/1.5，前期消耗700、去年1400；经营规范来源GMV1000/600/500，客单500/300/250，完整数据可返回complete。缺一天/指标、跨期单位/归因/退款冲突均拒绝比较而不补0。另连接在预检后改事实为999，三期仍来自阶段40同一旧视图；冻结后合同撤销或新值变化、失联恢复均不改原输入。原scope撤销拒绝；新增账户不进入本期或比较快照。

真实前端消费命令（项目根目录）：

- node node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json backend/scripts/check_analysis_comparison_frontend.mts docs/backend/evidence/tasks/M4-01b/comparison-result.json：PASS；真实PG结果经现有previousRange/groupAnalysisPoints/pointValue，ROI3/2/1.5，前期范围精确一致。
- node node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json backend/scripts/check_analysis_result_frontend.mts docs/backend/evidence/tasks/M4-01b/frontend-result.json：PASS；最新非等权真实PG输出周ROI1.9而非日比率和11，CTR0.25、CPC28.571428571428573，缺CPA null。

证据JSON只含本地合成数据与真实运行标识，无凭据。回滚以前向停用分析派发、保留快照/结果为主，不破坏性downgrade。BE/F08具名复核PENDING，真实平台口径和业务放行不代签。下一任务由主Agent重新核对M1-19/M4-04就绪条件，本卡不自行越过门禁。
