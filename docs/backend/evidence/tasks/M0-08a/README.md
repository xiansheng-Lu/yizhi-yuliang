# M0-08a RLS与系统共享行策略技术交付与验收证据

日期2026-10-27（Asia/Shanghai）；代码基线 8f16d40a41f1cda5db9af5c5e73821a5c5cccb5f；输入378154d。负责人BE，前端协作NONE。
状态WAITING_REVIEW。用户明确持续推进，采用前置卡已验证的技术输入；不替代前置卡及本卡人工签字。

## 范围、输入、交付
范围以20册对应叶卡为准：独立迁移/角色引导、身份与最小目录、事务租户上下文、逐表策略、范围校验与权限种子；不修改前端源码。
共同运行证据见[../M0-06/verification.json](../M0-06/verification.json)；迁移与回滚说明见[alembic README](../../../../../backend/alembic/README.md)。
本轮27项unit/contract、19项真实integration、ruff/format/mypy通过；命令在WSL backend目录执行，Linux虚拟环境与Windows独立。

## 三条DoD
1. 普通app_login实际执行SELECT/INSERT/UPDATE/DELETE正反例；跨租户读不到，更新/删除零行，越租户新增/修改被拒。身份/角色/范围表仍不允许普通角色直接写。
2. 正租户上下文可读五种系统角色模板，运行角色不可增改删；空、0、负数、非法串、溢出上下文均不可读业务或共享模板。内容workflow_templates必须等M3建表后继续验，不在本卡提前建表。
3. 普通角色不能SET ROLE app_owner/auth_fn_owner、不能直读users；业务角色非所有者/NOBYPASSRLS，维护角色NOLOGIN，逐表ACL且安全函数固定search_path。

## 复核、范围与回滚
BE复核实际SQL/ACL、事务和故障边界；适用FE协作项按任务卡留具名记录。人工结论未取得，保持PENDING。
真实平台写入、供应商费用、前后端浏览器联调未运行。本地开发库保留新结构；测试临时库/探针清理完毕，迁移角色恢复NOLOGIN。
应用回退保留兼容的新表与隔离策略，禁止自动删数据；结构修复走备份/维护审阅后的前向迁移。
