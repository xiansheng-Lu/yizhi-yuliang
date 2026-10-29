# M0-06 迁移与最小身份数据模型技术交付与验收证据

日期2026-10-27（Asia/Shanghai）；代码基线 8f16d40a41f1cda5db9af5c5e73821a5c5cccb5f；输入378154d。负责人BE，前端协作F00。
状态WAITING_REVIEW。用户明确持续推进，采用前置卡已验证的技术输入；不替代前置卡及本卡人工签字。

## 范围、输入、交付
范围以20册对应叶卡为准：独立迁移/角色引导、身份与最小目录、事务租户上下文、逐表策略、范围校验与权限种子；不修改前端源码。
共同运行证据见[verification.json](verification.json)；迁移与回滚说明见[alembic README](../../../../../backend/alembic/README.md)。
本轮27项unit/contract、19项真实integration、ruff/format/mypy通过；命令在WSL backend目录执行，Linux虚拟环境与Windows独立。

## 三条DoD
1. 真实UUID临时空库upgrade head成功；同head再次执行对象清单不变；0001中间版本无业务ACL并FORCE RLS，默认拒绝。
2. 身份/目录/权限骨架实际复合FK闭合；合法会话可写，拼接另一用户成员的会话由三列FK拒绝，updated_at触发器真实生效。当前未建分区表，后续日志/事实分区归所属卡。
3. 使用Alembic受控SQL，没有ORM create_all；迁移失败事务回滚。downgrade明确拒绝自动删身份数据，版本和对象保留，应用回退走兼容结构及前向修复。

## 复核、范围与回滚
BE复核实际SQL/ACL、事务和故障边界；适用FE协作项按任务卡留具名记录。人工结论未取得，保持PENDING。
真实平台写入、供应商费用、前后端浏览器联调未运行。本地开发库保留新结构；测试临时库/探针清理完毕，迁移角色恢复NOLOGIN。
应用回退保留兼容的新表与隔离策略，禁止自动删数据；结构修复走备份/维护审阅后的前向迁移。

## BE具名复核记录（2026-10-29）

- 角色：BE 负责人；复核人：luwenlong
- 复核日期：2026-10-29（Asia/Shanghai）
- 基线提交：505771b40520cbd7ea94a04f59278d183fe00e90
- 确认来源：用户在本会话明确批准“R00 规格卡（4 张）可以过，M0 底座卡（22 张）也可以过”
- 结论：README 记载的三条 DoD 与 verification/static-check 证据核对一致，BE 复核通过
- 批准范围：仅本卡 BE 复核签字；不含 FE 侧 F00/F01/F02 签字、G0 门禁放行、A01-A12 全部结论及真实平台写/付费许可
