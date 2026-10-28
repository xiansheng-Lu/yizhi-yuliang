# 原 SYSTEM 恢复发布的跨 job 隔离回归

状态：双job真实PG回归首轮2项全部PASS（6.97s），Ruff/mypy通过。

真实缺陷：原 `recover_execution` 对当前Y恢复得到WAITING后，调用按同actor批扫的 `publish_schedules`。X的当前DUE/PENDING更早且撤scope时，该发布抛FORBIDDEN，外层却把Y.next_event标成AUTH_REVOKED。RLS仅tenant/actor，不保证当前shop/account范围，不能依赖批量列表隔离。

生产修复由主Agent持有：按 result.recovery_id、job_id、原actor/member精确取当前恢复event，定向发布；FORBIDDEN仅作用于实际尝试发布的该event，不以另一job错误推断当前job撤权。

本专项新文件 `test_recovery_publish_scope.py` 独占回归，不改生产Worker、旧测试或迁移。两个job均走实际现值采集/注册、真实审批B批准A申请、ExecutionOrders登记；两个目标位于同account两shop，Y经Archived合成HTTP timeout生成实际网络未知，X保持更早DUE/PENDING。只有到期/排序/当前scope由维护fixture调整，不手插job/receipt/recovery业务状态。

用例1撤X店铺范围而保留Y：调用实际 recover_execution task入口，Y应WAITING且自己的后继PUBLISHED，无AUTH_BLOCKED证据；X恢复行与outbox发布次数保持原样。用例2同时撤Y，只有Y应持久BLOCKED_AUTH，X仍原样，不调用sender。scope使用精确ACCOUNT与SHOP，不保留ALL_TENANT掩盖隔离缺陷。

实际命令 `python scripts/test_infrastructure.py --group recovery-publish-scope --details`，空库链head `0077_approved_dispatch_worker`。首轮通过，无fixture/源码修复；DB窗口已明确释放，未额外运行旧Worker/full。

发送后继使用本地确认callback，不能冒充真实Rabbit/Worker测试；已有独立pipeline7由主Agent完整all覆盖。本专项平台与付费调用0，新Celery/MQ进程0。
