# 后端开发入口

当前已交付工程、身份/目录迁移、租户隔离、权限管理、密码会话、最小管理接口及Worker骨架；身份与管理HTTP默认关闭，尚无平台写能力。执行状态和未通过依赖以 [任务台账](../docs/backend/evidence/task-status.json) 为准。

## 平台资料目录

M1-01核对并导入仓库既有1818条目录，按原始HTML、规范化JSON和生成目录逐条匹配，保存来源版本、摘要和不可混写的导入批次。50条空路径和1组重复路由保留为资料事实，未知HTTP方法存UNKNOWN；原资料的SOURCE_VERIFIED标记不等于真实平台取证。八连接器仅作资料索引，均不产生执行权限，微信豆无正式API目录绑定。

WSL中运行`uv run python scripts/import_platform_catalog.py`只做本地核对。先运行`uv run python scripts/migrate_local.py upgrade`，再以`uv run python scripts/migrate_local.py catalog-import`在受控维护窗口导入；重复导入同摘要不重复建记录，运行账号只有带租户上下文的读取权限。普通应用身份不能导入、改目录或把route_enabled设为true。回退应用时保留0015来源历史，不删表降级；关闭真实平台操作的现有守卫仍保持。

验证入口为`uv run python scripts/test_infrastructure.py --group catalog --details`。本卡以已验证底座进行技术准备，G0的BE/FE具名复核独立保留，目录导入不放行正式平台流量。

## 账户能力证据与灰度验证许可

M1-02的0016迁移按租户、账户、连接器、产品线、动作、目录版本及不可变绑定保存六阶段证据。证据仅由独立维护身份录入，没有用户自授权HTTP入口；运行身份只读。任何前置缺失、过期、失败或先于更新后前置的旧证据，都会使后续阶段无效。账户状态/身份变化停用旧绑定；目录或授权上下文变化须重新绑定取证。读路径使用READ_SCHEMA/READ_RESULT，写路径使用WRITE_SCHEMA/MATCHED_RECEIPT，不能混用。巨量、小红书及微信豆仍不开放平台操作。

首次写验证仅在前五阶段有效且没有既往执行结果时，消费由他人批准、具备相应店铺/账户审批范围、绑定请求键/冻结载荷摘要/金额上限/时间窗的一次性许可。消费再次检查当前成员、审批人、权限、账户和证据；同一上下文即使更换请求键也只能领取一次未核对的首次尝试，UNKNOWN不重新领取。许可不产生普通业务写能力，也不等于平台已受理或写入成功。后续执行内核必须从冻结载荷计算摘要/金额，并在同一受控流程持久化许可消费，继续经过风控、审批、执行和回读。

`uv run python scripts/test_infrastructure.py --group capability --details`覆盖真实PG隔离和消费竞争。当前没有实际账户证据或许可，没有平台请求；自动探测与证据回写由后续连接器任务接入。F03/F11接线、BE/FE复核和真实平台验收分别留证。

## 连接器端口与出站约束

M1-03a提供read/preview/submit/readback/refresh_token/capability_probe六方法异步端口，以及显式产品线注册表、固定操作描述和平台HTTP客户端。默认注册表为空，没有因此新增真实平台适配器、路由或调用。上下文来自服务端核验，仍须经过能力、额度、审批与执行守卫；端口的ACCEPTED回执与MATCHED回读分开建模。

操作描述由代码绑定目录ID/摘要、连接器/产品线、固定HTTPS Host/路径及extra=forbid参数Schema；账户、既有目标字段须与已核对上下文一致，签名器不能改业务方法/载荷/查询或Host。V9真实传输枚举shop在适配器注册边界显式映射wxpromo，ADQ不复用该注册项。巨量、小红书、微信豆仍不能注册本轮适配器。

客户端trust_env=false、不跟随重定向、有总超时与响应大小上限；只允许显式幂等读取最多三次，写和令牌轮换不自动重试。发送后超时/丢响应保留UNKNOWN；较长Retry-After留给后续调度。平台调用上下文内抑制HTTP库原始请求细节，其他调用日志不改。`uv run pytest tests/unit/test_connector_boundaries.py -q`使用合成端口/MockTransport验证上述边界，只计U层，不计平台验收；M1-03b继续载荷留档与业务错误分类。

## 平台载荷留档与错误分类

M1-03b的0017迁移增加请求意图和逐次网络观察的追加档案。RecordedPlatformClient先以真实PG事务保存请求/审计，再在每次发送前核对当前成员、账户/目标、读写类型、方法、路径和版本，持久化ATTEMPT后才发送；响应或丢失错误逐次追加。同outbound_id只查询原记录，内容改变拒绝，已有NOT_SENT或UNKNOWN不自动重新发送。响应落档失败保留已开始事实，业务侧须核对原单；在途回复不因发起人随后撤权而丢失，但撤权后禁止新发送。

