# M0-14 独立最小管理入口

2026-10-27（Asia/Shanghai）；代码基线 7be7404606351c9916cbc15f36dfaf058ede569c；输入4e9b26a。负责人BE，协作NONE。
状态WAITING_REVIEW：采用前置卡技术输入，人工签字不代填；身份/管理HTTP默认关闭，F01接线未完成。

## 交付与验证
真实PG下的受限函数/HTTP集成证据见[运行报告](../M0-09/verification.json)；29项unit/contract、29项真实integration通过。
本轮没有更改前端代码；协议兼容断言与服务端CORS测试不是浏览器联调。

## 三条DoD
1. 默认关闭的/internal/admin/directory独立验证实际来源IP、有效内部成员与PLATFORM_ADMIN角色+admin.access；OPERATOR和OWNER均403。
2. 管理只投影最多100条用户/租户字段，不提供资金/平台业务写入或通用表查询；成为平台管理员后业务快照仍403。
3. 每次合法管理读取追加审计，输出不含哈希/令牌；默认回环网段，外部地址即使伪造X-Forwarded-For也不通过。部署关闭不受信代理头解析。

## 范围、回滚与人工复核
关闭IDENTITY_API_ENABLED/ADMIN_API_ENABLED可停用新入口；保留兼容表、认证/撤销/审计事实，结构修复走前向迁移。
BE需复核认证角色、秘密边界与故障证据，适用FE需完成F01/F02真实联合验证后具名签字。

## BE具名复核记录（2026-10-29）

- 角色：BE 负责人；复核人：luwenlong
- 复核日期：2026-10-29（Asia/Shanghai）
- 基线提交：505771b40520cbd7ea94a04f59278d183fe00e90
- 确认来源：用户在本会话明确批准“R00 规格卡（4 张）可以过，M0 底座卡（22 张）也可以过”
- 结论：README 记载的三条 DoD 与 verification/static-check 证据核对一致，BE 复核通过
- 批准范围：仅本卡 BE 复核签字；不含 FE 侧 F00/F01/F02 签字、G0 门禁放行、A01-A12 全部结论及真实平台写/付费许可
