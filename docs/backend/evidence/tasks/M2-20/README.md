# M2-20 内部结构化执行时间线

2026-10-29，负责人BE，协作F06/F13。本批基线main e71d0eb，本地codex/backend-manual-evidence-timeline；状态IN_PROGRESS。当前实现内部只读时间线与独立验证，尚无本批PG结果，不复用此前1098集成作为新代码通过。

既有job_events、审批决定、发送尝试、回执、恢复依据、verified_action_facts及0076/0077批准/派发来源已实际验证；0078新增人工说明仅作未验证来源。读取沿本人snapshot.read和当前双端scope，不扩展跨actor可见性。

时间线将提供真实时间、稳定源ID、来源类别与可核对版本；可变当前行只能作为当前快照，不能推断历史版本。SENT是本地登记，READ传输记录不代替E2，人工说明引用真实回执仍是未验证说明。未知来源版本明确null和缺口，不填当前版本冒充当时事实。原始载荷、凭据、自由恢复文本与审批评论不透传。

commerce独占0079_execution_timeline及内部models/readservice和自测；beans独占0078人工说明；reports独立SQL对照/权限/证据语义审查；主Agent统一runner、迁移窗口、台账和Git。源码和迁移冻结后串行验证，当前没有授予DB/设施窗口。

既有前端只有基础任务history，聊天history与此无关。本批不新增未冻结的HTTP或修改前端合同，不声明浏览器或结构化时间线已接入。站内通知、审批收件人策略、真实邮件与完整M2-05b仍另列；M0-15是队列基础，验证码SMTP验收不等于通用通知发送许可。真实平台及付费调用为0，BE/FE具名验收待完成。
