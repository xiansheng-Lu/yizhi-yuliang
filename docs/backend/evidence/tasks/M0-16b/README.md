# M0-16b Inbox未完成任务恢复

2026-10-27；负责人BE，无FE协作；代码基线 75cb1ef170ce328b3d259cb3775f7aa17362e109。
状态WAITING_REVIEW；采用M0-16a技术输入，不代签前置卡或本卡。

先独立持久RECEIVED，Inbox行锁内把内部DB副作用与PROCESSED原子提交。仅PROCESSED重复跳过；未完成恢复持久写入唯一scheduled_tasks的DUE记录后才ACK。双消费者及进程退出验证内部效果最多一次。

三条DoD的技术断言由test_durable_messaging.py与messaging_fault_process.py覆盖，实际命令与七阶段进程记录见[verification.json](verification.json)。30项unit/contract、46项真实integration通过；ruff、格式和完整mypy通过，开发库已升级0007。

范围只含内部数据库恢复协议；DUE记录不是外部业务执行成功。平台/资金/AI未知结果保持guard关闭，不套用自动重放。dispatch_pending须由服务端提供已验证租户，最多100条；周期部署与租户分片未启用。没有真实平台或付费调用。

BE复核事务原子性、确认与租约、去重及恢复边界。应用回退时停止派发并保留消息事实，不自动删除表。

## BE具名复核记录（2026-10-29）

- 角色：BE 负责人；复核人：luwenlong
- 复核日期：2026-10-29（Asia/Shanghai）
- 基线提交：505771b40520cbd7ea94a04f59278d183fe00e90
- 确认来源：用户在本会话明确批准“R00 规格卡（4 张）可以过，M0 底座卡（22 张）也可以过”
- 结论：README 记载的三条 DoD 与 verification/static-check 证据核对一致，BE 复核通过
- 批准范围：仅本卡 BE 复核签字；不含 FE 侧 F00/F01/F02 签字、G0 门禁放行、A01-A12 全部结论及真实平台写/付费许可
