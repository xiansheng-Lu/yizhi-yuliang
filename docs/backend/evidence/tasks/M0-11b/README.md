# M0-11b CSRF引导与写请求防护

2026-10-27（Asia/Shanghai）；代码基线 7be7404606351c9916cbc15f36dfaf058ede569c；输入4e9b26a。负责人BE，协作F01。
状态WAITING_REVIEW：采用前置卡技术输入，人工签字不代填；身份/管理HTTP默认关闭，F01接线未完成。

## 交付与验证
真实PG下的受限函数/HTTP集成证据见[运行报告](../M0-09/verification.json)；29项unit/contract、29项真实integration通过。
本轮没有更改前端代码；协议兼容断言与服务端CORS测试不是浏览器联调。

## 三条DoD
1. 真实预认证挑战和会话轮换可完成登录/写入；缺失、伪造CSRF均403。
2. 登录消费预认证挑战，旧令牌无效；重新获取会话CSRF轮换旧值，精确CORS预检放行、恶意站点拒绝。当前没有未冻结的切租户HTTP路由，不宣称已完成切租户页面。
3. 后端分域方案使用host-only HttpOnly Cookie与JSON令牌，不要求JS读Cookie。F01真实浏览器联合接线NOT_RUN，保持待联调。

## 范围、回滚与人工复核
关闭IDENTITY_API_ENABLED/ADMIN_API_ENABLED可停用新入口；保留兼容表、认证/撤销/审计事实，结构修复走前向迁移。
BE需复核认证角色、秘密边界与故障证据，适用FE需完成F01/F02真实联合验证后具名签字。
