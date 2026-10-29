# 独立结构化时间线核验

状态：IMPLEMENTED_NOT_RUN。独立测试 `backend/tests/integration/test_execution_timeline_verification.py` 四项已编写，静态 Ruff/编译通过；尚未获数据库窗口，未运行集成测试。

测试从真实内部 A 创建/B 批准、批准来源、派发、执行尝试、回执、恢复观察与人工说明生产者创建数据；仅平台上游使用明确合成 transport。不手工拼接时间线 DTO。独立维护连接直接查源表的 ID/时间集合与真实 job_events 版本，和本人只读服务输出比对，核对稳定顺序/双读一致（排除 as_of）及读取/人工说明前后任务、执行、预算与资金 hold 不变。

另区分 SENT 本地登记与物理请求/平台受理；READ 检查在内存判 MATCHED，不等于已经持久落账的 E2；人工说明即使引用真实 receipt/recovery，仍只能 UNVERIFIED_NOTE，并且时间线不输出说明正文或参考编号。可变行的当前状态、版本与 updated_at 只放 current_snapshots，不反填创建事件的历史版本。

E2 正例使用实际已存在的维护校验 `record_verified_action`（通过既有 `independent_match` 技术 helper）验证独立 GET 归档后入事实，再调用真实 `BudgetSettlement.match`，最后独立 SQL 对照 VERIFIED_FACT。该入口明确是维护导入技术输入；实际没有 `Readback.apply` 正式生产者，M2-17b 正式 Writer 缺口继续保留，不能声称仅 `inspect MATCHED` 就自动得到 E2。

没有新 HTTP/前端合同或前端消费验证。站内通知/邮件通知不在本独立测试范围，真实平台/付费调用均不授权。

独立审查发现并交 owner 修复：0078 Python 使用 NFKC 后识别凭据样式，原 SQL 对原文识别，直调可能绕过全角 Bearer；owner 已补 SQL 统一规范化及明确 NULL 拒绝，实际 PG 结果待串行验证。本目录不会将静态修复写成运行通过。
