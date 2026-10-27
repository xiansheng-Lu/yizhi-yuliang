# M0-12 身份资料与合法空快照

2026-10-27（Asia/Shanghai）；代码基线 7be7404606351c9916cbc15f36dfaf058ede569c；输入4e9b26a。负责人BE，协作F02。
状态WAITING_REVIEW：采用前置卡技术输入，人工签字不代填；身份/管理HTTP默认关闭，F01接线未完成。

## 交付与验证
真实PG下的受限函数/HTTP集成证据见[运行报告](../M0-09/verification.json)；29项unit/contract、29项真实integration通过。
本轮没有更改前端代码；协议兼容断言与服务端CORS测试不是浏览器联调。

## 三条DoD
1. API身份中的user/tenant/shop均字符串ID，页面按有效权限推导；默认平台管理员没有业务页面/快照权。
2. 无平台镜像时返回初始revision=1与当前租户目录，十二集合都是数组，scope日期为上海时区，其余平台集合为空而非Mock数据。
3. 本人昵称更新经过有效会话/CSRF/数据库守卫，长度/空白/伪造userId/tenantId/permissions均拒绝，不能更新他人身份。

## 范围、回滚与人工复核
关闭IDENTITY_API_ENABLED/ADMIN_API_ENABLED可停用新入口；保留兼容表、认证/撤销/审计事实，结构修复走前向迁移。
BE需复核认证角色、秘密边界与故障证据，适用FE需完成F01/F02真实联合验证后具名签字。
