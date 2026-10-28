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
