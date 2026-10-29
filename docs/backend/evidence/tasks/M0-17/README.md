# M0-17 审计横切与认证事件

2026-10-27；负责人BE，无FE协作；代码基线 640e473a323d190f6db56fe190ac49683ed2c36c。
状态WAITING_REVIEW，采用M0-08b/02技术输入，不代签前置或本卡。

三条DoD技术通过：父表只SELECT/INSERT及分区可写；实际业务记录actor、requestId、对象和摘要；认证失败无tenant=0业务行，脱敏与跨租户拒绝验证通过。
[verification.json](verification.json)记录33项unit/contract、61项真实integration及静态检查，开发库已升级0008。没有以跳过测试冒充PASS。

本人资料更新锁定真实更新前行，审计与业务同事务提交，昵称只存带独立密钥的HMAC摘要。详情白名单拒绝凭据和订单PII。受限auth_events维持认证边界，按服务器requestId由独立维护侧关联。

审计按UTC月分区，初始化当前月和下月；月前独立运行audit-partitions维护并设置失败告警，生产定时器尚未部署。缺分区会回滚业务，不能吞审计故障继续写。
BE需复核角色/子表ACL、维护操作、数据脱敏与事务原子性；未来业务用例逐项接入。应用回退保留审计事实，禁止自动删表回滚。

## BE具名复核记录（2026-10-29）

- 角色：BE 负责人；复核人：luwenlong
- 复核日期：2026-10-29（Asia/Shanghai）
- 基线提交：505771b40520cbd7ea94a04f59278d183fe00e90
- 确认来源：用户在本会话明确批准“R00 规格卡（4 张）可以过，M0 底座卡（22 张）也可以过”
- 结论：README 记载的三条 DoD 与 verification/static-check 证据核对一致，BE 复核通过
- 批准范围：仅本卡 BE 复核签字；不含 FE 侧 F00/F01/F02 签字、G0 门禁放行、A01-A12 全部结论及真实平台写/付费许可
