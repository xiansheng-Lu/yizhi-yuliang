# M2-11b 工程设计

设计按实际实现与技术验证归档，人工最终复核仍PENDING。基线a5c7b28，2026-10-28本地持续授权范围。

- 首attempt=SENT事务内登记私有恢复任务、只含真实COMMAND_JOB必要ID的Outbox/调度行。当前其他业务生产者仍关闭，不能伪造job。原命令发送与恢复都不在PG事务内调用平台HTTP。
- 只读恢复任务使用第四条system队列，持久Inbox按event去重。注册新事件类型并给Outbox加not_before；默认既有消息立即可派发，恢复计时不能被普通dispatcher提前发成热循环。
- 恢复到期先持锁将目标QUARANTINED，撤销LEASED发送资格后再查原始ATTEMPT。保留当前claim的fence/owner，以便同一尚未被新claim取代的迟到响应仍可归档/定性；新写在隔离期间始终拒绝，新claim才递增fence。无需覆盖旧attempt的不可变claim关联。
- 无Root或有Root但无ATTEMPT，且旧写资格已撤销，才能形成持久SYSTEM/E1未发送证明。不得抹掉claimed_at/dispatch_started_at伪装成未领取。扩展内部CONFIRMED_NOT_EXECUTED无平台回执分支、资金终结proof_kind=RECOVERY_NOT_SENT及对应严格证明服务；同事务FAILED/RELEASED/IDLE，预算增额仍retryable=false，不自动重试原写。
- 有ATTEMPT则可能已物理发送，不能仅凭超时/租约过期/FAILED释放。已有可信受理/拒绝先消费原始证据，拒绝走现有E1终结；其余隔离，保留HELD并登记下次只读恢复。独立平台回读/查询的真正定性归M2-17/18，不能造结果。
- 已有NoSent终结时，Frequency读模型应排除与该原Root精确关联的真实恢复E1，不能靠补造平台NOT_SENT事件消除在途计数。
- 恢复协调租约与原目标写租约分离；协调者不获得新的dispatch_secret。重复/领取失败/Worker退出后，恢复账与待发事件仍可查询，不能成功ACK后无后继事实。
- 系统任务不做外部平台副作用，可在DB/内部队列基础设施故障时重投；严格校验存储Outbox payload，不接受客户端任意命令。身份撤销只保守记BLOCKED_AUTH/待处理，不能借系统任务绕过原作用域放开写或释放资金。
- 已提供按当前可信身份的bounded due dispatcher，以及在PG提交之后发布恢复计时消息的helper（允许ETA，MQ确认在PG事务外）。运行自动派发装配随M2-15推进；本卡必须实际启动现有RabbitMQ/Redis/PG的prefork Worker验证，不能只以helper存在冒充运行。
- 同事务入口按policy共享锁→预算租户锁→申请/执行/恢复/守卫锁。后续生命周期参与者必须在状态更新前取得预算锁，避免先锁job后锁tenant与执行路径相反。
- 当前全量基线：520单元/契约、548集成PASS，1项生命周期重启跳过。开发库head0035，所有业务/资金/恢复生产行0；原06/12两份各2行未提交改动继续排除。
