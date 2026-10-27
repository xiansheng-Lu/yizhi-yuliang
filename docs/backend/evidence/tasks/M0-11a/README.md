# M0-11a 会话读取与续期

2026-10-27（Asia/Shanghai）；代码基线 7be7404606351c9916cbc15f36dfaf058ede569c；输入4e9b26a。负责人BE，协作F02。
状态WAITING_REVIEW：采用前置卡技术输入，人工签字不代填；身份/管理HTTP默认关闭，F01接线未完成。

## 交付与验证
真实PG下的受限函数/HTTP集成证据见[运行报告](../M0-09/verification.json)；29项unit/contract、29项真实integration通过。
本轮没有更改前端代码；协议兼容断言与服务端CORS测试不是浏览器联调。

## 三条DoD
1. GET session滑动续期最多2小时且不越过绝对7天上限，真实SQL断言expires_at<=absolute_expires_at。
2. 过期/伪造/未知Cookie按契约返回200+null，不成为业务身份；受保护接口返回401。
3. 请求始终查PG，Redis残留stale-active值不覆盖成员移除或PG撤销；不使用缓存身份作为权限来源。

## 范围、回滚与人工复核
关闭IDENTITY_API_ENABLED/ADMIN_API_ENABLED可停用新入口；保留兼容表、认证/撤销/审计事实，结构修复走前向迁移。
BE需复核认证角色、秘密边界与故障证据，适用FE需完成F01/F02真实联合验证后具名签字。
