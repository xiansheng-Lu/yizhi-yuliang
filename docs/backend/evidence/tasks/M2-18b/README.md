# M2-18b 当前授权的独立只读执行证据

状态：内部读服务已通过真实 PG/Redis 生产链技术专项。整卡 IN_PROGRESS；没有 HTTP/前端合同接线或人工纠错写入。

## 范围与既有实现

没有重写 ExecutionOrders/ExecutionReceipts/ExecutionRecovery/IndependentReadback。旧父任务 M2-13b、M2-11b、M2-17a 已有真实技术证据；细分 M2-18b 未注册不代表这些内核不存在。

新 `ExecutionEvidenceReads.read(job_id)` 复用实际持久 orders/attempts/receipts/recoveries/recovery_evidence、独立 READ GET 归档传输结果及 verified_action_facts，只返回严格类型白名单。SENT 是本地发送登记，不代表发生网络调用或平台受理；accepted 保留 bool/null；读取 E0/E1 不制造 E2 或成功。独立 readback 的比较结果目前由原服务返回而非单独持久，因此本接口只读真实传输结果元数据；只有独立 verified_action_facts 才列作已有核验事实，不重新计算比较/金额。

## 精确权限与数据库边界

- 沿0010 workspace_private 本人 job 范围，current tenant/user/member 均由可信 VerifiedIdentity 提供。
- 使用0060 `snapshot_member_version` 校验活跃主体和现有 snapshot.read，再用既有 configuration_scope 核 AD_PLAN 对应当前 campaign/shop/account/实体/链接双范围；不借 CLAIM/EXECUTE，不加角色权限。
- 原执行者撤去 task.create/plan.write 而保留 snapshot.read，可独立读本人历史；另一合法同租户 actor 仍不可见。不扩大原 RLS 的跨 actor 可见性。
- 读取历史不要求当前平台 OAuth 可发送；成员/权限/范围撤销、对象移除仍拒绝。当前只接 AD_PLAN，人工场次任务明确未集成广告执行源。
- 0075 `access.execution_evidence_read(member,user,job)` 是固定 search_path 的 STABLE SECURITY DEFINER，只返回逐列JSON安全投影，无泛化表/SQL/标识符参数；PUBLIC/app_auth 无执行权，app_login 仅该函数执行权，原表 RLS/写权限不变。旧 recovery.outcome 是自由文本，读取仅维护旧0036生产者固定码，未知值改为 UNRECOGNIZED 并给固定脱敏warning，原事实不改；模型异常转固定 DATA_COVERAGE 错误，不透异常原文。
- 服务以 REPEATABLE READ、read-only 事务读取。每集合探测1001，超过1000明确 DATA_COVERAGE_INSUFFICIENT，禁止静默截断。无 raw/payload/headers/token/dispatch secret/fence/hash/wire/request-key。

## 验证范围

模型8项收口后重跑 PASS（0.29s，2026-10-28T15:45:43Z）；初版8项0.30s不作为收口版本结果。最终Ruff/check-format/mypy5文件 PASS。

真实 PG/Redis `execution-evidence-reads` 最终16 PASS（16.01s），使用已有 ExecutionOrders/Receipts/Recovery/IndependentReadback 生产者及合成上游建事实；覆盖独立读权限、真实未发送恢复E1、UNKNOWN不伪成功、独立回读不伪E2、当前撤权/范围、跨actor/伪member/跨tenant、真实回执并发提交时一致快照、旧自由文本outcome敏感样式不泄漏。模型校验异常转换用显式故障注入验证，不冒称生产者生成非法历史结构。

首轮15 PASS/1 FAIL（17.32s）：故障 fixture 试图硬删仍被 command_jobs 引用的 entity_links，原FK正确以23503拒绝。仅修自有测试：明确断言硬删被拒、历史关联保留；再合法软移除店铺验证当前scope拒绝。未放宽约束、RLS或业务模块。复跑全16通过，DB已明确释放。

准确迁移head `0075_execution_evidence_reads`，down_revision `0074_resource_query_cursors`。0074文件名仍为 resource_query_continuations，revision因既有Alembic32字符限制由主Agent协调缩短；0075业务函数不受此命名调整影响。记录UTC `2026-10-28T16:02:59Z`（本地已2026-10-29），原授权沿用，无额度重置或新平台请求。

真实平台/付费调用0。未交付正式 attempts HTTP 或 F06/F13 前端消费；不存在冻结前端 attempts 合同，不能自造新端点声称接线。人工依据登记、对账定性写入及整卡最终人工验收另列；读取不会更改任务状态、额度、成功、原回执或隔离。
