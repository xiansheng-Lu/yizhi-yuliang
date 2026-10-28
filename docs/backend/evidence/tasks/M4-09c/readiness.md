# M4-09c 分析事件切片就绪核对

2026-10-28；**PLANNED，未实现、未测试**。本次仅只读审计源码/既有证据并写计划；未使用DB、未修改源码/迁移/代理、未操作Git。当前最终full regression由主Agent独占。下一批须合并后新本地分支并确认文件边界、迁移号和DB窗口再实施。

## 已有能力与准确边界

| 能力 | 实际代码/约束 | 可复用内容 |
| --- | --- | --- |
| 分析受理 | `0042_analysis_admission.sql` 的analysis_admit；analysis_tasks/results/deliveries | 原账户account_scope、当前owner/租户/shop/账户可见性、幂等受理；只给app_login SELECT和受控函数执行，不能开放任意状态更新 |
| 内部投递 | analysis_admit原子写outbox_events，topic=`yuliang.system.analysis_accept`；worker/tasks.py accept_analysis | 已有Celery内部执行投递与去重，**不是浏览器分析通知**；payload历史字段job_id承载分析任务ID，不等于command_jobs实体，不可拿它套job.changed |
| 阶段与结果 | analysis/repository.py execute_analysis；实际最终函数定义在0058_analysis_comparisons.sql（覆盖0055） | 真实stage10/40/75/90/100与-1、lease/fence、3期冻结及原子结果成功写；只读analysis_executions不泄漏token/snapshot |
| 覆盖失败 | 0053_analysis_coverage.sql analysis_coverage_failed；worker与repository实际调用 | queued→failed的持久原因，不是成功/阶段100；该路径也必须产生一次真实失败通知 |
| 业务通知源/投影 | 0038_event_projections.sql及0040_connector_events.sql | 持久event_source_outbox、同事务写入、tenant提交序水位、source去重、7天保留、当前RLS及重读提示；与Celery outbox是两个用途不同的链路 |
| SSE | api/events.py /v1/events、platform/events.py CoreEventStream | Cookie身份、Last-Event-ID、当前权限指纹、失效关流/权限变化resync、25秒心跳、短事务轮询、断线游标和过期resync，不重放业务写 |

本次全文搜索未发现analysis_tasks状态/结果已经写入event_source_outbox的trigger或分析主题投影；不能重复做内部analysis_accept后称通知完成。

## 当前允许的实体与事件

platform/events.py EventData.entity_type仅允许：command_job、approval、execution、capability_binding、sync_source。StreamEvent.event仅允许：job.changed、approval.changed、execution.unknown、capability.changed、sync.completed。

0040的ck_stream_entity_topic同样只允许以上5对。源范围约束当前只有“真实command_jobs引用+scope_user_id”或“capability/sync账户引用+permission_code”两类。event_resource_heads也仅允许capability_binding/sync_source。现有4参数stream_event_visible对未知topic直接false。**仅改Python Literal不够，也不得伪造command_job/job_id来通过数据库外键或可见性检查。**

EventChanges目前只有大写status、extra=forbid。实际AnalysisTask HTTP status保持小写。建议下一批确认新增专用`analysis_task` × `analysis.changed`（名称尚未冻结），changes只携带派生大写QUEUED/RUNNING/SUCCEEDED/FAILED作为提示，客户端按独立通知revision重读原任务；不改AnalysisTask既有小写契约，不向流发送结果正文、error原文、事实snapshot、比较快照、租约token或用户身份资料。阶段详情从真实任务progress读取，不伪造额外计算进度。

## 精确生产点与建议实现

