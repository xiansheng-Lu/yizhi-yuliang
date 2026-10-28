# M1-19 领取记录

2026-10-28，BE负责，IN_PROGRESS。基线main d0caf14；新本地分支codex/backend-content-plan-readmodels。用户已授权连续多Agent开发，当前仅领取和实现中，不声明通过。

范围：匹配现有V8/V9前端只读契约的规范事实及商品读取切片。输入：M1-18目录/scope/已确认商品切片已验证；其余完整生产投影未完成不冒充。

三条DoD：

1. 实际客户端集合名/单位/状态匹配，未授权不开放写入。
2. 完整规范事实范围聚合，不能从截断/单日快照造历史。
3. 空/缺失/未接入区分，跨租户店铺账户反例验证。

实现、自测、必要回归与实际证据由负责子任务补齐；公共接线、迁移号、台账、Git及串行DB窗口由主Agent统一。专项已获串行DB窗口并完成释放；真实平台/付费未调用，正式人工复核保持PENDING。06/12既有文档不夹带。

实际实现切片：POST `/v1/delivery/integrations/queries`，精确支持 adq/reports、wxshop/compass、wxshop/products、wxshop/stock。QueryService(engine, VerifiedIdentity).query(QueryRequest)，无DDL。逐请求重验当前主体/店铺/账户/目录版本/声明/scope及六阶段，读取固定已实现路径；非支持资源显式拒绝。

reports/compass为完整所选范围的账户日投影，nativeId为真实账户原生ID，非平台报表单ID；只读version=null。仅当前有效独立规范合同的事实提供数值，缺项null，真实零保留0。只认当前来源最新成功run，不将新空页/失败回退旧值，跨归因/币种/退款不混算。syncedAt仅真实持久fetched_at。

商品库存是本地已知ID和详情观察子集，不宣称平台完整目录或历史库存回放；每个缺详情已知ID明确warnings，不把缺失当0。被移除商品过滤，筛选后同步时间只来自匹配观察。无抓取依据拒绝假空成功。产品名称未采集使用ID标签，缺SKU/stock保持未知，无执行前置承诺。

真实前端时间语义证据：platform-catalog.ts的row.updatedAt仅非空文本，platform-workspace.tsx约838直接显示；按主Agent确认V9未知源时间显示“源更新时间未提供”并warnings说明，绝不以fact_date/now/fetchedAt冒充源更新时间。Page.syncedAt仍是Date可解析抓取时间。此文本策略不适用V8严格ISO字段。

10项单元PASS（0.54秒），覆盖安全整数/无效数、未知与0、范围/闰日、scopeKey顺序。实际PG/HTTP resource-queries共6项PASS（8.63秒），包含商品与Compass未知合同、缺详情ID、跨店观察不复用；integration-context回归15项PASS（10.37秒）。实际冻结PlatformService.query消费捕获HTTP结果PASS（含真实CSRF调用形状、未知/零和时间断言），2次注入fetch、真实平台网络0。本切片技术验证通过，未据此宣布整卡全部生产与人工验收完成。

仍未完成：V8 workspace完整13集合、CAMPAIGN不可证明bid字段、canonical AdPlan映射、完整生产数据与人工验收；未虚构overview HTTP端点。M4-04依赖仅部分就绪，不标完整通过。

执行命令（WSL后端目录；UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv）：

- `uv run --frozen pytest tests/unit/test_resource_queries.py -q`
- `YULIANG_RESOURCE_QUERY_CAPTURE=/mnt/d/codex/yizhi-yuliang/backend/.runtime/resource-query-capture.json uv run --frozen python scripts/test_infrastructure.py --group resource-queries --details`
- `uv run --frozen python scripts/test_infrastructure.py --group integration-context --details`
- `uv run --frozen ruff check src/yuliang/modules/reporting/query* src/yuliang/api/integration_query_routes.py tests/integration/test_resource_queries.py tests/unit/test_resource_queries.py`
- `uv run --frozen mypy src/yuliang/modules/reporting/query* src/yuliang/api/integration_query_routes.py`
- 仓库根目录：`node node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json backend/scripts/check_resource_queries_frontend.mts backend/.runtime/resource-query-capture.json`

本地测试数据库每次空库执行真实head迁移到0063；无新增DDL。上游使用合成payload、独立合同、实际PG/RLS/session cookie/CSRF与真实app路由，不等于平台E01/E02、人工签字或浏览器完整验收。
