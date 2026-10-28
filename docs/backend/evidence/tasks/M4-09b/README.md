# M4-09b 核心SSE与权限续查

状态WAITING_REVIEW；源提交f425af46a6571de8b06b408799cb167c30002608。负责人BE，协作F12，基线bb208e7，整合c703654。M4-09a/M0-11c技术输入已验证；G0/E08及F12实际页面验收保留。

## 实现范围

GET `/v1/events`在已开放身份API内装配，使用现有HttpOnly会话Cookie与精确Origin校验。只接收Last-Event-ID；租户、用户和范围取自服务端会话，不从游标或客户端body设置。CORS显式允许该续传头，响应为text/event-stream、Cache-Control:no-store、X-Accel-Buffering:no。

初始`: connected`刷新响应头，不推进游标；普通帧id为租户:tenant_seq，data仅含tenantId/entityType/entityId/version/changes/at。空闲25秒发`: ping`。PG查询在线程池短事务内完成，事务/连接不跨异步等待或网络yield持有；默认每秒查当前会话与范围，每个业务帧发送前再查一次。退出、过期、移除或会话身份改变结束流，权限指纹改变发resync后结束。指纹核对实际角色权限、范围和资源可见性，即使维护身份改范围未递增authz_version，也不会沿用旧缓存。

游标跨租户、超前或超出七天保留边界时，发当前租户水位的resync并结束，由客户端重新取当前数据。分页和投影沿用M4-09a租户提交序，不依赖全局identity；断线只重读事件，不调用任何平台写派发或重试入口。

## 五类真实生产者

| 事件 | 实际生产依据 | version语义 |
|---|---|---|
| job.changed | 完整command_jobs申请/状态修改 | V1任务实际version |
| approval.changed | COMMAND_JOB审批记录插入或决定 | 审批记录实际version |
| execution.unknown | 执行单实际转为UNKNOWN | 执行单实际version |
| capability.changed | binding启用状态、证据插入或持久截止到期 | 该binding的持久通知修订 |
| sync.completed | SyncStore实际完成、暂停或中止批次 | 该sync_source的持久通知修订 |

后三种业务状态及源均在PG中产生，不接收HTTP200或客户端成功标志。capability的CHANGED是要求重读的提示，INACTIVE是实际绑定停用，不等于能力已授权或可写。同步SUCCESS/PARTIAL/FAILED/RATE_LIMITED/PAUSED保留真实状态，不能把完成通知当成全量同步成功。

0040增加event_resource_heads、capability_event_deadlines及账户/动作范围列。通知修订独立于不可变binding.revision和同步分页游标version；执行前合同revision不因通知而变。客户端按entityType+entityId记录最后收到的通知version并重新读取当前能力/同步状态，不能把通知version传入平台执行合同。

证据写入时原子登记真实expires_at截止，不预分配未来通知版本。截止到期在SSE轮询前按当前租户有界捕获、当时分配通知修订、原子标记已处理；断线期间仍保留截止。后到旧证据也不会使到期通知的版本倒退，重复捕获不新增事件。没有依赖进程内计时器保存业务截止，也没有长ETA或Worker sleep。

账户通知逐次核对实际binding.permission_code、当前有效成员/角色以及店铺和账户双范围；原有任务/未知执行保持申请人私有，审批允许当前合格的其他审核人。运行角色不能直接伪造事件源、修订、水位或截止。

## 三条DoD与证据

| 标准 | 本地技术验证 | 证据 |
|---|---|---|
| 租户续传、resync、心跳，无长事务 | 真实Uvicorn回环HTTP流，跨租户/超前/过旧分别resync；实际等待25秒心跳，由PG管理员观察无idle-in-transaction | frames-resume.json、heartbeat.json；test_core_sse.py |
| 订阅/补发查当前会话与范围，退出/撤权关流 | 真实Cookie会话退出后EOF；未升authz_version的实际范围修改触发resync+EOF；Origin/未登录/非法游标在流头前拒绝；账户通知缺任一资源范围/动作权限不可见 | test_core_sse.py、test_connector_events.py |
| 断线/重复不重放写，按版本重读 | 重连仅收到新事件，实际JobApplications.get结果与帧version一致，申请2、执行单0；重复证据/截止不多发，通知修订单调 | frames-resume.json、capability-frame.json；F12接线说明 |

专用20项真实本地PG/HTTP用例，包括证据/通知/截止共同回滚、真实SyncStore的SUCCESS与PARTIAL、binding停用、到期与后到旧证据、通知表写权限拒绝。平台及OAuth输入明确合成，供应商/平台调用0、付费调用0；25秒心跳实际等待，测试只加快撤权轮询，未改心跳常数。

## F12接线与仍待验收

当前前端ApiClient.subscribe仍是focus监听，没有SSE客户端；本卡不修改伙伴前端。F12须携带Cookie，处理Last-Event-ID、版本去重、resync后全量重读、注销关闭、断线退避及轮询回退。首次页面重开如何带游标由F12实现；服务端当前未提供query游标别名，也不假定原生EventSource能任意设置首个请求头。

本地重读证据调用真实查询服务，不冒充尚未完成的任务生命周期HTTP或完整FrontendSnapshot；M2-05b/M1-18技术缺项仍保留。F12真实页面、代理/Nginx、负载与断线回退验收未运行；本卡后端技术证据不替代这些验收。内容/生成/分析全事件归M4-09c及其依赖。

## 验证与回滚

最终命令、源提交及计数见verification.json/static-check.json；最终609项单元/契约、20项专用、651项全量集成通过；生命周期1项跳过。专用流验证使用既有隔离fixture数据库与临时回环HTTP端口，不重建真实平台/付费现场。迁移开发库前先结束所有PG回归，随后恢复迁移角色NOLOGIN并核对空业务数据。

回滚可停止SSE路由装配，事件源与截止保持持久，恢复后继续投影/补发；不倒退tenant_seq，不重放任何业务写。0040采用前向迁移保留通知证据。

BE复核PENDING；FE/F12复核PENDING；G0/E08未通过，Agent未代签。

共享测试窗口修复：隔离测试库仍共享app_migrate全局角色，本次和migrate_local共用760001锁并保持到全部夹具结束，恢复NOLOGIN后释放。实际互斥探针见migration-window.json；该探针未执行迁移或角色修改。