1. 在新前向迁移中对analysis_tasks AFTER INSERT、AFTER UPDATE的(status,progress,result_id,error)真实变化捕获。INSERT queued、stage10 running、stage40/75/90进度、stage100成功及覆盖/计算失败均由同一个受控生产点覆盖；不另在Python/Celery外发以免提交失败却已通知。仅token/lease续期不产生任务变化通知。
2. stage100的analysis_results INSERT与analysis_tasks成功更新在同事务，FK已绑定真实结果。建议以任务SUCCEEDED事件提示result可读，由既有GET任务再GET结果；暂不增加第二份结果正文事件或独立analysis_result实体，避免重复通知与隐私扩散。是否需要独立结果topic由消费契约确认后决定。
3. 使用任务独立通知revision，不能拿progress当version（queued为0、失败进度可不变、RUNNING各阶段同status），也不能把lease token当版本。可扩展既有resource_heads的允许实体并以原子upsert生成revision，源唯一键按真实entity/revision/topic去重。相同终态重放、过期fence拒绝、已冻结恢复但没有任务字段变化不得重复增加事件。
4. source/projections新增显式analysis_task_id及(tenant_id,analysis_task_id)外键，保留job_id为空；新增源scope约束，scope_user_id绑定真实analysis_tasks.user_id。**这是建议DDL，尚未申请迁移号或落盘。** 原5类事件语义不变。
5. 扩展精确analysis主题可见性分支，从真实任务读取tenant/user/shop/platform/account_scope并调用access.analysis_visible；保留原owner可见规则，不因新增可见账户扩大原account_scope，也不能只检查一个account_id代替多账户范围。tenant/user/membership/shop/任一原账户权限撤销必须拒绝读取旧投影。扩展投影函数与RLS时委托原逻辑处理已有5类主题，拒绝未知topic。
6. 重用tenant_seq提交顺序投影、source事件去重和保留/重连逻辑，不新增第二套SSE endpoint、游标或通知队列，不改业务执行器为“收到事件就重算”。

## 下一批文件边界建议

- reports：新分析事件迁移及DB生产点/权限函数/约束、专用分析事件PG与HTTP测试、M4-09c切片证据。原则上无需改现有analysis repository/worker执行逻辑，DB任务变化捕获覆盖其真实路径；若确需改，先报主Agent确认。
- 主Agent协调共享：platform/events.py新增白名单、api/events.py是否需要改动、公共runner/main/settings、迁移编号/依赖、台账和Git。需明确唯一文件owner后才能编辑；不重写既有0038/0040/0042/0053/0055/0058已合并迁移。
- FE/F12：当前ApiClient.subscribe仍是focus监听，M4-09b证据明确未接SSE页面。新增topic消费、revision去重、重读分析任务/结果、resync/注销/退避由端间确认；后端流测试不等于F12已接。
- deployment/代理：本切片默认无配置变更。若新增/改动代理、缓冲或超时配置，必须通过真实HTTP代理链验证，不用配置文本或X-Accel-Buffering响应头声称代理已验。

## 必须实际执行的验收（当前全部NOT_RUN）

- 真实受理→Worker执行→阶段→冻结结果：每个已提交状态/进度变化可重读；终态重放/并发投递/陈旧lease无重复通知；回滚不留源或投影。
- 覆盖在Worker前变化的queued→failed、计算失败、冻结快照恢复均捕获正确；不因结果已冻结而重抓事实或重放业务写。
- 提交序、两个projector并发、源去重、跨租户/跨owner、shop和原账户授权增撤、历史cursor/过期resync保持正确；源表不可由app_login任意伪造通知。
- loopback真实/v1/events：分析专用entity/topic帧，Last-Event-ID续传、状态版本重读、注销关流与权限变更resync。新增/修改HTTP行为必须真实测试；原M4-09b证据不自动覆盖新主题。
- 50连接及代理无缓冲、心跳/超时/断线轮询补偿按整卡范围另做真实验证。当前不能记PASS；不能以纯静态声明代替代理链和负载结果。

## 卡状态

技术上分析事件切片具备M4-03与M4-09b输入，可以在下一批确认范围后实施；**当前仅PLANNED**。M3-20b内容/生成事件完整输入尚未确认就绪，内容全事件、F12与代理/负载验收未完成，M4-09c整卡必须保持IN_PROGRESS，不得因分析一个切片或原SSE存在而宣称全卡通过。
