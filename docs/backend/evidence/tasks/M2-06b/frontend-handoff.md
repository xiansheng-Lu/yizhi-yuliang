# F06 V8兼容投影接线缺项

2026-10-28，基线bd17bc5实际检查`src/services/delivery-service.ts`中legacyExecution：目前仅在FAILED分支检查verification=UNKNOWN/TIMEOUT，EXECUTING和VERIFYING直接映射同名V8态，因而漏掉提交未知与已受理回读未知。本卡未修改该前端文件，页面联调未执行。

F06接线需采用后端共用查询输出，或让旧兼容入口遵循相同断言：

| 实际事实 | V1 | V8 | 回执与重发 |
|---|---|---|---|
| 提交未知、无响应 | EXECUTING，RESULT_UNKNOWN | UNKNOWN | receipt=null，禁止重发 |
| 已受理、回读未知 | VERIFYING，accepted=true | UNKNOWN | PLATFORM+UNKNOWN，禁止重发 |
| 平台明确拒绝 | FAILED，accepted=false | FAILED | 不因verification=UNKNOWN显示成未定性 |
| 批准但未执行 | APPROVED | PENDING | 原计划值保持，不能提前生效 |
| 平台独立匹配 | SUCCEEDED | EFFECTIVE | PLATFORM+accepted+MATCHED |
| 人工开关播交接 | MANUAL_REQUIRED | 不建广告执行 | 无广告账户/平台回执，不显示线下已完成 |

接线时保留最后核对时间、可解释时间线与核对入口；最后恢复核对时间不能被标为平台verified_at。未知不纳入成功/失败计数，safeRetry=false，旧键/新键都不能作为重发许可。明确拒绝与未发送E1的终态应保留真实定性。

核对任务的before/after仅为请求提案，计划镜像及列表现值继续取独立同步/核验来源。后端confirmed_change只表示该次动作已确认，不能当成当前平台真值。V1新增状态或SYSTEM公共回执均不允许。

以上是待F06执行的交接，不是FE具名批准或页面验收记录；后端脱敏输入见trace.json。
