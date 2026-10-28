# M4-02 日/周/月周期与维度覆盖守卫

2026-10-28；BE、F08；WAITING_REVIEW（技术验证）。按用户持续多agent研发授权，采用M4-01a/M1-10已验证实际技术输入及M1-12规范CompassContract→FactWriter来源；M1-13未知单位/时间/退款观察不作为覆盖事实。

最小计划/边界：analysis/coverage.py周期、reporting/coverage.py实际事实覆盖、AnalysisService.create受理同事务预检、worker/tasks.py仅accept_analysis局部复检；关联单元/集成测试；不修改冻结AnalysisQuery，不把Worker投递记为分析计算完成。新增0053_analysis_coverage受控失败函数，不允许运行账号任意写任务状态。

周期只允许北京时间日1天、任意连续7天、完整自然月。每日期/当前可见账户/所选维度检查规范非缺失事实、实际成功同步run和确认页证据；零值保留，未知口径、复数口径、跨日单位/归因/退款口径差异及缺维度统一422。ADQ广告要求消耗/GMV/曝光/点击；店铺维度仅接受wxshop规范经营来源，ADQ归因GMV不可替代；小店经营要求GMV/订单/退款三项。直播/视频无可靠规范来源继续422，不凭页面数据或余额补齐。

受理先持久幂等SQL核对原权限与请求一致性，原键任务可恢复；新任务才覆盖检查，失败同事务回滚任务与Outbox。实际Worker accept_analysis在analysis_receive之前调用同一守卫；Worker缺口通过0053受控函数同事务持久为failed和固定安全说明并登记delivery，不变成功或100%，后续计算仍归M4-01b。

既有受理集成fixture改通过真实SyncStore/FactWriter预置实际AdqReportSync/有效合同生成七天零值事实，保留原角色/幂等/历史/队列断言；新增缺一天422、原键在数据变化后恢复、非法周期、缺维度、Worker复检用例。6项周期单元已通过，实际集成待DB窗口。BE/F08复核与G0/E08保留。回滚通过回退本卡服务调用，无数据迁移破坏。

最终生产日报来源验证：uv run --frozen python scripts/test_infrastructure.py --group analysis --details，17通过，32.68秒。覆核合同ID/binding/日期/有效期/归因窗口与time_line组合、完整页数及成功run；不能仅凭通用成功页认覆盖。新增ADQ-only请求shop422、合同撤销、PARTIAL和跨日口径拒绝、Worker持久failed可解释回归。6周期单元通过0.23秒，Ruff/mypy相关7源码通过；真实平台/付费0。早期用例删除事实被历史FK拒绝，改为模拟来源口径失效，未削弱数据约束；替换生产fixture时重复登录nonce被防护拒绝，移除重复登录后通过。
