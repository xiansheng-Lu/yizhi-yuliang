# M0-13 组织权限与角色种子技术交付与验收证据

日期2026-10-27（Asia/Shanghai）；代码基线 8f16d40a41f1cda5db9af5c5e73821a5c5cccb5f；输入378154d。负责人BE，前端协作F02。
状态WAITING_REVIEW。用户明确持续推进，采用前置卡已验证的技术输入；不替代前置卡及本卡人工签字。

## 范围、输入、交付
范围以20册对应叶卡为准：独立迁移/角色引导、身份与最小目录、事务租户上下文、逐表策略、范围校验与权限种子；不修改前端源码。
共同运行证据见[../M0-06/verification.json](../M0-06/verification.json)；迁移与回滚说明见[alembic README](../../../../../backend/alembic/README.md)。
本轮27项unit/contract、19项真实integration、ruff/format/mypy通过；命令在WSL backend目录执行，Linux虚拟环境与Windows独立。

## 三条DoD
1. 五角色模板和权限/页面映射可核对；按租户重复seed UUID与行不变，租户删除的自定义权限不会被重复种子补回。PLATFORM_ADMIN只有admin.access，无默认业务权限。
2. 同一AGENCY_STAFF在两客户有成员关系，仅A有显式权限和范围；A读通过，范围mask排除的动作和无授权客户B均拒绝。平台管理员即使有ALL_TENANT范围也不能取得snapshot.read。
3. 受控函数复查有效会话/当前租户/管理权限、目标成员和权限版本CAS；范围变更及移除均递增authz_version并追加脱敏审计，失败FK全部回滚；普通连接不能改删审计，伪造请求体role/tenantId/permissions被模型拒绝。

## 复核、范围与回滚
BE复核实际SQL/ACL、事务和故障边界；适用FE协作项按任务卡留具名记录。人工结论未取得，保持PENDING。
真实平台写入、供应商费用、前后端浏览器联调未运行。本地开发库保留新结构；测试临时库/探针清理完毕，迁移角色恢复NOLOGIN。
应用回退保留兼容的新表与隔离策略，禁止自动删数据；结构修复走备份/维护审阅后的前向迁移。
