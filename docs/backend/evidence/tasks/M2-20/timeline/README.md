# M2-20 内部结构化执行时间线

状态：IMPLEMENTED，待真实PG验证及独立SQL对照；无新HTTP/前端生产合同，不代表完整UI或整卡验收。

入口 `ExecutionTimelineReads(engine,identity).read(job_id)`，只读REPEATABLE READ事务。0079复用0075真实原actor `snapshot.read` 与当前店铺/账户双scope门，不借CLAIM，不扩大跨actor访问，不检查历史读取不需要的当前平台发送资格。

`events`只读真实job_events、审批创建及不可变决定、attempt.sent_at本地登记、不可变回执、恢复证据、READ raw RESPONSE/ERROR实际记录时间、verified_action_facts.verified_at、0076批准来源、0077派发记录创建及0078人工注记。按真实时间/source_kind/source_id固定C排序；中文摘要仅固定代码映射，无自由detail/outcome/body/headers/token/hash透出。人工备注只列ID/类别/创建时间和UNVERIFIED_NOTE，不嵌正文或reference_code，不继承引用receipt的证据等级。

`current_snapshots`单列JOB/ORDER/ATTEMPT/RECOVERY/APPROVAL/DISPATCH的当前state/version/updated_at，并固定标记CURRENT_SNAPSHOT_NOT_HISTORICAL_EVENT。审批/派发创建事件不猜历史版本，返回null及HISTORICAL_VERSION_UNKNOWN；未知updated_at也用null。审批决定仅采用不可变resource_version_after/approval_version_after。

SENT只证明本地登记，READ传输观察不等于配置匹配；E2只来自已持久verified_action_facts。现正式M2-17b Writer仍未具备，独立验收可通过已有维护校验导入路径证明有据E2展示，但不得称正式运行E2生产已完成。

完整events及snapshots各上限1000，查询1001探测后明确54000/DATA_COVERAGE_INSUFFICIENT，不静默截断。自有PG边界拟通过真实人工note append1000次产生组合超限，不用手拼DTO冒充来源。

当前静态：Ruff通过，2源码mypy通过，5单元通过0.29秒。自有PG7项、独立SQL对照4项均待唯一DB授权窗口。真实平台/付费0，尚未DB/迁移/设施操作。
