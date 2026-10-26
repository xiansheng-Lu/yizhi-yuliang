# R00-02 数据库与认证规格验收记录

日期：2026-10-26（Asia/Shanghai）；输入提交36abdd6；本地分支docs/backend-r00-database-auth；负责人BE，F00/F12参与规格复核。

**本卡后端规格交付和Agent静态自检完成，正式状态WAITING_REVIEW。** 用户明确要求继续第二项，R00-01技术基线可用；没有代填第一项或第二项BE/FE签字，M0/M1/M4真实验收仍NOT_RUN。

## 交付物

- [数据库与认证冻结规格](../../../contracts/R00-02-数据库与认证规格.md)：十项矛盾决定、类型与归属、系统模板、迁移DAG、13个认证函数、事实可比键、事件游标及唯一业务用例。
- [机器清单](../../../contracts/database-auth-contract.json)：角色、函数签名/守卫、强制FK、迁移阶段、后续引用以及13项真实用例的输入/动作/预期/证据。
- [认证参考SQL](../../../contracts/r00-02-auth-surface.sql)：仅resolve_session函数体和最小ACL，创建/撤销PUBLIC/精确授权同事务；其余12个函数未实现，不提供假成功占位。
- [静态检查实际报告](static-check.json)：含输入SHA256、结构统计和25项故障副本各自被拒绝的原因。
- 04/09/13已统一；14仅澄清SSE游标签名格式、15同步同用例调用决定、16仅标注系统模板/FK约束归口，不改变内容业务规则。

## 实际命令与结论

在仓库根目录执行：

| 命令 | 实际结论 | 证明范围 |
|---|---|---|
| `python docs/backend/tools/check_database_auth.py --self-test --report docs/backend/evidence/tasks/R00-02/static-check.json` | PASS：39表、84外键、11迁移阶段、13函数签名、25故障变体被拦截 | 从13册受控SQL片段解析列、FK目标唯一键/类型、分区键、事实索引及角色约束；不是通用PG语法或权限实测 |
| `python docs/backend/tools/check_plan.py` | PASS：149卡/7门禁/156节点/101父任务/42项A/14项F | 原任务关系保留，没有借规格修改批量关闭任务 |
| `python docs/backend/tools/check_contracts.py` | PASS：15份前端源码、52路由 | 第一项兼容基线未改 |
| `node docs/backend/tools/freeze_frontend_surface.mjs` | PASS：54处调用、133类型、52路由 | 前端请求/字段无漂移 |
| R00-02相关相对链接、JSON解析和Python AST检查 | PASS | 文档与工具可读取，不代表后端业务通过 |
| 现有Ruff检查新增check_database_auth.py | PASS | 工具静态代码检查，未新增依赖 |
| `git diff --check` | PASS | 文档空白检查 |

本卡只使用现有Python标准库/Node及仓库TypeScript，无需安装新的后端依赖。缺Docker不阻塞D类规格；也不因此用Mock替代PG、RLS或并发实测。

## 三条DoD

| 卡片标准 | 本次可核对证据 | 状态 |
|---|---|---|
| 1. 租户/实体ID、分区PK、系统模板只读、事实可比键无矛盾 | users全局；会话三列FK；全部已声明FK目标唯一且类型相容；分区唯一键含分区列；模板正租户/系统行及选择器分开；完整事实键和NULL安全币种约束 | D类技术PASS，数据库执行待对应M卡 |
| 2. 无租户认证有受限入口；业务账号无BYPASSRLS；未知邮箱审计有归属 | app_login/app_auth无特权和认证表直读；NOLOGIN函数所有者只获8张认证必需表白名单；PUBLIC执行权撤销；未知邮箱auth_events租户为空；13个函数签名/守卫和1份参考函数体 | D类技术PASS，真实授权/攻击测试NOT_RUN |
| 3. API/Worker共用规则有唯一具体调用决定 | 同一同步Session用例；API用run_sync，Worker直接调；领域纯函数；外部IO在事务外；进程池/Session不共享；SSE/调用账本迁移不等待内容域 | 决定已交付，运行一致性实测NOT_RUN |

## 复盘

1. “用户、成员、租户”必须分开；否则服务商多租户登录或会话归属会产生矛盾，只有单列FK无法兜底。
2. RLS不是全权安全代理：完整性检查可能绕过RLS，GUC可由持连接者设置；同时需要真实复合FK、可信认证入口、对象数据范围与受控错误。
3. 写了CHECK不一定排除了NULL；币种约束已改为IS NOT DISTINCT FROM，并加入故障变体。
4. NOLOGIN函数所有者仍要最小表权限；“有BYPASSRLS但仅8张认证表权限”和“整个业务库可绕过”不可混为一谈。密码校验信任后端的边界明确写出，不夸大函数ACL能力。
5. 迁移不是从P00一路等到最后：AI调用账本、核心SSE和内容按实际表依赖分支，避免把MVP等待链又引回后期内容模块。
6. 后续13项真实用例逐项保留输入/动作/预期/证据和所属任务；本卡25项静态负例通过不代表这些用例已经运行。

## 待复核与后续

- BE复核模型、最小授权和迁移归属；F00复核内部ID串行化/身份契约未变，F12复核签名游标仅存回传及resync。均未取得人工签字，保持WAITING_REVIEW。
- 参考SQL未在真实PG执行，SQL片段含后续迁移骨架；真实空库、RLS/FK攻击、角色ACL、消费并发、回滚、SSE提交顺序由机器清单列出的M0/M1/M4卡验收。
- 下一项是R00-03资金与崩溃恢复规格；是否进入实现仍按用户任务授权、技术依赖与相应门禁，不以本卡静态PASS替代真实开放许可。
- Git按本地分支→合并main→推送main，只纳入本项文档/检查/证据；既有backend/及06/12册未验收草稿保持本地，不顺带提交。
