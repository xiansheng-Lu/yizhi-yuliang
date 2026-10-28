# M4-03 冻结分析结果与真实证据叙述

2026-10-28，BE/F08，WAITING_REVIEW（本地技术验证）；直接依赖M4-01b本地23项实际设施通过、M2-01b既有网关技术输入。规则模式不依赖付费模型，不调用模型，不将结果标为AI。使用冻结contract要求insightSource小写rules。

范围/计划：新增analysis/narrative.py规则证据验证，repository.py在确定性计算后绑定真实snapshot引用再冻结；新增关联单元与集成测试。不改既有结果写权限，无额外迁移。M4-01b现有受控函数与immutable trigger保证每task唯一结果及不可修改。

每条结论须有ready metric、真实同产品线/日期/授权账户snapshot，并能从分子分母复算出相同数值。只引用真实fact ID/修订号/run/page，无事实、无分母、跨账户范围或数值不一致时不伪造叙述。未提供商品/场次行就不生成排行机会或因果解释；没有成本不输出利润。引用历史冻结版本，后来合同过期或新事实不改历史内容；当前用户失去范围后结果读取仍拒绝。

规则阈值沿冻结前端默认值保存，无模型费用或真实平台调用。规则及计算语义共7项单元通过，实际PG/HTTP/Worker24项通过。人工BE/F08签字与E08仍保留。

## 实际验证

WSL环境UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv：uv run --frozen python scripts/test_infrastructure.py --group analysis --details，24通过，40.34秒；uv run --frozen pytest tests/unit/test_analysis_calculator.py tests/unit/test_analysis_narrative.py -q，7通过，0.57秒。Ruff与9分析源码mypy通过。

覆盖：每条insight引用本task真实fact@revision/run/page；数值变化与证据不符拒绝；无授权账户/无来源引用不产结论。完成结果重复读取JSON一致，后来原合同撤销和新数值999不改冻结结果，当前数据范围移除后按结果ID读取NO_DATA。实际Worker首次计算、重复不算、原租约未到期不提前ACK丢恢复机会，保持原task重排。初轮1个失败源于测试批量替换误把succeeded重放设为期待LeaseBusy，修复精确断言后24全通过，未改变已成功重放返回false的正确行为。

未调用AI，insightSource=rules；未新增任何任意结果写权限，不新增0057迁移。下一READY卡M4-05报告草稿/版本/安全预览；M4-04仍待M1-19完整输入，不能绕过。
