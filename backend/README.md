# 后端开发入口

当前已交付工程、身份/目录迁移、租户隔离、权限管理、密码会话、最小管理接口及Worker骨架；身份与管理HTTP默认关闭，尚无平台写能力。执行状态和未通过依赖以 [任务台账](../docs/backend/evidence/task-status.json) 为准。

## Python与离线验证

基线Python 3.13.14、uv，Windows PowerShell。依赖由uv.lock锁定；当前网络无法连接pypi.org，项目使用清华PyPI镜像，未更改全局包源。Python安装行为见[uv官方说明](https://docs.astral.sh/uv/guides/install-python/)。在backend目录执行：

```powershell
uv python install 3.13.14
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest tests/unit tests/contract -q
```

live默认排除；`-m unit`/`-m contract`/`-m integration`分别选择分组。`-m live`仅供以后经专门许可的真实调用，不自动运行任何资金或付费操作。集成测试缺环境明确失败/BLOCKED，不能把离线通过报成集成通过。

## WSL/Linux开发与真实设施验证

在WSL2 Ubuntu中进入`/mnt/d/codex/yizhi-yuliang/backend`。Windows的`.venv`不可与Linux共用；Linux环境放到Linux文件系统：

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.cache/yuliang-venv"
uv python install 3.13.14
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest tests/unit tests/contract -q
python3 scripts/prepare_local.py  # 首次生成；已有配置时拒绝覆盖
bash scripts/infra.sh check
bash scripts/infra.sh start
uv run python scripts/test_infrastructure.py
uv run python scripts/test_infrastructure.py --lifecycle
```

生命周期验证只重启`yuliang-local`项目，写入UUID命名的独立探针：PG schema、Redis键、持久MQ队列及S3桶/对象；重复初始化两次并比较重启后真实值，随后清理本次探针。入口只向子进程环境注入本地凭据，不输出连接串，失败栈默认隐藏。不带`--lifecycle`时该项明确SKIP，不能算整卡通过。

探针schema不属于租户模型；租户初始化归M0-06/M0-13，本卡不伪造租户种子。本地配置以随机密钥生成，仅保存于Git忽略的`.env`及`deployment/.env.infrastructure`；Linux创建模式0600，WSL挂载盘还受Windows ACL约束。应用配置不含postgres/迁移凭据。

Windows仍可用`scripts/prepare-local.ps1`和`scripts/infra.ps1`，后者要求Windows有Docker CLI。WSL NAT下Windows localhost转发可能不可用，设施测试在WSL内执行，无需把绑定扩大到公网。四个服务只监听容器宿主127.0.0.1并使用命名卷，`bash scripts/infra.sh stop`停止服务，Stop/Restart不删卷。

镜像固定版本；MinIO按本机已核对的RELEASE.2025-09-07T16-13-09Z摘要固定，避免latest漂移。对象存储只供本地兼容验证，生产S3方案归M5复核。实际证据见仓库根目录`docs/backend/evidence/tasks/M0-04/`。

## 启动

`prepare-local.ps1`生成的.env只供本地。也可复制.env.example后自行填写；缺必填配置或无效配置会拒绝启动，错误只列字段，不回显凭据。

```powershell
uv run uvicorn yuliang.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers
```

GET /healthz只表示进程存活；依赖状态通过受控来源的/readyz和/metrics读取。真实账户写入仍需对应验收和授权。

## 身份与范围底座

WSL中先重放角色/schema引导，再执行受控迁移窗口：

```bash
bash scripts/infra.sh reinitialize
uv run python scripts/migrate_local.py upgrade
uv run python scripts/migrate_local.py current
```

迁移后默认app_migrate为NOLOGIN，运行应用不持所有者/迁移连接。兼容及回滚见[迁移说明](alembic/README.md)。
真实隔离/权限测试在UUID命名临时数据库中进行，不会清空本地开发库。
五角色为初始模板，重复种子不覆盖租户已调整的权限；数据范围必须显式授予，平台管理员没有默认业务权限。
范围管理目前为受控数据库函数与Python用例，尚未开放HTTP范围编辑入口。
内容workflow模板与实际平台字段分别归后续M3/M1，不提前创建。

## 身份接口接线与资源

F01尚未完成浏览器联合接线，因此IDENTITY_API_ENABLED默认false。
确认接线后才显式开启，并配置独立于SESSION_SECRET的AUTH_AUDIT_SECRET（至少32字符）及精确ALLOWED_ORIGINS。
登录前先GET /v1/auth/csrf取得JSON令牌与HttpOnly预认证Cookie，POST /v1/auth/login带X-CSRF-Token。
登录后预认证Cookie撤销，重新GET csrf取得会话绑定令牌；每次获取会轮换，旧页403后只刷新令牌，不自动重放原写请求。
生产Cookie为Secure/HttpOnly/SameSite=Lax、host-only；分域不要求JS读取Cookie。
密码尝试按邮箱/IP的独立HMAC摘要各5次/5分钟持久节流，成功尝试也计入防爆破桶。
会话初始2小时、滑动续期且绝对7天上限，逐请求查PG；Redis只参与最佳努力缓存失效，不能覆盖PG身份状态。

ADMIN_API_ENABLED另行默认false，只有实际内部PLATFORM_ADMIN成员可读/internal/admin/directory；OWNER不会隐式取得权限。
管理入口默认仅回环网段，最多投影100条用户/租户，排除哈希/令牌并追加读取审计；不提供业务数据通用查询或资金写接口。
默认启动关闭代理头解析，反代部署需另行确认可信代理范围，不能用客户端X-Forwarded-For作为来源许可依据。
邮箱验证码由独立EMAIL_LOGIN_ENABLED控制，默认关闭；已实现真实SMTP与一次性消费，F01/F02浏览器联合接线仍单列。

## Worker与Beat

WSL中复用Linux环境，运行：

```bash
uv run celery -A yuliang.worker.app:app worker --pool=prefork --concurrency=1 --loglevel=WARNING
uv run celery -A yuliang.worker.app:app beat --loglevel=WARNING
```

默认四队列为yuliang.execute/platform/ai-media/system，JSON消息只携event/job/member/user ID及正租户标识。
逐任务重新建立事务上下文、复核成员，连接池在实际子进程按PID创建，不继承父进程连接。
当前只有system.identity_probe为真实只读诊断；三个副作用队列的guard明确失败，不调用平台或供应商。
默认副作用任务acks_late但worker丢失不自动重投、无自动retry；外部动作恢复仍归后续执行内核。
只读探针允许Worker丢失后重投，真实测试已验证SIGKILL子进程后由新子进程消费同一消息。
Beat使用Redis比较令牌租约，失去租约或Redis不可用时不派发；默认尚无周期业务任务。
租约不等于业务恰好一次。

## Outbox与内部恢复

0007增加租户Outbox、系统Inbox与scheduled_tasks。业务用例在同一business_transaction内emit，提交后调用publish_one或dispatch_pending；派发阶段不跨MQ持有数据库事务。
发布需真实confirm和mandatory路由确认，失败保留PENDING，崩溃租约30秒后可补投，始终沿用event_id。
dispatch_pending只扫描已验证租户，每批最多100条；调用方负责租户分片与周期调度，本轮没有启用跨租户扫描或自动业务Beat任务。
system.schedule_recovery核对原始持久事件，先持久RECEIVED，再以Inbox行锁把内部DB副作用与PROCESSED原子提交。
PROCESSED重复直接跳过；过期未完成的内部恢复事件可补投到同一ID，生成唯一DUE恢复记录。该记录不代表外部业务执行成功。
仅此内部DB幂等任务允许故障重投；平台、资金和AI未知结果不得套用自动补投协议。
真实崩溃演练和边界见docs/backend/evidence/tasks/M0-16c；运行全部设施测试时，重启演练先于创建Worker连接池。

## 追加审计

0008创建UTC月份分区的audit_logs，初始化当月和下月；应用账号只有父表SELECT/INSERT，无子表直接权限或分区DDL权限。
每月由独立维护窗口运行`uv run python scripts/migrate_local.py audit-partitions`，幂等补齐当月和下月。当前未部署生产维护定时器；部署方须设置月前维护与失败告警。
缺分区时写入明确失败，同事务业务回滚，不能跳过审计继续写。维护和归档保留在独立账号，不向运行应用放开DDL、UPDATE、DELETE或TRUNCATE。
业务用例调用modules.audit.service.record，同事务记录actor、服务器requestId、对象UUID及摘要；当前本人资料更新已接入，其他未来业务须显式接线。
详情只允许字段名与APPLIED/DENIED枚举；昵称前后值使用带独立密钥的HMAC摘要，不存原值。订单/凭据/原始平台载荷不进入审计。
by_request保持租户事务和RLS；尚无对外审计查询HTTP接口。登录前失败仅记受限auth_events、tenant=NULL，不创建tenant=0业务审计行。

## 就绪探测与降级

/readyz和/metrics默认只允许回环来源，可用OPERATIONS_ALLOWED_NETWORKS显式配置采集网段；启动保持--no-proxy-headers，不能信客户端X-Forwarded-For绕过来源限制。
探测以运行账号执行真实SELECT 1、Redis PING与AMQP连接，无业务数据读取；连接/查询有超时，并在每进程内缓存1秒。数据库连通不等于迁移、权限、分区或业务已验收。
业务库不可用，或开启身份接口时认证库不可用，readyz返回503；healthz保持进程存活200。MQ/Redis失效时返回200并明确degraded和各依赖DOWN，避免剔除仍可用的数据库读取能力。
MQ失败保留已提交Outbox申请，首次传输失败暂停本批，后续健康派发恢复。Redis不作为会话事实源，身份仍回查PG；Beat失去Redis租约时停止派发。
dispatchTransportReady只表示PG/MQ运输连通，Worker标为UNOBSERVED、业务执行器仍关闭；不证明外部执行已就绪。
Prometheus文本指标仅含固定依赖状态、API/运输就绪和采样时间，无租户标签或原始异常；尚未接入的任务在途/资金指标不以固定零占位。

## CI基础门禁

BE维护.github/workflows/backend-ci.yml，托管Ubuntu运行器使用固定Action提交、Python 3.13.14与uv 0.12.13，uv sync --frozen安装锁定依赖。后端、冻结规格或前端源码变化会触发检查。
检查包括ruff/格式/mypy、unit/contract、冻结源码/规格/任务状态、四设施重启、UUID临时空库迁移/重复升级、租户隔离和真实Worker消息故障回归。默认不运行live，不存在真实平台或付费凭据。
scripts/check_ci_sensitivity.py只在独立临时源码副本故意破坏契约版本、迁移SQL和租户策略；每项健康控制测试必须先通过，注入后必须实际失败且命中对应诊断，不能用环境故障冒充有效门禁。
敏感性测试复用本地设施但仅创建唯一临时测试库，结束恢复app_migrate NOLOGIN；不改开发源码或业务库。CI托管运行器使用临时随机配置，末尾仅清理该运行器设施，不上传.env或容器日志。

上游MinIO预构建仓库已无法匿名拉取；CI从官方签名发布对应源码07c3a429构建测试镜像，保留AGPL许可证。已有本地缓存可继续复用；新环境先构建并指定镜像：

```bash
docker build --tag yuliang-ci-minio:07c3a429 deployment/minio
export YULIANG_MINIO_IMAGE=yuliang-ci-minio:07c3a429
bash scripts/infra.sh start
```

该候选仅用于本地/CI的真实S3兼容验证，生产存储替代与维护仍归M5。

## AI模型与调用账本底座

0009增加租户隔离的model_configs、prompt_versions与ai_invocations。配置由独立维护身份按版本登记，运行账号只读；仅存服务端endpoint_ref/secret_ref，不接受API密钥、凭据URL或用户任意环境变量名。
ServerSecrets仅解析服务端显式注入的SecretStr映射，密钥不入库、不进入模型配置快照。主/备模型、配置版本、提示词内容摘要和输出Schema引用在调用前固定。
InvocationLedger先独立提交STARTED事实，费用/用量默认UNKNOWN且NULL；完成由受限函数CAS一次写入，不能由应用直接改账。已知费用必须明确币种且与所选模型计价币种一致，不跨币种相加。
备用调用须关联同请求/场景/用户的失败主调用并沿用原路由版本，不能在主调用仍在途时启动。该底座不自动重试或切备，结果未知的调用保留待核对事实。
ModelGateway是场景统一端口；当前UnavailableGateway明确返回RULES/UNAVAILABLE、NOT_SENT和待复核标志，不产生AI正文或执行动作。M5-02a已交付独立费用许可端口；真实供应商适配、出站保护与耗时验收仍归M2-01b，当前没有真实或付费模型调用。
13册旧示意DDL的token/cost默认0未沿用；按20册M2-01a和04册UNKNOWN规则落实NULL及独立已知状态，差异随本卡由BE复核。

## 中枢项目、对话与归类

0010增加本人私有的项目、会话、消息、幂等响应与任务归类。业务事务首条SQL同时设置租户和actor，均为LOCAL；私有表RLS同时校验租户与本人，连接池复用不能遗留身份。
开放身份API时同时挂载六个冻结接口：workspace/history、projects、conversation-messages、PATCH conversations/{id}、POST conversations/{id}/jobs、PUT command-jobs/{id}/project。需当前task.create权限，写请求检查精确来源和CSRF。
消息键在租户/本人范围内唯一，载荷规范化摘要相同回放已持久响应，不新增消息；更换文本、范围或来源返回409。项目/会话所有权同时有RLS和复合FK保护，写操作同事务追加不含正文的审计。
当前解释明确UNRECOGNIZED并说明能力未接入；不伪造模型输出或平台数据。只有已持久ACTION助手消息能关联本人有范围权限的真实任务标识，归类操作不改变任务状态。
command_jobs本卡只建立DRAFT任务标识、所有权与对象FK底座，运行账号不能写入；完整申请/风险/审批/执行字段及任务创建接口归M2-05a/b。关联测试中的ACTION和DRAFT记录为迁移身份夹具，不代表解析或执行通过。
身份接口仍默认关闭；F01/F06真实浏览器接线和最终联合验收继续单列。

## 邮箱验证码

配置deployment/.env.smtp.local（参考.env.smtp.example），仅支持证书校验开启的SSL或STARTTLS；凭据保持SecretStr，不输出供应商原始异常。另设至少32字符且独立于会话/审计密钥的EMAIL_CODE_SECRET，只有身份与邮件开关均开启才加载发送器。
先GET /v1/auth/csrf取得预认证Cookie和令牌，再POST /v1/auth/email-codes，email+purpose=LOGIN返回challengeId、expiresAt和重发间隔；登录method=email_code，携带相同预认证上下文、challengeId和6位code。
邮件码5分钟有效、最多5次尝试，HMAC-SHA256绑定challengeId|LOGIN|code，只存摘要；重发使旧码失效。失败次数先提交，成功消费和建会话同事务，插入失败整体回滚，并发只有一次成功。
邮箱/IP摘要上的重发间隔均为滑动60秒，验证5次/5分钟，EMAIL_DAILY_LIMIT控制UTC日发送上限（默认10）；429带Retry-After。未知系统邮箱走相同挑战流程，不自动注册或新增租户，登录失败使用统一文案。
SMTP在数据库事务外执行；服务商接受只记ACCEPTED，故障返回502并使挑战不可用，不能把接受状态等同实际收件。真实收件通过用户提供邮件中的验证码完成独立验收，证据仅存挑战/请求ID和结果。
CI邮件发送为捕获端口夹具，不会读取本地SMTP凭据或向外发信；实际SMTP/收件结果单独归档。默认不开启身份/邮箱接口，不因资源到位跳过F01/F02及具名复核。

## 首次付费费用门禁

0012增加按租户/场景/币种/有效期维护的费用预算、价格版本、报价、调用许可与追加事件；默认不登记任何真实价格或预算。运行角色只读，通过受限函数执行报价、明确确认、原子预占、一次性开始及结算。维护身份需依据实际价格来源登记带证据摘要的VERIFIED版本，变价新增revision，撤销旧版设REVOKED；重叠预算直接拒绝，不任选一条。
CostGate.quote接收服务端构造的BillableRequest，绑定请求键、载荷摘要、价格键与输入/输出最大计费单位。使用整数微分单价和NUMERIC计算，向上取整到分，冻结价格快照；报价最长5分钟，且不超过价格/预算有效期。confirm必须匹配报价摘要、金额与币种，不能省略或传NULL；acquire在PG预算行锁内核对剩余额度，预占与许可一次提交。
CostGate.execute先提交IN_FLIGHT，再在事务外调用端口，传入稳定请求键；没有许可、载荷变化、权限/价格/预算失效或重复开始都不能运行回调。LLM报价及发起前均核对已提交STARTED调用、模型键、场景和计费币种；GENERATION复用同一协议保存生成请求键，其真实生成账本接线归M3-19，不假称已接供应商。
外部异常、进程中断或记账失败保留UNKNOWN/IN_FLIGHT及预算预占，禁止自动重试或按到期退零；只有RESERVED且尚未发出的许可可以取消。带回执摘要的已知账单按实际金额结算，同一回执并发重报仅计一次，明确零元也必须有回执。币种不符保留预占，原始币种/金额进入追加事件，不能折算或相加；实际超报价如实记OVERAGE，并阻止越过当前预算的新许可和未发许可。
该门禁限制预估许可，无法强制第三方实际扣款上限。后续供应商适配必须将请求中的上限落实为实际输入/输出限制，并按报价与预算取得本次付费验证授权；未配置价格/预算或未确认时拒绝调用。自测全部使用合成价格/账单，不产生付费请求。可运行scripts/test_infrastructure.py --group cost --details验证真实PG边界，F10与BE具名复核仍单列。
