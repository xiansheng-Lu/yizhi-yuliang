# M3-19a 生成账本与成本确认

IN_PROGRESS：实际PG/HTTP技术链路已验证；真实供应商/价格/单位默认未接，不代表付费能力开放。readiness.md为此前PLANNED快照。

## 本批实现

- generation_models/provider/repository/service/routes及0070_generation_jobs（接0069_ai_content_requests）；0071受控job→费用许可恢复桥由主Agent持有。
- generation_jobs持久七业务态、独立NOT_SENT/SENT/ACKNOWLEDGED/UNKNOWN提交态、稳定UUID requestKey、client/payload/request fingerprint、实际document历史版本/run/step与actor/membership FK。
- 同tenant+requestKey幂等；同provider+requestKey唯一；同step跨attempt在途QUEUED/RUNNING/WAITING_PROVIDER/WAITING_REVIEW部分唯一约束，redo不能绕过未知任务再付费。
- 独立generation_price_sources维护provider+kind→VERIFIED GENERATION/JOB/scale1价目/单位来源和有效期。当前只确认代码可处理IMAGE/GRAPHIC的一请求JOB语义；视频/媒体时长单位尚未支持，显式拒绝，不估0。
- quote/confirmation/create分离；服务端重读文档/商品/实际素材固定版本HEAD/HMAC。CostGate.acquire_in_transaction与job/GENERATION引用/SOP步骤RUNNING同事务，不存permit token或hash。
- dispatch先同事务使用0071 generation_fee_start，唯一generation_dispatches栅栏+SENT提交后，事务外最多一次submit。SENT/UNKNOWN重复dispatch不发；query始终原requestKey，无外部ID也不重发。
- 费用未知保留预占/actualCostMinor=null；KNOWN仅消费已结算费用事实，LLM InvocationLedger不写入供应商费用。服务器确认私有产物版本后最多WAITING_REVIEW，SUCCEEDED正式采纳属于M3-19c，不伪完成。
- 默认Unavailable端口在SENT消费前拒绝，实际HTTP不挂供应商dispatch入口。明确NOT_SENT可cancel释放预占和在途素材引用；SENT后不能假称取消/退款。
- 新HTTP：POST /v1/content/generations/quotes、/confirmations、空路径create，GET /{id}，POST /{id}/cancel-unsent。复用真实Cookie/CSRF/X-Shop-Id，拒query覆盖。

## 实际技术验证

7项unit通过0.52秒，Ruff/mypy7文件通过。当前修复后实际专项：**31 PASS/26.96秒，head0072**，命令同下；其中包括新锁序/RLS四例。修复前历史首轮 `python scripts/test_infrastructure.py --group generation-jobs --details`：27 PASS/23.30秒，真实PostgreSQL/MinIO与实际main Cookie/CSRF HTTP。0069/0070/0071整链实际升级并验证，冻结SQL和费用桥无语义修复。
合成端口验证预定包含报价确认/过期/篡改、可信单位缺失、同键/并发/step在途约束、SENT在供应商端口可见前提交、响应丢失/崩溃后仅查询、不重发、原子回滚、当前文档/权限变化与默认关闭。

真实供应商协议/计费来源、实际回调/查单、媒体产物导入、用户E05付费范围以及M3-19b/c/F10联调均未完成。本卡不调用真实平台/供应商、不改原token配置、不执行Git。


静态审查已补：实际quote.price_id逐次匹配provider/kind/独立单位来源；未确认或过期报价先明确409。FAILED等业务终态不等于账单终态，费用UNKNOWN仍仅query原键，不能复活业务；已知费用遇后续未重复账单的UNKNOWN片段保留原额且不重复fee_finish，合法产物状态仍可推进；矛盾新KNOWN回执不覆盖旧结算。三条实际PG验证已在修后31项中通过。


主Agent0071已额外约束未发仅QUEUED/CANCELLED、真实dispatch+fee才能SENT、业务终态不可由账单复活、KNOWN不可改账、当前未接采纳故SUCCEEDED关闭；WAITING_REVIEW需真实同店同商品CONFIRMED私有图片。专项增加四个直接SQL伪造负例及真实UNKNOWN GEN子步骤阻断祖先redo，不新增重复公共SOP守卫。现已在上述真实PG专项通过，不是仅静态检查。


