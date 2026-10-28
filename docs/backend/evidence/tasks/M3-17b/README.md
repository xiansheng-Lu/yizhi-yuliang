# M3-17b 人工候选、采纳与闭包重做技术切片

2026-10-28，BE/F10。整卡IN_PROGRESS；人工MANUAL/REVIEW、版本2可选语义、事务派生与闭包重做切片TECH_VERIFIED_WAITING_REVIEW。基线main591447df0a4d4b102d66143a3d5e5e4960a3be8f，本地codex/backend-workflow-execution-links-events。真实AI正文候选与供应商生成分别归M3-18/M3-19，未具备时本卡明确拒绝，不声明完整机器执行闭环已完成。未付费、未调用真实平台、未操作Git，06/12原有伙伴内容未动。

## 实际实现

0066_workflow_execution接0065_content_imports，增加正整数run.revision默认1及updated_at；BEFORE UPDATE强制每次UPDATE恰好revision+1。每个实际execute/accept/skip/redo动作仅在同事务_finish更新一次run，重放仅返回持久结果、不UPDATE。旧WorkflowService响应保持原契约，新GET execution提供revision、step attempt和历史。main真实注册新router，测试没有自行include退路。

人工execute接收白名单brief/body/scenes/prompt完整字段替换，重构服务端当前Document并调用validate_content(purpose=SOP)。MANUAL要求实际patch，REVIEW要求非空审核说明；候选保存输入doc/version、规范文档、选定patch、attempt、审核说明、actor，并进入WAITING_REVIEW，不写正式正文。候选仅来自人工输入，不把人工内容标成AI输出。共享素材预检在事务外进行，事务内重新校验具体版本/HMAC证明及输入版本。

accept锁run，复核run revision/attempt/当前候选及输入doc版本；普通采纳保存正文、文档引用、DONE、下游READY及run revision在一个business_transaction完成。派生调用主Agent新增FlowService.create_in_transaction，固定kind/sourceVersion及run/step/attempt/kind键；不先争父doc锁再反向争flow-key，锁顺序为run→flow-key→parent→child。execute持久冻结从patched父稿真正派生的完整Document，候选表输入doc/version仍指父稿依据。accept通过Flow的prepared参数一次创建该冻结新稿，输出ID与候选相同且首版version=1，不二次保存、不从OLD父稿重新拼默认prompt/节点文本；失败全部回滚。document.workflow_run_id及复合scope约束保留，商品ID只用V9 commerce_products.id且本地未移除，不附加在售条件。

动作键在tenant/run内唯一，摘要包含动作/step和请求全部版本与内容。同键同请求返回原快照，异请求IDEMPOTENCY_MISMATCH。新动作在run锁内预检动作数必须小于1000；重放优先于配额守卫。本次history摘要先加入返回结果再一次INSERT不可变结果，GET按revision排序与同版本动作响应一致，不改旧不可变历史。candidate/action历史只INSERT，数据库禁止改删；skip/redo原因持久。新reader返回候选历史及历史动作产物文档指针，redo后原文稿和候选仍可追溯。读取显式限制1000候选/动作及候选文本合计4MiB，超限拒绝并要求分页，不返回截断历史。

redo按冻结DAG计算当前step及下游闭包，清除当前机器产物指针并递增attempt；历史保留，独立DONE分支不动。RUNNING闭包拒绝重做，错误RESULT_UNKNOWN，不能重发供应商调用。当前没有provider job事实，测试仅用维护角色构造已有RUNNING状态验证负例，不能解释为真实供应商恢复已验收。run完成仅更新COMPLETED并结束其文档在途RUN引用，不触发平台上传/投放；redo重新激活运行引用。

## 向后兼容模板提案

旧模板缺schemaVersion按1，序列化不注入schemaVersion/optional字段；旧seed和在途template定义不变，全部步骤必需。requiresHumanConfirm不等于可跳过。

新模板显式schemaVersion=2允许step.optional（默认false）和optionalDependsOn（默认空且必须是dependsOn唯一子集）。仅optional且READY可skip，原因非空。默认SKIPPED不能满足下游依赖；只有下游显式列在optionalDependsOn才满足。未知schema版本拒绝。新增字段属于尚无FE消费者的新API实现proposal，提供golden后保留BE/F10最终具名复核，不逐字段预审批阻断本地实现，不宣称已经签字。

## 验证结果

初轮独占DB窗口三个组依次通过，审查两修复后独立重授窗口只重跑execution组；实际空临时库升级至包含0068的head：

- workflow-execution：修后12 passed，12.09秒（初轮9 passed，11.87秒，仅为修复前历史）。并发execute/accept重放、候选不提前改正文、run revision恰好递增、同键异payload、人工编辑冲突、旧attempt拒绝、V1必需/V2可选依赖、菱形闭包与独立分支、派生后注入失败全部回滚、AI/GENERATION不可用无假候选/无版本增长、真实main Cookie/CSRF/字段白名单、审核说明、历史/版本DB约束、非法结构和在途闭包负例。新增graphics prompt与videos generate节点NEW内容、冻结payload/ID/version1完整一致；本次响应含当前history且等于同revision GET/原键重放；维护角色构造1000历史的边界，1001动作拒绝且状态/正文/候选/事件计数无变化。
- content-workflows：8 passed，8.60秒。旧模板与开线行为回归。
- content-flows：初轮6 passed，7.16秒，含主Agent新增外层事务整体rollback验证；本轮prepared接口由主Agent独立8 passed，7.84秒（未在本子任务重复运行）。
- 本卡12单元加旧8单元共20 passed，0.65秒。V1序列化、旧字段拒绝、V2可选依赖子集/唯一性、任意分支闭包、未知版本、patch身份字段/任意路径拒绝等。
- Ruff通过，5源码+2测试mypy通过。DB窗口已明确释放。

## 保留范围与下一步

真实内容模型候选、生成jobs/供应商/成本确认与未知扣费恢复均未在本卡实现；AI_ASSISTED/GENERATION执行返回CAPABILITY_UNAVAILABLE，候选表和run revision不变，无虚构QUEUED/SUCCEEDED。这不阻塞人工事务切片，也不把缺失能力记成成功。

workflow.changed事件由reports/root的0068及公共事件模块接真实run revision/status，不冒充command_job。本卡12专项运行期间该迁移成功，但事件/SSE细项验收归其独立证据，不能仅凭本卡GET成功声称SSE完成。F10实际UI接线、BE/F10契约最终具名复核、M3-18/M3-19真实生产者仍保留。M3-16已有实际SOP推进校验调用，本卡不能据此关闭尚缺真实GENERATION入口等整卡差距。

下一依赖M3-18可接候选生产端口：必须以已治理的内容场景/提示版本/授权上下文产生持久候选，再沿本次相同采纳事务落正文，禁止用现command_parse Interpretation冒充文稿。

审查修复边界：首轮派生候选仅冻结patched父稿会使accept默认图文/视频产物残留OLD，已改冻结真实派生文稿；首轮返回history漏当前动作及1001配额写入后不可读已修。12专项证明本轮新动作行为，旧持久action.result不改写、不假称历史响应已被修补。事件专项由reports本轮9通过14.75秒；这里只引用其独立结果，不冒充本子任务执行。
