# 身份/目录迁移与回滚边界

迁移需要独立的`YULIANG_MIGRATION_DATABASE_URL`；运行应用的`.env`没有这个凭据。
部署引导先创建NOLOGIN所有者/认证函数所有者和NOINHERIT迁移角色，运行角色不继承所有者。
迁移窗口临时启用app_migrate LOGIN，显式SET LOCAL ROLE app_owner；关闭后立即NOLOGIN。
测试只在UUID命名临时数据库运行，维护身份仅用于建库/夹具/拆除；运行攻击场景用app_login。

WSL中，先`bash scripts/infra.sh reinitialize`按新脚本补齐本地角色，随后：

```bash
uv run python scripts/migrate_local.py upgrade
uv run python scripts/migrate_local.py current
```

0001创建P01/P02/P03身份与最小目录、权限骨架、时间触发器。
0003添加五角色初始种子及受控范围变更/追加审计。
0004落实密码/会话/预认证函数面，0005本人资料更新，0006内部管理只读投影。
0007增加Outbox/Inbox及内部恢复调度事实；应用回退保留事实并停止派发，不自动删除消息表。
业务表从创建即ENABLE/FORCE RLS且无运行ACL，0002增加实际四段策略与逐表ACL。
users/sessions/邮件码/挑战/认证事件/节流表不给运行角色直读，认证白名单在M0-09/11实现。
系统角色模板/权限字典仅有正租户上下文时可读，运行角色不可写；内容workflow模板属于M3，未提前建表。
当前身份表不分区；日志/事实的分区和包含分区列的主键随所属M0-17/M1/M4迁移创建，不能拿本卡证明后续分区已验收。

迁移使用受控SQL，不使用ORM create_all；同一head重跑只读取版本，DDL事务失败全部回滚。
无删除或改名的破坏性变更；前一版应用可继续使用已有接口，身份新结构在新接口开放前可先部署。
应用回退保留这些新表和隔离策略；downgrade明确拒绝自动删身份数据或撤销安全边界。
若必须撤销结构，需要独立维护计划、备份与恢复演练，再以经过审阅的前向迁移操作；不提供一键删库替代回滚。

租户工厂只接受服务端已复查的VerifiedIdentity，每个事务首句set_config(...,true)，API用同一同步用例run_sync，Worker直接调用。
RLS防漏过滤，不能防持有运行数据库凭据的人任意设置GUC；数据库凭据隔离与服务端会话复查都是必需条件。
范围服务要求成员有效、动作权限、店铺范围、账户范围及实际关联同时成立；两个对象均须有授权，不接受客户端声明权限。
普通角色不能直接修改成员/角色/范围；M0-13受控管理函数执行事务内校验与追加审计，表ACL只开放读取。
auth.check_csrf(text,text)是新增的内部最小布尔守卫，只精确授权app_auth；不扩展resolve_session的输出，也不返回CSRF摘要。
auth_fn_owner当前只获密码/会话所需7张表的最小权限，未预授邮件表权限；本人资料更新由受限access函数完成，不扩大users的认证角色写权。

OWNER初始模板不默认给可配置的投放写权限；OPERATOR含内容写，AGENCY_OPERATOR无默认审批/连接管理，PLATFORM_ADMIN仅admin.access。
工作台页面从权限推导，不能用页面授权代替动作和范围检查。M0-13的成员/范围变更经独立受控函数执行，普通表ACL仍只读。
