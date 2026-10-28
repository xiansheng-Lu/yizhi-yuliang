# F12 核心事件流接线

后端负责人BE；前端协作FE/F12。后端本地技术验收与前端最终验收分别登记，不代签。

## 已有后端入口

GET `/v1/events`，现有HttpOnly登录Cookie，精确Origin白名单；响应text/event-stream、no-store、X-Accel-Buffering:no。CORS允许Last-Event-ID。服务端不接收客户端tenantId/账号范围来建立身份，也没有用于首次续传的query参数。

普通帧格式与实际ID见[frames-resume.json](frames-resume.json)；`id`为租户:tenant_seq，`data`包含tenantId、entityType、entityId、version、changes、at，不包含完整资源。`: connected`和25秒`: ping`均为注释，不作业务事件或推进缓存版本。

| event | entityType / entityId | version解释 | 客户端重读目标 |
|---|---|---|---|
| job.changed | command_job / 任务UUID | V1任务version | 当前可见任务 |
| approval.changed | approval / 审批UUID | 审批记录version | 当前可见审批与关联任务 |
| execution.unknown | execution / 执行单UUID | 执行单version | 同执行单核对信息和关联任务 |
| capability.changed | capability_binding / 绑定UUID | 持久通知修订 | 当前完整配置上下文中的能力/连接状态 |
| sync.completed | sync_source / 来源UUID | 持久通知修订 | 当前范围的同步健康、概况和计划 |

能力/同步通知version独立于执行前binding.revision或分页游标version；不要将通知修订传进写前合同。以entityType+entityId对通知去重并触发重读，不能用一个账户级版本覆盖不同产品/目标/版位的能力。CHANGED仅表示重新读取；INACTIVE表示停用。sync.completed的PARTIAL/FAILED/RATE_LIMITED/PAUSED也需显示实际状态，不当成功同步。

execution.unknown是通知名，V1仍只有十二态；查询投影可能是EXECUTING+空回执或VERIFYING+未知核验，不创造第十三个UNKNOWN任务状态，不弹成确定失败或自动重发。

## 接线要求

1. 当前`frontend/src/services/api-client.ts`的subscribe仅注册focus事件，尚未接SSE。保持既有订阅注销函数语义，页面销毁/切换身份/退出时关闭流并清理旧身份的游标和缓存。
2. 同一连接的断线续传携Last-Event-ID；页面重开如何恢复持久游标需由FE确定。原生EventSource自动续传与首次请求手工设置头不同，不能假定现有subscribe已经完成它。使用fetch流时带credentials和正确AbortSignal。
3. 对每个资源保留已处理通知version，忽略重复/较旧事件，合并短时间重复刷新；用当前权限重新取资源，通知本身不替代资源响应。不要依据通知或旧能力快照开放写控件。
4. resync帧带当前租户游标和reason，服务端随后结束流；清理旧快照并重新读取当前可见资源，再重新订阅。权限/范围变化、跨租户/过旧/超前游标均可能触发。不能用过去的tenant游标打开新租户数据。
5. 401/退出停止重连；其余断流按契约退避，超过阈值回落既有轮询。重连和轮询只重新读，不重放create/confirm/approve/retry等写请求。

## 待联合验收

实际前端断线/重连、重复事件/版本合并、页面重开、撤权/退出关流、代理缓冲/心跳及轮询回退均待F12验证。本轮没有修改伙伴前端或运行这些页面用例。

本地帧version重读证据使用真实JobApplications查询服务。完整FrontendSnapshot和任务生命周期HTTP仍分别待M1-18/M2-05b，不能用M0空快照替代或宣称完整页面数据链已打通。能力/同步专用字段界面还需M3-07及相应镜像/上下文接线；本卡只验证真实源通知和当前权限过滤。

真实平台授权、非空业务数据和逐动作许可仍按E01/E02/E03交接。五类通知、Mock平台响应与本地HTTP200均不等于平台验收或G-MVP通过。
