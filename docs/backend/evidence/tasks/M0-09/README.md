# M0-09 受限认证与密码登录

2026-10-27（Asia/Shanghai）；代码基线 7be7404606351c9916cbc15f36dfaf058ede569c；输入4e9b26a。负责人BE，协作F01_F02。
状态WAITING_REVIEW：采用前置卡技术输入，人工签字不代填；身份/管理HTTP默认关闭，F01接线未完成。

## 交付与验证
真实PG下的受限函数/HTTP集成证据见[运行报告](verification.json)；29项unit/contract、29项真实integration通过。
本轮没有更改前端代码；协议兼容断言与服务端CORS测试不是浏览器联调。

## 三条DoD
1. 真实app_auth在无租户上下文调用精确认证函数；直接读users/sessions/shops、SET ROLE所有者均拒绝。
2. Argon2id正确密码返回API身份，未知邮箱/错误密码同文案；邮箱/IP持久节流跨应用重启仍有效，429含Retry-After。
3. 随机256位bearer只进HttpOnly Cookie，PG仅存摘要；JSON/日志不含密码或bearer，成功/失败事件有受限审计。

## 范围、回滚与人工复核
关闭IDENTITY_API_ENABLED/ADMIN_API_ENABLED可停用新入口；保留兼容表、认证/撤销/审计事实，结构修复走前向迁移。
BE需复核认证角色、秘密边界与故障证据，适用FE需完成F01/F02真实联合验证后具名签字。
