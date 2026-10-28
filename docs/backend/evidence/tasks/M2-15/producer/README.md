# M2-15 真实批准的可靠 outbox producer

状态：producer 内部切片已通过真实 PG/审批生产链专项。M2-15 整卡 IN_PROGRESS；不声称生产 WRITE 适配/真实平台动作就绪。

## 新切片与原内核

复用已发布0037真实审批/风险/版本/状态逻辑，前向0076将原13参 `approval_decide` 重命名为私有 `approval_decide_core_0037` 并收回 PUBLIC/app_login/app_auth EXECUTE；同名同签名 wrapper 调用原核并原样返回 `{job,approval,decision}`。仅真实新 APPROVE 决定在同事务生成来源与 outbox。REJECT、旧版本重放不发事件，迁移不扫描历史审批补发。

Python `Approvals._decide` 原调用不用旁路：REJECT预算释放、两条审批/任务审计仍位于原 unit-of-work；后续任一步失败，审批决定/状态/来源/outbox全部回滚。重复review保留原 VERSION_CONFLICT 语义，不能把它改成新一轮批准或自动重发。

## 来源、身份和去重

事件固定 `yuliang.system.approved_execution`，payload 严格现有 WorkMessage 五ID。批准者 B 的decision只证明批准，执行 subject 永远取真实 `approvals.requester_id/requester_membership_id`（A），交叉核 job.created_by 与复合membershipFK，不取B或客户端传入身份。

event ID由数据库固定domain、tenant、job、批准后job版本、真实decision ID的SHA256截128位转UUID确定生成，仅作标识不作授权。`approval_execution_events` 唯一decision及job/version、关联真实审批/事件/成员，运行角色无表权限，来源UPDATE/DELETE阻断。新type outbox精确guard防同tenant任意INSERT造事件；identity/payload不可改，后续发布确认状态更新不要求job仍停在APPROVED。

`load_approved_event(session,item)` 返回 `ApprovedEvent`，核真实来源、完整broker五ID与当前活跃subject，不代CLAIM；`.identity`只是数据库复核后的原A身份，不授发送资格。

`load_approved_origin`/`approved_execution_origin` 对失活主体只提供同一真实来源的ID/版本与subject_active标记，用于下游0077窄BLOCKED_AUTH事实，不能转为任意更新、业务payload读取或发送许可。真正当时权限、策略、现值、授权及一次发送资格仍由原执行内核与下游准入守卫核对。原A失活不应ACK丢失去向或无限重投，也不能因后来配置启用自动重发已blocked旧消息。

## 验证边界

单元4项 PASS（0.62s），Ruff/mypy4文件 PASS。真实 PG/Redis `approval-execution-outbox` 首轮18项全部 PASS（16.40s）：真实A创建/B批、并发一决定一事件、REJECT与旧版本、审计/资金回滚、私有core权限/来源表权限、outbox伪造、broker五字段伪造、A失活、MQ确认丢失原event重投、来源不可变、历史旧核真实决定不补发。无SQL/源码/fixture修复。历史夹具仅维护角色调用真实旧核以模拟既有记录，运行角色仍被42501拒绝；不手插假成功。

空库链实际升级至 `0077_approved_dispatch_worker`（0076→0075）。本专项未启动Celery/helper/MQ进程；确认丢失使用本地sender回调，不能冒充真实Rabbit Worker验收。DB窗口已明确释放。验证记录UTC `2026-10-28T16:31:19Z`（本地2026-10-29），沿原授权，无额度重置。

默认无正式执行runtime时，由下游真实consumer持久BLOCKED_EXECUTOR再ACK，不创建attempt、SENT或平台HTTP；producer测试不替代该consumer验收。Rabbit/worker技术验证由独立测试负责，未运行前不记通过。真实平台/付费调用0。
