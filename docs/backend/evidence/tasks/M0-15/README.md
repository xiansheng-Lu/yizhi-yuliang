# M0-15 Worker与调度骨架

2026-10-27（Asia/Shanghai）；代码基线 780c3b82b1672d2677dea6da42521022bd2e789d；输入88bc6c4；负责人BE，无FE协作。
状态WAITING_REVIEW，采用M0-01/04/07技术输入，不代签前置卡与本卡。

## 交付和三条DoD

1. 四个命名空间队列execute/platform/ai-media/system使用JSON和持久交付；消息模型只允许event/job/member/user ID及正租户。
2. Worker按实际PID创建连接池，逐任务建立事务上下文和复核成员；真实同一子进程先A后B正确，伪造身份拒绝后上下文仍清理。
3. acks_late、无自动retry、副作用worker-loss不自动重投；只读探针可重投。测试锁住真实PG成员表，确认活动子进程PID后SIGKILL，再由新PID收到同一消息redelivered=true，retries=0。

真实Worker/Redis租约、命令与结果见[verification.json](verification.json)：29项unit/contract、33项真实integration通过，ruff/format/mypy通过。
Beat租约的比较/释放/接任在真实Redis验证；本轮无周期业务任务，不把租约等同恰好一次业务执行。
三个副作用guard只验证路由与拒绝边界，不调用真实平台或供应商；后续重投/幂等/恢复由M0-16及M2/M3交付。
测试Worker、临时库、队列、交换机、结果键和租约已清理，没有删除其他项目资源。

## 复核和回滚

BE复核队列、消息身份、进程池及自动重试边界。无前端协作，不代签BE。
可停止本项目Worker/Beat并回退应用，保留数据库事实；没有新增业务迁移。
下一卡M0-16a技术输入已就绪，立即继续Outbox；邮件资源阻塞仅影响M0-10。

## BE具名复核记录（2026-10-29）

- 角色：BE 负责人；复核人：luwenlong
- 复核日期：2026-10-29（Asia/Shanghai）
- 基线提交：505771b40520cbd7ea94a04f59278d183fe00e90
- 确认来源：用户在本会话明确批准“R00 规格卡（4 张）可以过，M0 底座卡（22 张）也可以过”
- 结论：README 记载的三条 DoD 与 verification/static-check 证据核对一致，BE 复核通过
- 批准范围：仅本卡 BE 复核签字；不含 FE 侧 F00/F01/F02 签字、G0 门禁放行、A01-A12 全部结论及真实平台写/付费许可
