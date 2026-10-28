# M2-15 批准事件Worker派发技术切片

状态：初始恢复handoff与派发内核TECH_VERIFIED，13项真实PG首轮全部通过14.00秒；此前10项保留为历史基线。真实Rabbit/loopback专项由独立Agent继续；不代表生产WRITE、E03或整卡验收完成。

本切片仅接受0076真实批准决定生成的 `yuliang.system.approved_execution`。先核不可变origin与完整5ID，再以原job创建者身份复检权限、批准版本/有效期及现有执行guard/策略。`consume`回调只做DB事务与`ExecutionOrders.prepare(unit_of_work=session)`；SENT及inbox提交后，才调用既有Recorded/Governed client与原before_verify/after_admission hooks。出站参数和一次性secret仅在首次内存ticket，不进入Celery结果。

默认DispatchRuntime无注册器。无已核验执行器时持久 `BLOCKED_EXECUTOR`、0attempt/HTTP，原事件之后不会因为配置变化自动发送。真实origin失活、权限或版本失效只持久 `BLOCKED_AUTH`；窄SQL入口只确认原actor/来源，保留实际attempt关联，不赋发送权、不改job十二态、不释放预算。坏broker或无真实origin抛 `DispatchMessageRejected`，由Worker拒绝且不重投；数据库/未提交事实异常交Worker重投原事件。

重投不能重建ticket或第二次WRITE。既有ADMITTED/SENT只检查既有0036恢复任务并记录RECOVERY_REQUIRED；inbox已PROCESSED仍继续该检查。回执只来自真实持久raw，`RECEIPT_RECORDED`不等于效果验证/最终业务成功。

独立pipeline复核发现仅查询恢复事实不足以真正派发初始恢复事件，现新增 `publish_recovery(item,sender)`，由Worker在handle返回后、ACK前对所有分支调用。它只定位该批准event所关联实际attempt的当前恢复事件，在事务外复用publish_schedule；确认失败或仍PENDING抛RecoveryHandoffPending，原批准task重投且不再WRITE。终态/已发布幂等，默认关闭无attempt不发布；主体/权限失效只允许原来源窄Recovery.blocked记SYSTEM/E0，不修改job或资金。该修复13项实际PG首轮通过14.00秒；发布确认失败仍PENDING、同event重投无新WRITE、RECEIPT_RECORDED初始投递、默认空不发布、inactive窄Recovery.blocked均有实际断言。真实MQ及Inspector恢复结论仍待独立增强pipeline，不能仅凭PG sender或RECOVERY_REQUIRED宣称真实恢复已完成。

## 文件与接线

- `execution/dispatch_service.py`：ApprovedExecutionDispatch(engine,audit_key,runtime=None).handle(WorkMessage)，status(WorkMessage)。
- `execution/dispatch_runtime.py`：默认空registry，代码显式register(action,provider,product_line,factory)。factory只在服务器内接收Session/真实原identity/ApprovedEvent；无环境URL或broker工厂入口。
- 0077_approved_dispatch_worker：接0076_approval_execution_events，准入状态与真实event/attempt/receipt/recovery关联，应用无直接INSERT/UPDATE。
- Worker路由、默认runtime工厂和基础设施runner由主Agent统一接线。

Celery返回固定白名单 `event_id,job_id,state,attempt_id,reason`，恢复时只附 `recovery_id,attempt_id,state,next_event_id,next_due`；自由文本outcome完全排除，不含spec、parameters、secret、凭据、raw或正文。

## 当前验证

- 3项单元最终通过，0.79秒；Ruff通过，2源码mypy通过。
- 自有PG入口 `test_approved_execution_dispatch.py`，10项最终通过11.56秒；真实批准事件、默认closed、重复不重发、伪造5ID、失活/撤权/过期、SENT后ticket丢失；维护写入敏感样式旧outcome，实际断言不进入返回。
- 恢复handoff修复后完整13项PG首轮通过14.00秒，无源/SQL/fixture修复；Ruff、2源码mypy及3U0.79秒通过。
- 空库迁移head `0077_approved_dispatch_worker`，当前DB窗口已明确释放。
- 独立Agent拥有Rabbit+loopback测试，未运行；测试仅固定synthetic DTO，不能当生产serializer或正式协议。
- 真实平台/付费：0。

静态审查已修0077 aggregate_type与0076真实producer的 `command_job` 对齐。首轮PG8通过/2失败13.11秒，均为测试fixture：平台请求总数含前置READ，改仅断言WRITE；过期时间须保持expires_at>created_at，改真实已过期created_at+1微秒。第二轮10通过12.16秒。主Agent复核自由outcome安全边界后补白名单及实际PG负例，最终10通过11.56秒。0076/0077 SQL无PG阶段修复。MQ和并发故障结论由独立专项实际结果补录。
