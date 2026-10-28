# M2-15 可靠批准事件与派发编排

2026-10-29，本批基线main94834e2，本地codex/backend-approved-execution-outbox。负责人BE，协作F06/F13；固定一名前端、一名后端，Agent辅助。状态IN_PROGRESS：本批本地技术链已完整验证，生产WRITE适配器/正式serializer、完整前端生命周期及BE/FE最终验收仍待完成。

## 实际输入与范围

既有Approvals、ExecutionOrders、RecordedPlatformClient、Receipts、Recovery、outbox/inbox已有实际代码与技术证据；本批补批准事务的可靠producer及正式内部Worker编排，不重建这些内核。完整M2-05b前端生命周期与M1-18快照仍有缺口，本切片只消费已有真实内部批准生产者的AD_PLAN任务，不声称完整HTTP或真实平台验收。

- beans：0076_approval_execution_events，原审批函数前向包装与私有旧核权限收口、不可变批准来源、A创建/B批准的执行主体绑定、producer实际测试。
- commerce：0077_approved_dispatch_worker，当前批准/权限/意图核验、一次性发送资格、提交SENT后事务外发送、重投只核对恢复；runtime registry默认空，不能由消息覆盖。
- reports：真实Rabbit与隔离loopback HTTP的管线、进程退出/重投/响应持久化失败验证及独立审查。
- 主Agent：Worker任务与execute队列精确路由、公共runner、迁移窗口、台账、回归及Git。

## 可核对验收

1. 审批、来源、outbox及后续审计/资金副作用同事务；REJECT、冲突、并发或重放不重复发事件，不能伪造另一actor或借旧函数旁路。
2. broker只承载五个ID；消费者核不可伪造的真实批准来源，执行者取原申请A而非审批B。当前权限失效只记录受控阻断，不授发送权。
3. consume回调仅数据库副作用，实际SENT提交后才出事务调用现有归档客户端；丢失内存ticket、重复消息或已SENT只恢复核对，不重新WRITE。
4. 默认无生产WRITE serializer/profile，持久BLOCKED_EXECUTOR且零attempt/HTTP。后续启用配置不能自动执行既有blocked事件；不改任务十二态或制造成功/退款。
5. MQ中断/发布确认丢失、双Worker、提交后退出、wire后落库失败及非法消息有真实设施证据；未执行前不登记PASS。

本批所有Agent设施窗口均已明确释放，源文件冻结后最终完整回归通过；真实平台写与付费继续需要既有明确账户×动作×额度许可。本批仅tests内固定synthetic DTO及本机HTTP，不把它们当官方WRITE协议、真实平台回执或BE/FE具名验收。

## 最终实证

本批完整1098集成/1可选重启跳过1144.62s，999单元/契约9.68s、247源码mypy/Ruff通过；producer18/dispatch-handoff13/真实Rabbit与Inspector7/双job2已验证并纳最新full。开发库0077、52相关表仅5系统模板其余0、迁移角色NOLOGIN，旧审批core应用不可执行。默认生产WRITE registry仍空、平台/paid0。准备中文提交合并main推送后新分支继续人工依据/结构化时间线。详见[本批总验证](../../approved-execution-verification.json)及producer/dispatch/pipeline/recovery-scope各自真实记录。前期失败与bootstrap/断言修复保留，不将早期部分通过当最终结果。
