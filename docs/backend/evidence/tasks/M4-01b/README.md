# M4-01b 分析计算与真实阶段

2026-10-28，BE/F08，IN_PROGRESS（仅本期计算/阶段切片技术通过）。直接依赖M4-02已本地技术验证；真实E01/E02与G0/E08仍保留。

计划/边界：analysis/result_models.py冻结最小API结果模型、calculator.py精确聚合、repository.py阶段与租约；0055_analysis_execution下接0054_content_uploads，新增受控领取/阶段/结果原子函数，不给app_login任意UPDATE或结果INSERT。worker/tasks.py仅accept_analysis局部装配。M4-03正式叙述/引用不计本卡完成。

指标仅所选维度，广告与经营不合并GMV；Decimal分子分母重算ROI/CTR/CPC/客单价/退款率，同单位/归因/退款口径才可聚合。缺归因订单CPA为空，无成本不制造利润。比较期读取/冻结/计算尚未实现（NOT_IMPLEMENTED），当前previous/yearAgo恒null、比较数组为空；不能将未查询冒充缺覆盖。账户级来源不伪造商品/计划排行榜或AI洞察。

阶段10领取、40完整覆盖复检与同一REPEATABLE READ快照冻结、75实际计算完成、90结果模型/JSON校验完成、100不可变结果与成功状态同事务。独立阶段提交，120秒租约fence，终态幂等，旧fence拒绝，过期原任务恢复。实际投递后数据库错误Reject重排原任务；覆盖变化固定failed原因，计算失败固定安全原因。

验证/缺口：实际PG/Redis/RabbitMQ与HTTP验证通过，保留人工复核。平台/付费0；人工复核PENDING。

## 实际验证

WSL Ubuntu，UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv。

- uv run --frozen python scripts/test_infrastructure.py --group analysis --details：最终23通过，40.33秒。
- uv run --frozen pytest tests/unit/test_analysis_calculator.py -q：3通过，0.35秒。
- uv run --frozen ruff check src/yuliang/modules/analysis src/yuliang/worker/tasks.py tests/integration/test_analysis_admission.py tests/unit/test_analysis_calculator.py：通过。
- uv run --frozen mypy src/yuliang/modules/analysis：8源码通过。

实际覆盖：生产AdqReportSync生成的非零日报含roi事实，冻结仅取必要原生指标，7天消耗700/GMV2100重算ROI3；非等权单元样本重算1.9而非日均。零成本ROI/缺归因订单CPA为空；结果仅所选维度且比较期实现缺口明确partial。真实RabbitMQ/prefork Worker首次写唯一冻结结果、重复投递不重算；任务和结果运行身份直接改写拒绝。两并发claim只有一owner，旧fence拒绝，40阶段对外已可见；模拟worker中断后撤销当前合同/修改新事实仍以已冻结snapshot恢复同任务，未冻结前必须当前覆盖就绪。计算异常转固定安全failed，不泄露载荷；数据库错误由实际Worker Reject回原任务。

阶段恢复语义：事实冻结前重新核验当前规范来源；冻结后保留原版本/来源ID/页/字段数值，后续合同过期/新修订不改历史输入，当前用户/租户/任务读取范围仍必须通过。SQL coalesce禁止恢复覆盖snapshot。比较期计算尚未实现，未查询前期或去年期，暂不计算环比/同比，不制造账户级之外排名。M4-03正式证据叙述下一任务另交，当前insights空数组、规则阈值固定前端默认值。

回滚采用前向停用计算派发，不删除已有结果/快照；0055不支持破坏性downgrade。BE/F08复核PENDING。未调用真实平台或模型付费。

范围复核：本卡交付物含对比，整卡保持IN_PROGRESS。下一优先补M4-01b比较期读取、冻结和确定性计算；有完整同范围/同口径来源则计算，实际查询缺覆盖才null并附原因。当前规则引用只对应本期。0055未claim/NULL fence拒绝边界及0057草稿身份字段NULL拒绝将在最终all后补直接app角色负例，当前冻结代码未改。


## 最终必要修复与范围准确性

0055增加NOT FOUND / NULL p_token / NULL或过期lease拒绝，直接app_login（非Pydantic）对未claim任务请求失败/后续阶段均P0409，claim后NULL token亦拒绝。本期结果coverage与limitation明确“比较期计算尚未实现”；本卡仍IN_PROGRESS、comparison NOT_IMPLEMENTED，不把null假称已查询缺覆盖。

真实前端数学联动修复：派生指标携带numerator/denominator，日期points包含可相加的原生基数，缺归因订单保持ads.orders=null。NET_OF_REFUND经营金额不会因前端pointValue重算而伪造标准毛额比率；不具备毛额依据时使用显式不可用基数。派生minor成本保持六位除法精度，原生金额仍精确整数。

最终实际命令：YULIANG_ANALYSIS_RESULT_CAPTURE=/mnt/d/codex/yizhi-yuliang/docs/backend/evidence/tasks/M4-01b/frontend-result.json uv run --frozen python scripts/test_infrastructure.py --group analysis --details：26通过，48.85秒。fixture通过实际AdqReportSync/PG产生7天非等权事实（100/1000、900/900，其余零），执行冻结结果，原生平台ratio不参与。node node_modules/tsx/dist/cli.mjs --tsconfig frontend/tsconfig.json backend/scripts/check_analysis_result_frontend.mts docs/backend/evidence/tasks/M4-01b/frontend-result.json：实际现有validateAnalysisResult/groupAnalysisPoints/pointValue通过；周ROI=1.9而非日ROI之和11，CTR=0.25，CPC=28.571428571428573，缺CPA=null。未修改前端源码。

计算与规则7单元复验通过0.62秒；最终全局Ruff/mypy187源码通过。此后源码冻结；比较期完整实现优先续接，不越过本卡对比缺口将其计DONE。