## 技术结果与保留边界

当前31项在原27项覆盖上增加后述锁序/RLS四例；原27项覆盖供应商价源/实际报价版本/确认与过期、新旧载荷与并发、step在途唯一、SENT及许可IN_FLIGHT先于端口调用、响应丢失/结果落库前崩溃的原键恢复、未知费用保留、业务FAILED后仅查费用、已KNOWN但后续未重复账单仍可推进实际私有产物、矛盾新账单拒绝、引用/费用/生成账本原子回滚、当前文档/租户/actor/shop拒绝、默认Unavailable，以及未发SUCCEEDED/伪SENT/无真实产物/已知改账四个SQL负例。

真实MANUAL祖先execute+accept后启动子GEN UNKNOWN，再实际redo祖先被公共SOP闭包RUNNING守卫拒绝；没有为本卡重复实现公共SOP状态机。

7项unit通过0.52秒；Ruff及格式检查（含0070迁移Python）通过；mypy本卡7文件通过。本轮0c613c4 generation-jobs DB窗口已向root与reports明确释放，此后未再DB。源码/DDL/测试冻结，Git由主Agent统一处理。

当前七态有数据库真实约束，但SUCCEEDED采纳入口按M3-19c明确关闭；真实供应商/付费价目与单位未配置，F10浏览器未接；视频/媒体计费规格未支持。合成provider返回的待审素材是测试已有CONFIRMED私有图片，仅证明范围/固定文件/费用链路，不代表外部生成出图。正式M3-19b/c不据27 PASS宣称完成。


## 最终交叉审查修复与实际复验

发现并修复锁顺序与派发记录RLS两项：所有变更采用run→job→sorted(input/output assets)→budget；repo.job(lock=True)先只读runId，锁run再锁job，取消/FAILED费用调用前预持后续释放所需资产锁。0071私有费用绑定函数由主Agent同步遵守run→job。

0070 generation_dispatches SELECT须原actor真实job+原request_key/payload_hash；INSERT另需当前actor原quote/permit/key、QUEUED/NOT_SENT且GENERATION许可已IN_FLIGHT。跨actor不能预插受害者dispatch堵塞发送/取消，也不能读取其派发记录。

新增四项实际PG专项已通过：跨actor app SQL INSERT拒绝后受害者仍可send/cancel两例；Barrier同run独立READY create与cancel/FAILED费用结账并发两例，VERSION_CONFLICT允许显式重新报价，不吞40P01或重试外部发送。此前27 PASS保留为修复前历史结果。修复后本轮实际 `python scripts/test_infrastructure.py --group generation-jobs --details`：**31 PASS/26.96秒，head0072**。两Barrier并发用例无40P01或重复费用；跨actor预插派发均被42501拒绝，受害者发送/取消正常。0070/0071/0072无需运行时SQL修复，未放宽任何守卫。


本轮GEN修复31唯一DB窗口已明确向root/reports释放；此后不跑新DB，源码/DDL/测试冻结。新31结果替代旧27作为当前验证结果，真实provider/paid/platform调用仍0。

本批最终完整整合：1009集成通过、1可选重启跳过（953.14秒）；952单元/契约、240源码mypy/Ruff及0069至0072迁移Ruff通过，冻结源码哈希一致。开发库0072、48相关表仅5系统模板其余0，迁移角色NOLOGIN。详见../../ai-content-generation-verification.json；整卡未完成范围与BE/FE具名复核仍按上列保留。


## 2026-10-29 视频编排骨架补齐（供应商无关）

仍需IN_PROGRESS：本轮只补与供应商无关的编排骨架、适配器和任务账本，未接真实视频供应商，未声明任何真实视频价格，也未落视频产物或进入采纳。

### 新增文件

