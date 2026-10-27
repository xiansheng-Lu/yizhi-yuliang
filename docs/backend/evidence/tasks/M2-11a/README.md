# M2-11a 完整目标键与fence领取

负责人BE，无前端协作。WAITING_REVIEW，基线7046673。M2-06a实际技术底座；持续研发授权，G0/E08保留。

三条DoD：两账户同native不串锁，同一真实目标只一领取者；领取/取消共享状态/版本谓词，旧fence不能回写；PG守卫不依赖Redis，QUARANTINED拒绝新写。

## 实现与三条DoD

1. 0030增加target_execution_guards与只追加target_guard_claims。唯一键tenant/account/provider/product/AD_PLAN/native，来源只取该内部申请关联campaign，不允许外部替换账户/产品/native。guard从IDLE按期望fence条件领取，fence+1、记录持有者/数据库租约时间与原job/assessment/request，运行身份不能直接更新守卫或删除领取记录。两账户相同native分别一行，同一目标只可有一条LEASED/QUARANTINED当前守卫。未实现的直播自动控制不进入此领取口。
2. TargetGuards.claim为同Session原语，不自行commit；按已有策略/租户预算锁顺序取得本次EXECUTE实际检查，使用原状态归约生成CLAIM。受控SQL再锁job、核对APPROVED/原version/未领取未发送/有效期，锁完整目标守卫，核对当前策略与精确同任务/用户/版本/时点10秒有效、非空全通过risk，p_document.risk_summary须等于该实际assessment。然后调用既有job_apply_transition共享条件更新，状态/claimed_at/version/event与守卫/领取记录/审计同事务；任何失败一起回滚。取消和领取通过同一状态/版本与未领取谓词竞争，不能两方成功。assert_current再次按当前精确任务范围取得PG共享行锁，比较job/guard/owner/fence/LEASED/期限；在该事务中旧持有者不能用旧fence推进后续写回。实际attempt/结果写入模块须强制接此原语，不宣称尚未实现的结果模块已经接线。
3. describe只在精确任务权限后提供该目标fence/状态/租约，不返回其他申请人的原文或任务信息。守卫/资格读取不使用Redis锁；QUARANTINED立即TARGET_QUARANTINED，已有LEASED即使到期仍LOCKED，过期本身不转IDLE、不授予再次发送权。租约恢复/已发送判定归M2-11b，不能按时钟自动重发。旧fence/owner/期限在assert_current返回版本冲突。

## 实际验证与未覆盖项

9项真实PG集成验证：实际风险/HELD前置后原子领取、同目标双Worker唯一胜出、两个已核对账户（各有独立READ/WRITE绑定、全预算源/确认页与维护账户发现关联）使用同native=456得到不同guard；旧fence/owner拒绝；QUARANTINED拒绝并保持原APPROVED；缓存读取故障不影响PG；领取审计故障回滚；运行身份不能改守卫，过期仍LEASED；取消/领取竞争只一方成功且claimed_at与最终状态一致。trace.json为本地lease/job/fence引用与明确阶段标签，平台/付费均0。取消竞争只验状态内核，未结算R保守保留，不能称完整取消/资金释放已接线。

上游与维护策略/能力证据仍为显式合成技术输入，前置审批阶段通过维护身份布置，未构造真实人工签字或审批记录；资金原语和数据库事实真实执行。主公开生命周期/执行Worker仍未安装，无真实发送，M2-13发送前attempt与M2-14审批记录将按依赖补齐。BE复核与G0/E08保留；本卡无前端协作，不宣称页面验收。

执行命令、全量数量及开发库迁移见verification.json。回退停用领取调用并保留guard/claim/job/event/HELD；不能清守卫或改fence使旧Worker获得新写资格。下一卡M2-12接口幂等，再接M2-13a/b任务→执行→attempt→原请求/回执映射，之后回到M2-10c/d资金结算与未执行释放。
