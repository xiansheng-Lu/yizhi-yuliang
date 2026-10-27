# M2-03 指令解析与对象消歧

2026-10-27（Asia/Shanghai）；BE负责，FE协作F06；状态WAITING_REVIEW。基线877e9a2af83aac0ea604e175516a8b33a9e5197a；使用M2-02、M2-01b技术输入，以及本轮用户直接确认的28条预期，不代填BE/FE最终签字。

交付：七动作规则解析、模型后置守卫、两个冻结HTTP入口、会话幂等接线及0014私有追加留痕。实际验证、适用范围与源码摘要见[verification.json](verification.json)。无平台写入、无申请/审批/执行创建；本卡离线与集成验证不产生模型费用，真实模型回归归M2-04单独留证。

三条DoD后端技术通过：

1. 七动作输出符合前端CommandInterpretation，金额整数分、ROI正比值、暂停/恢复布尔值，直播仅人工交接；QUERY显式场次或null，未知对象不替换成唯一可见场次。
2. 混合/多目标/非法单位或精度/相对金额/越权/注入/否定/条件语句均保守澄清；同名歧义保留全部，模型不能任意挑一个。解析当前语法有意保守，不声称支持任意自然语言。
3. 解析不写command_jobs，规则路径不产生ai_invocations；结果与输入摘要/范围/规则和提示词版本同事务留痕。模型候选还须携带已报价冻结请求并复核本人/成员/请求/场景/状态与报价指纹；不可用通道只能留MODEL_UNAVAILABLE拒绝。

当前真实目录边界：数据库尚无广告计划同步读模型，默认AD_PLAN为空；集成中的计划为服务端端口显式注入的合成夹具，不作为真实计划或平台验收。现有live_sessions可按task.create+店铺/场次权限读取，但没有连接器归属列，因此仅connector_id=all提供场次候选，其他范围不能假称绑定。后续M1同步和能力数据就绪后接正式目录。

输入/预期/实际：28条用户确认样例逐项通过；新增否定/条件/重复动作/隐含多目标/金额边界/同名歧义/未知查询对象；真实PG验证两个HTTP入口、CSRF/来源/撤权、跨租户/同租户私有RLS、不可删除留痕、并发消息幂等与零任务/平台副作用。消息同键异载荷409，原响应回放不新增解析。模型原始正文和密钥不入库，trace只保存输入摘要与安全解释。

复现：在backend目录使用WSL既有uv环境，运行ruff check/format --check、mypy、pytest tests/unit tests/contract -q；uv run python scripts/test_infrastructure.py --group parser --details。首次升级可uv run python scripts/migrate_local.py upgrade/current；集成使用UUID临时数据库，结束删除本轮测试库并恢复app_migrate NOLOGIN。

L3真实前端浏览器/F06与人工复核PENDING；L4平台写不适用本解析卡。应用回退保留0014、关闭身份HTTP或回退应用即可停止入口，不删除留痕。G0及原有BE/FE复核保持未放行。下一卡M2-04已获得本轮DeepSeek验证许可，可直接实施固定安全集真实回归；真实广告账户操作仍需逐账户/动作/额度授权。