- `backend/src/yuliang/modules/content/video_generation_models.py`：异步任务状态机（PENDING→RUNNING→SUCCEEDED/FAILED，TIMEOUT/CANCELLED为本地终态）、按秒/按次计价模式、提交/轮询/结果DTO及边界校验。
- `backend/src/yuliang/modules/content/video_generation_provider.py`：`start_task`/`poll_task`/`fetch_result`三段式适配器协议与默认不可用端口（只拒绝，不伪造调用）。
- `backend/src/yuliang/modules/content/video_generation_config.py`：只读`YULIANG_VIDEOGEN_PRIMARY_ENDPOINT/MODEL/KEY/PRICE_PER_SECOND/PRICE_PER_CALL`；缺失、歧义、0价、非HTTPS端点均抛`CAPABILITY_UNAVAILABLE`“视频生成供应商未配置”，不静默降级。
- `backend/src/yuliang/modules/content/video_generation_service.py`：复用既有图像生成报价/确认/创建/费用栅栏，仅在发送钩子写入视频任务账本，事务外调用适配器；`dispatch/query/cancel/get_task`。
- `backend/alembic/versions/0081_video_generation_tasks.sql/.py`：价源语义允许VIDEO的`ONE_REQUEST_SECOND`、`generation_fee_start`仅放宽VIDEO按秒/按次匹配、`video_generation_tasks`账本与RLS/触发器。
- `backend/tests/integration/fake_video_provider.py`、`backend/tests/unit/test_video_generation.py`、`backend/tests/integration/test_video_generation.py`。

### 账本与状态边界

- 按秒计价：报价用声明的预期时长作`input_units`，缺时长显式拒绝；成功回执按供应商返回时长记`billed_seconds`并结账。按次计价：`input_units=1`，不写`billed_seconds`。两种都由数据库可信价目来源驱动，不按环境变量估价。
- 视频任务账本`UNIQUE(tenant,job)`且外部任务标识不可变；终态不可复活；PENDING/RUNNING→SUCCEEDED必须在同事务带结果元数据，按秒成功必须带结算秒数；伪造成功、终态复活均被触发器拒绝。
- SENT在独立事务提交后才调用适配器；启动回执丢失保留SUBMIT=UNKNOWN与费用UNKNOWN，不按请求键重发。轮询RUNNING仅更新账本与确认态；SUCCEEDED先`fetch_result`落任务账本元数据并结账（KNOWN或UNKNOWN如实保留），但generation_jobs保持WAITING_PROVIDER，不伪造WAITING_REVIEW/资产采纳；超时在本地记TIMEOUT并将业务置FAILED，不再轮询；取消只停止本地轮询，不宣称供应商停止或退款，绝不改已发费用。
- 环境变量未配置时在费用消费/SENT前拒绝；不读取`.env.llm.local`，本轮不发起任何真实网络或付费调用。

### 实际验证数字

- 单元：`uv run pytest tests/unit/test_video_generation.py` 12 PASS/0.11秒（与本卡原7项合计19 PASS）。
- 视频集成（真实PostgreSQL/MinIO，fake provider）：`uv run python scripts/test_infrastructure.py --group video-generation --details` **11 PASS/29.48秒，head0081**；覆盖提交、轮询、成功、失败、超时、发送前/发送后取消、重复dispatch幂等、启动回执丢失、按秒/按次结账、未知费用保留、缺时长拒绝、未配置拒绝、伪造成功与终态复活两条SQL负例。
- 图像账本回归：`--group generation-jobs` 31 PASS/42.52秒，原有31专项未回退。
- 静态：本轮11个源码/脚本/测试文件Ruff与mypy均通过；仓库级Ruff/mypy在基线上已有与本卡无关的既有失败（如`src/yuliang/api/overview.py`、`tests/unit/test_notify.py`），本轮未改动也未掩盖。

### 仍缺（不得据11 PASS宣称视频能力完成）

1. 真实视频供应商端点/模型/密钥：`YULIANG_VIDEOGEN_PRIMARY_*`仍为空占位，未做任何真实调用。
2. 真实视频供应商单价（元/秒或元/次）须经人工验证写入`generation_price_sources`，本轮未写入。
3. 视频产物落库/采纳：需下载结果到私有MinIO、视频时长与媒体校验、confirmed video asset及WAITING_REVIEW/SUCCEEDED采纳，属后续M3-19c范围。
4. 供应商失败回执的账单、重复轮询限速/退避、进程级调度器与F10前端接线。
