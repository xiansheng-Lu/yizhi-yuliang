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
0008添加审计父表与UTC当月/下月分区；后续分区通过独立维护窗口补齐，运行角色不能建分区或改写审计。
0009添加AI配置/提示词版本及调用账本；用量/费用未知保持NULL，已知费用显式币种，配置只读且完成事实经CAS写入。
0010增加actor LOCAL上下文和私有工作区RLS、项目/对话/消息/幂等响应/任务归类。command_jobs仅DRAFT标识与对象FK底座，完整申请生命周期由M2-05a/b扩展，当前无创建或执行入口。
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
auth_fn_owner先前仅获密码/会话所需7张表权限；0011按需增加email_codes的SELECT/INSERT及限定列UPDATE，运行角色仍不能直读邮件码。本人资料更新由受限access函数完成，不扩大users的认证角色写权。

OWNER初始模板不默认给可配置的投放写权限；OPERATOR含内容写，AGENCY_OPERATOR无默认审批/连接管理，PLATFORM_ADMIN仅admin.access。
工作台页面从权限推导，不能用页面授权代替动作和范围检查。M0-13的成员/范围变更经独立受控函数执行，普通表ACL仍只读。

0011实现auth.issue_challenge/email_session及内部email_delivery布尔守卫，声明与授权同一事务。服务端预分配challengeId经本事务GUC传入，以便在数据库外计算独立pepper的HMAC；HTTP不接受GUC或摘要参数。
邮件函数统一按预认证行→邮件码行加锁，成功消费与会话插入原子提交。无成员候选时，在邮件摘要验证后从该邮箱真实活跃成员中选择，不能由客户端任意user/tenant创建会话。

0012增加AI预算/价格/报价/许可/追加事件，不登记生产价格或预算。运行账号仅SELECT，各费用函数固定search_path并精确授权app_login；所有费用增减先锁预算、再锁报价或许可。未知费用持有预占，已发送许可不能取消；已知回执重复结算幂等，跨币种保留UNKNOWN，超额如实记账并阻止后续许可。旧应用可继续运行，无自动删除账本的downgrade。

0013为AI调用增加provider_model和response_hash，保留原请求路由快照。新增15参数ai_finish在同一事务复用原13参数CAS并记录成对响应标识/摘要，旧入口兼容；不给运行角色直接UPDATE。未知实际扣费仍NULL，不用token推算金额冒充账单。

0014增加tenant+actor私有的interpretations，记录输入摘要、范围、候选、规则/提示词版本与模型调用关联；运行账号仅SELECT/INSERT，无UPDATE/DELETE。模型关联使用tenant/user/call复合FK，服务层另核对本人/成员/请求/场景、冻结输入和报价指纹。只追加模型调用唯一索引，不改既有业务字段；旧应用可继续运行，回退保留留痕，不删表。
