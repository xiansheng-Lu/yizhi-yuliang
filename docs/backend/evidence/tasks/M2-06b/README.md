# M2-06b V1/V8状态与回执投影

负责人BE，协作F06。WAITING_REVIEW，基线bd17bc5，2026-10-28持续研发授权。输入M2-06a、M2-13b及M2-11b技术证据；G0/E08保留。

三条DoD：提交未知EXECUTING、已受理回读未知VERIFYING，V8均UNKNOWN；成功只认PLATFORM+accepted+MATCHED，审批不提前修改计划值；V1不新增UNKNOWN/SYSTEM，人工交接不冒充平台完成。

前端legacyExecution当前未覆盖EXECUTING/VERIFYING的UNKNOWN，F06接线与页面验收需单独处理，本卡不把后端契约测试当浏览器联调。

## 实现与DoD

1. command.projections是V1与V8共用事实源，读取原申请、按事件序排列的历史、当前执行状态和最后恢复核对时间；最终投影使用一条SQL的同一MVCC快照，避免READ COMMITTED多次查询把旧版本与新历史拼接，不为读操作授予UPDATE/行锁权限。JobApplications.get和幂等重读接该投影，DeliveryJobQueries重新核对当前目标权限并适配相同结果。当前已实现的V8业务源是实际ADQ广告申请，不将其他平台账户重标为ADQ。
2. 在途且执行UNKNOWN、RESULT_UNKNOWN或回执UNKNOWN时，明确受理仍保留VERIFYING，未明确受理保留EXECUTING；V8统一UNKNOWN且safeRetry=false。没有响应就保持公共execution_receipt=null，通过error和内部verification表达未知，不造PLATFORM或新增SYSTEM公共枚举。已受理回读未知只在读视图中解释UNKNOWN，不能回写原回执或伪造verified_at；last_checked_at单独来自原核对/验证事实。明确拒绝的FAILED即使verification=UNKNOWN也保持V8 FAILED，不凭“UNKNOWN”单字段抹掉拒绝定性。历史按SUBMIT_UNKNOWN/READ_UNKNOWN控制事件解释，保留后续受理/匹配事件。
3. SUCCEEDED仅允许PLATFORM、accepted=true、MATCHED和观测值；金额/布尔还须与申请新值一致，ROI依平台精度的比较归M2-17，不把模型浮点相等当平台精度规则。confirmed_change仅成功时提供，含义是该次动作已确认结果，不代表当前平台现值。V8 before/after是申请变更提案；审批、受理或投影均不写计划镜像。没有执行单时V8按既有legacyJobId表示真实申请的兼容视图，不建假执行行。人工开关播没有广告账户，返回真实MANUAL_REQUIRED申请且不生成V8广告执行、不造账户ID或平台回执，模型拒绝人工交接平台成功。

## 技术验证与边界

22项新增契约测试覆盖十二态原字段、两类UNKNOWN、无响应无伪回执、拒绝不被未知覆盖、审批/受理不生效、成功来源/受理/匹配/值错误与人工交接拒绝，以及未知不能把终态改回进行中。总计543项单元/契约通过。

9项真实PG专项覆盖Mock原响应驱动的受理/拒绝/超时、原执行与同一V1/V8查询、实际批准后计划镜像仍100且无confirmed_change、独立留档经维护校验E2实际结算才EFFECTIVE、并发读取与回执提交版本历史一致、原申请私有隔离及权限撤销、实际恢复后的最后核对时间、实际人工交接申请不伪造平台执行。回读UNKNOWN的输入通过明确维护夹具设置内部状态，仅验证该状态的查询契约，未声称M2-17的生产者/退避已完成。trace.json含合成request/job/execution/attempt/receipt关联。全部供应商输入明确Mock，真实平台/付费调用均0。

全仓Ruff、226文件格式、181文件Mypy及上述专项已通过。全量572项集成通过，1项生命周期重启演练明确跳过；末次核对时间展示条件补充后又复跑9项专项通过。开发库仍0036、迁移角色NOLOGIN、申请/执行/恢复/资金终结业务行0；完整执行顺序和源码基线见verification.json。

## F06接线、回退与复核

后端只读服务及V8模型已提供；生命周期HTTP装配归M2-05b，完整投放工作区API与F06页面联调仍按对应任务接入。[frontend-handoff.md](frontend-handoff.md)记录旧兼容映射缺项和期望，不改前端负责人当前文件，不将后端契约通过当浏览器通过。人工BE/FE签字与G0/E08继续PENDING。

本卡不新增迁移/新状态/写API。回退可恢复旧查询接线并停止新V8适配，持久申请、历史、执行、恢复与资金账不删除；成功来源与人工交接的模型约束应保留。原06/12两份各两行未提交修改继续排除。
