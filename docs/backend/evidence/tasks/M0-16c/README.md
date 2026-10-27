# M0-16c 基础消息故障演练

2026-10-27；负责人BE，无FE协作；代码基线 75cb1ef170ce328b3d259cb3775f7aa17362e109。
状态WAITING_REVIEW；采用M0-16b技术输入，不代签前置卡或本卡。

七个独立真实进程覆盖发布前、confirm前后、处理前/处理中、ACK前后退出；归档实际PID、事件ID、退出码、崩溃库状态及最终processedAt。每个事件内部效果一次，重复消息无重复业务写入。

三条DoD的技术断言由test_durable_messaging.py与messaging_fault_process.py覆盖，实际命令与七阶段进程记录见[verification.json](verification.json)。30项unit/contract、46项真实integration通过；ruff、格式和完整mypy通过，开发库已升级0007。

范围只含内部数据库恢复协议；DUE记录不是外部业务执行成功。平台/资金/AI未知结果保持guard关闭，不套用自动重放。dispatch_pending须由服务端提供已验证租户，最多100条；周期部署与租户分片未启用。没有真实平台或付费调用。

BE复核事务原子性、确认与租约、去重及恢复边界。应用回退时停止派发并保留消息事实，不自动删除表。