档案保存代码白名单业务字段投影及带域隔离的HMAC，未知字段、凭据、订单PII和自由文本默认丢弃；投影条目/深度上限显式标记truncated，不用投影冒充完整事实来源。完整原文不在数据库重建，平台trace只存HMAC关联引用。对外摘要只有档案ID、requestId、分类和核对状态，不返回原始载荷；运行角色无UPDATE/DELETE，按租户及发起人隔离。

HTTP200需适配器明确核对业务Schema后才算读取成功或写受理，ACCEPTED仍不等于MATCHED。参数明确拒绝、429、5xx、发送后丢响应独立分类；写5xx/丢响应保留UNKNOWN，未经确认不标失败或重发。Retry-After数值/日期可解析，无法确认的等待显式记录，不偷偷缩短。当前存档器用于已有账户能力绑定的业务操作；OAuth授权上下文由M1-05另行接入，不能借业务预算绑定取得令牌权限。

`uv run python scripts/test_infrastructure.py --group payload --details`使用真实临时PG验证追加/隔离/事务故障；平台响应来自合成MockTransport，仍无真实平台验收。回退保留0017核对事实与审计，不删表或清空已发送记录。

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

## 解析候选集与提示词版本

M5-03a在aikernel/prompts/v1登记28条合成输入/授权上下文/预期动作或拒绝，以及候选模板、参数和SHA256清单。2026-10-27用户已直接确认28条预期，独立confirmation.json绑定代码基线、模板/样例总摘要和逐项摘要；确认来源为USER，BE/FE角色签署不代填。包状态仍为CANDIDATE，不自动登记生产ACTIVE提示词。
uv run --frozen python scripts/check_prompt_bundle.py仅核对冻结内容；加--review输出逐项摘要，加--require-human-labels在缺少有效用户确认或BE/FE标注时失败。--predictions接受以样例ID为键的离线预测JSON，缺项保留为失败，越权候选/额外审批字段/错误单位均拒绝。把给定预期自比仅为评估器的健康控制，不代表模型通过。
模板/样例变化必须升版本并记录理由，validate_successor禁止删除历史失败样例，逐项改标需要case_changes说明，样例摘要变化使旧签署失效。FrozenRoute绑定STARTED账本的配置ID/revision/主备模型/实际选择/提示词摘要与Schema；密钥或不属于原路由的模型不能混入快照。
真实模型的质量和耗时由M2-01b/M2-03验证；样例确认和F06联调仍由BE/FE具名复核。查看docs/backend/evidence/tasks/M5-03a/review-cases.md逐项确认，不以Agent候选预期代签。

## DeepSeek受控结构通道

M2-01b增加显式注入的AuthorizedGateway，符合ModelGateway协议；默认应用仍使用未接入路径，不自动读取本地模型密钥或启用付费。prepare_call先提交STARTED并核对冻结路由/模板/Schema，构造最多500字符、8192 token保守字节界限及512输出上限的解析请求，返回费用报价；调用端必须另行明确confirm和acquire，不能由网关自动确认。
当前只接用户选择的DeepSeek单模型解析，不创建备用模型。HTTP只允许固定HTTPS官方出口，不继承环境代理、不跟重定向；非流式JSON，禁用思考/工具，整个网络过程最多8秒，响应最多64KiB，没有自动修复、重试或切备。后续若增加调用步骤，每步须独立许可并在同一客户端15秒预算内或明确改异步契约；生产数据库连接/锁等待也需由部署超时约束。
输出只接受七类候选结构和服务器提供的授权对象/动作，空候选、越权ID、错误值类型、额外审批字段、工具/思考输出或截断均明确RULES/UNAVAILABLE降级。端口不创建申请、审批、执行或平台调用；完整语义拒绝、目录消歧和在线留痕归M2-03。
0013同事务CAS写入服务商返回的实际模型标识和响应SHA256，用量从响应usage核对；账本保留请求的配置/模型键，两者不混同。服务商未返回实际扣费回执时，费用保持UNKNOWN并持有原报价预占，token乘价格只能作为推算，不能写KNOWN。
本次用户授权人民币0.20元、最多6次合成样例验证已全部使用：6次受控结构通过，0.878～1.315秒，输入4009/输出422 token；按核验高峰价推算0.011394元，实际账单未核对。临时库已清理，脱敏请求/响应摘要与本地额度记录保留在证据，不自动重置或重跑。
参考deployment/.env.llm.example配置本地忽略文件；只有独立live测试显式读取它。真实验证默认排除，需YULIANG_LLM_LIVE_APPROVED和本地持久额度记录共同允许；测试先扣减次数和预占，失败也不自动退次数。生产价格、预算和ACTIVE路由未登记，F06接线与人工预期确认仍待完成。

## 指令解析与消歧

