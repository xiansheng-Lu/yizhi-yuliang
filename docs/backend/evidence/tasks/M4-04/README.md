# M4-04 独立规范概况聚合器

2026-10-28，BE；基线main70ab960，本地codex/backend-workflows-import-overview。IN_PROGRESS，正式人工验收PENDING。

范围：`reporting/overview_models.py`、`overview_calculator.py`、`overview_service.py`，对应测试和实际前端helper验证。没有新增HTTP端点、迁移或FE Loader接线，不修改M1-19已冻结query读取模块。输入为显式冻结的真实ADQ账户scope集合及OverviewFilter，返回可序列化OverviewReport。

使用REPEATABLE READ逐账户核对M1-19当前scope/权限/来源六阶段，再读取规范合同有效、真实成功页的完整事实。本期和等长紧邻前期保持原账户集合；每个账户×自然日×metric必须唯一完整，缺失null，真实0保留；广告支付归因GMV与广告消耗相除，ROI/CPM按合计基数重算，不累计日ratio或混入微信小店经营GMV。跨归因/退款/单位/币种不可比时不汇总GMV/ROI，前期不可比保持null并说明。

source_updated_at仅真实源字段，未提供则updatedAt=null；fetched_at只记在coverageNote抓取依据，不冒充源更新时间。不声明未配置的新鲜度SLA。计划、直播、投放状态、完整漏斗和流量来源缺规范维度，明确空/不可用，不把账户金额摊派下去。AI统计按真实可见command_jobs及PLATFORM/MATCHED回执，接受成功或MOCK不算完成。

规模夹具目标100=2账户×10日×5指标，5000=10账户×100日×5指标，全部真实AdqReportSync→同步页→规范合同→FactWriter入本地PG，长窗口按31日分批。合成上游不是平台真实业务验收。独立SQL核对全部命中数、spend/GMV合计，包含生产roi原始ratio行而不将其相加。

单元4项通过0.77秒，3源码Ruff/mypy通过。PG专项4项通过89.62秒，真实100/5000条均全部命中规范来源（100：消耗2000分/GMV6000分；5000：消耗100000分/GMV300000分），两者ROI3/CPM5000分，与独立SQL一致。历史前期/缺日/null与0/新空页不回退旧值/撤权均通过。实际前端Overview helpers消费最新PG输出PASS，明确Loader未接线。DB窗口已释放。

实际命令（WSL backend目录，UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv）：

- `uv run --frozen pytest tests/unit/test_overview_facts.py -q`
- `YULIANG_OVERVIEW_CAPTURE=/mnt/d/codex/yizhi-yuliang/backend/.runtime/overview-capture.json uv run --frozen python scripts/test_infrastructure.py --group overview-facts --details`
- `uv run --frozen ruff check src/yuliang/modules/reporting/overview* tests/integration/test_overview_facts.py tests/integration/overview_scale.py tests/unit/test_overview_facts.py`
- `uv run --frozen mypy src/yuliang/modules/reporting/overview*`
- 仓库根目录：`node node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json backend/scripts/check_overview_frontend.mts docs/backend/evidence/tasks/M4-04/overview-result.json`

首轮5000规模夹具遇新SDK来源核验时间晚于被复制旧阶段时间，守卫正确拒绝；仅将夹具阶段时间取max、有效期取min后复验全部通过，未放宽生产守卫。测试数据/合同/身份/回执全合成，仅本地实际PG/Redis与真实来源处理链，不是平台正式验收。
