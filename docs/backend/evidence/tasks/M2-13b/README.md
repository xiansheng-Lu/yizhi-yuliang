# M2-13b 平台回执与结果落账

负责人BE，协作F06。WAITING_REVIEW，基线a98902d。M2-13a技术输入，G0/E08保留。

三条DoD：仅可验证响应标记PLATFORM，accepted未知保留null；重复/迟到/旧fence不覆盖更高状态；回执/状态/审计短事务共同落库，HTTP在事务外。

## 实现与三条DoD

1. 0033增加只读execution_receipts。record只接attempt_id、已有raw_id与预期执行版本，source/accepted/E1等级从同租户、本人、同执行尝试的原始WRITE根和原始事件推导，不接受调用者传来的受理布尔值或任意回执JSON。PLATFORM必须有先前ATTEMPT、真实归档RESPONSE/RESPONDED、响应摘要和HTTP状态；2xx与业务ACCEPTED同时成立才accepted=true，明确业务拒绝才accepted=false/E1。200未确认、5xx仍accepted=null/E0；无响应的ATTEMPT/ERROR为SYSTEM/accepted=null/E0，不伪造平台受理或平台回执号。原始trace只留HMAC，未明示真正回执号时platform_receipt_id保持null，不能拿trace摘要冒充回执号。
2. 回执以租户/attempt/raw唯一，重复处理返回首次结果快照，不产生第二次状态转移或审计。租户锁后按申请/执行/尝试/守卫取行锁，旧fence/owner、非当前attempt、旧版本、落后raw事件与已经受理或终态均存为未应用隔离结果。未知转内部UNKNOWN/公开EXECUTING和QUARANTINED，不增加公开状态枚举，不自动重发；同fence的后续明确响应可以从UNKNOWN进入VERIFYING/FAILED，但守卫保守留隔离直到后续结算或对账定性。迟到响应原文仍可归档，不能恢复旧fence写状态资格。
3. 风险/审批/冻结意图不由结果写入替换。SQL重新推导实际证据及转移，核对JobReceipt字段和响应时间、错误分类与不可重试标志，再用既有条件状态内核写job/事件、执行态、attempt和回执；Python审计使用同Session同事务，审计失败全部回滚。外部HTTP已经由M2-13a在前一个归档/网络阶段完成，本卡没有HTTP回调或跨网络事务。lookup与重放仍重核原申请当前访问权限，其他调用者不可读取。

无平台响应时V1的execution_receipt保持null，error=RESULT_UNKNOWN、retryable=false；内部SYSTEM观察另有受控记录。纯状态机新增仅内部的transport_unknown事实，必须同时有持久proof_ref才可省略平台回执，不放宽其他事件的受理/E1/E2条件。V1/V8最终页面投影归M2-06b/F06，不改变JobReceipt.source=PLATFORM的既有公开契约。

## 实际验证

17项真实PG专项覆盖明确受理/拒绝、200未确认、5xx、真正合成网络超时；四并发结果只落一回执/转移/审计；先处理ATTEMPT系统未知后同fence明确响应；旧fence/attempt/version、已核验内部终态与旧证据隔离；在HTTP响应返回前故障注入fence变化，响应可留档但不可落当前状态；审计失败回滚回执/状态/事件且原始档案仍在；无关raw与不存在attempt拒绝；运行身份不能改删；当前权限撤销后不能lookup，其他实际审批成员不能读取申请人私有回执。内部终态为明确维护故障输入，不冒充M2-17核验生产者。

另有纯状态机回归验证无响应只能在明确内部事实和证明齐备时保留null平台回执。供应商全部为MockTransport，审批阶段与序列化合同明确合成；PG、并发、短事务、RLS、归档/内核/审计真实执行。资金预占在受理、拒绝和未知时均仍HELD，本卡不绕过M2-10c提前释放或结算。完整命令、计数、开发库迁移见verification.json，trace仅受控ID和状态，不含密钥或账户真实数据。

## 边界与回退

真实供应商协议/账户E01、Worker派发、回读生产者与调度M2-17、资金结算M2-10c、恢复器M2-11b和F06具名联调尚待后续卡。旧fence隔离记录不能作为可直接释放的E1；后续对账须重新验证当前尝试与平台证据。回退关闭结果消费者并保留原始事件、回执和在途账，不能删未知或覆盖旧回执。BE/FE签字、G0/E08未过；下一卡继续资金结算与释放。