M2-03同时接入POST /v1/command-interpretations与会话消息入口。身份/CSRF、task.create及店铺/场次范围核验后，服务端规则输出QUERY/ACTION/UNRECOGNIZED；七类动作仅生成候选，金额使用人民币整数分、ROI保持正比值、开关播为人工交接。混合/多目标/相对金额/未知单位/注入/未知对象均澄清，同名歧义保留完整候选。

默认HTTP路径只运行规则，不加载DeepSeek密钥或产生费用。现有live_sessions只在connector_id=all时提供经过权限核对的场次目录；具体连接器缺少归属事实，不能假装已按连接器绑定。广告计划尚无M1同步读模型，默认目录为空，不把合成测试对象作为真实计划。查询只表明意图，不宣称已读取经营指标。

0014增加私有、只追加的interpretations，存输入摘要、范围、结果、规则/提示词版本和关联调用ID。规则路径无模型ID；模型后置守卫必须核对冻结输入/上下文、已报价载荷、本人/成员/请求/场景/提示词与已结束调用事实，失败通道只能记录MODEL_UNAVAILABLE拒绝。模型不能改动作/数值、选掉歧义对象或把自由文本中的“已执行”投影成事实。消息同键回放复用持久结果，不新增解析记录。

uv run python scripts/test_infrastructure.py --group parser --details验证真实PG/HTTP解析、私有隔离、消息幂等与网关回归；unit/contract另跑pytest。解析不创建command_jobs、审批、执行尝试或平台写调用。F06真实浏览器、G0人工联合验收仍单列；应用回退保留0014留痕及安全策略，不执行删表降级。

## 固定安全集真实回归

M2-04使用用户已确认的28条预期。首轮真实DeepSeek候选25/28匹配，三条对象范围差异被严格守卫拒绝；修复服务端消歧后，原28条实际响应回放28/28、针对三条差异另做真实复验3/3。动作/值/类型必须相同，只有原话与授权目录能把模型候选超集收窄，模型不能缩减真正歧义。最终规则标识command-rules-v2，原始模型与组合链结果分列，不能把组合链通过写成裸模型准确率100%。

真实验证共31次、预占93分，按核验高峰价推算约0.054316元，实际账单未知并保持UNKNOWN；独立临时测试库已删除，已用许可不清零。完整证据见docs/backend/evidence/tasks/M2-04。live入口默认排除，需要YULIANG_COMMAND_LIVE_APPROVED=1与独立持久许可，定向修后套件另用YULIANG_COMMAND_LIVE_RUN=recheck；不会自动重试、付费或启动生产在线模型。


## 计划观察、V9查询与内容派生（0062—0063）

ADQ计划/广告组读取复用受控网关、当前授权和唯一token provider；PlatformRuntime.sync_adq_plans只接受固定CAMPAIGN/ADGROUP协议和显式readonly_interfaces。完整分页观察与独立PlanMeasureProof分开：币种、预算/出价单位、ROI倍率/归因、源时间与状态均需有效字段依据。CAMPAIGN官方模型没有bid字段，不用0或子广告组出价填V8计划；ADGROUP保留其真实层级。此阶段尚未接入canonical AdPlan/完整FrontendSnapshot或V8 workspace，不把原始观察当执行依据。

POST /v1/delivery/integrations/queries沿用现有V9请求/响应，当前仅adq reports及wxshop compass/products/stock。要求真实Cookie、CSRF及八字段scope，逐请求核对主体/店铺/账户、授权/版本、六阶段与精确路径。未知源时间明确显示“源更新时间未提供”，syncedAt为持久抓取时间；缺口/null/真实0分开。商品为明确标注的本地已观察子集，未取详情ID给warning，不宣称平台完整目录或历史库存回放；超过完整范围上限明确拒绝。未实现资源及完整V8 workspace不伪造成功。

内容模型增加共用DAG/结构/字幕文本校验，所有保存及内部_save均复验；错误给中文字段路径及受控对象ID，不回显input/ctx。实际生成/SOP业务入口尚未集成，共用三purpose单元验证不能替代它们。音视频未完成服务端真实时长探测，继续拒绝相关素材和剪辑，不用客户端自报时长替代。

POST /v1/content/documents/{id}/flow接受kind/version/idempotencyKey，要求Cookie、CSRF和X-Shop-Id。复制规则与实际frontend flowDocument一致；父版本锁、稳定键、子稿首版和不可变关系同事务，原键重试不重复创建或跟随后续编辑漂移。已移除子稿为终态，不复活；新请求键才可重新创建。F09云UI适配及BE/FE验收仍独立保留。

云内容/素材绑定使用commerce_products.id，V9 products返回该内部ID；旧FrontendSnapshot的canonical products.id是另一命名空间。本地稿迁移必须显式核对映射，不能把两个UUID当作同一个商品。所有本批测试使用合成上游及真实本地设施，无新真实平台写入或供应商付费。
