# 13 数据库 DDL 详设

本册把 04 册的表结构要点落成可直接用于 Alembic 迁移的 PostgreSQL 17 DDL 规范与核心表定义。命名、类型、公共字段以 04 册第 2 节为准；本册不重复全部约 50 张表，只给三类内容：(1) 库级初始化与通用模板；(2) 资金/执行/消息等最关键表的完整 DDL；(3) 分区与迁移操作规范。其余业务表按同一模板生成。

2026-10-26评审说明：本册仍是分段DDL设计，含占位模板与待补业务表，**不能作为整份可直接执行的迁移脚本**。本轮修正已确认的扩展名、分区主键、支撑唯一键与关键模型冲突；全部实际复合FK、认证仓储权限、RLS和迁移顺序须在R00/R02落为迁移并真实验收，见18册D01–D06、19册A09–A11。

## 1. 库级初始化

```sql
-- 仅迁移账号（表所有者）执行；应用运行账号不是所有者
create extension if not exists pgcrypto;   -- gen_random_uuid()
create extension if not exists vector;     -- pgvector 项目的 SQL 扩展名
create extension if not exists btree_gin;  -- jsonb 组合索引需要时

-- 三个角色：迁移所有者 / 应用账号 / 运维维护账号
-- 应用账号：非超级用户、非表所有者、无 BYPASSRLS、无 CREATEDB/CREATEROLE
create role app_login login password 'PLACEHOLDER_FROM_SECRET';
create role app_maintain login password 'PLACEHOLDER_FROM_SECRET'; -- BYPASSRLS，仅分区/清理任务
grant connect on database yizhi to app_login, app_maintain;
```

应用账号的对象权限由迁移统一授予（`select/insert/update/delete` 业务表，序列用法按需）；禁止授予 `truncate/references/trigger` 给应用账号。

## 2. 通用模板

### 2.1 公共列与命名

- 主键 `id uuid primary key default gen_random_uuid()`；外部标识另存 `native_id varchar(...)`。
- `tenant_id bigint not null`；所有业务表必有（不含纯系统表如 consumer_inbox）。
- 时间 `created_at timestamptz not null default now()`、`updated_at timestamptz not null default now()`；`deleted_at timestamptz null` 仅目录类表。
- `version int not null default 1`；金额 `bigint`（列名 `_minor`），微信豆 `bigint`（`_beans`），比值 `numeric(12,4)`。
- 枚举 `varchar not null + check`；状态名大写蛇形。
- 布尔语义用 varchar 状态而非布尔位（平台"未知"无法用 bool 表达）。

### 2.2 updated_at 触发器

```sql
create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;
-- 每张带 updated_at 的表：
create trigger trg_<table>_updated before update on <table>
  for each row execute function set_updated_at();
```

### 2.3 RLS 启用模板（每张业务表四段）

```sql
alter table command_jobs enable row level security;
alter table command_jobs force row level security;  -- 所有者也受限

create policy <t>_select on <t>
  for select using (tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
create policy <t>_insert on <t>
  for insert with check (tenant_id > 0 and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
create policy <t>_update on <t>
  for update using (tenant_id > 0 and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint)
  with check (tenant_id > 0 and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
create policy <t>_delete on <t>
  for delete using (tenant_id > 0 and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
```

应用侧每个事务开头执行（psycopg 参数绑定，不拼字符串）：

```text
select set_config('app.current_tenant', :tenant_id_str, true);  -- is_local=true，事务结束自动清理
```

含系统预置行的表（workflow_templates 系统模板、预置角色/权限点种子表等）额外加一条只读共享策略，且预置行对应用账号只读、不可改：

```sql
create policy workflow_templates_select_system on workflow_templates
  for select using (tenant_id = 0);
-- 预置行不提供 insert/update/delete 给 app_login；租户自建行走上面的租户策略
```

判断"是否系统表/共享表"是建表迁移评审项：除明确的预置数据外，禁止用 tenant_id=0 开共享口子。

复合外键模板（防跨租户拼装）：

```sql
foreign key (tenant_id, campaign_id) references campaigns(tenant_id, id)
```

## 3. 身份与会话

```sql
create table tenants (
  id              bigint generated always as identity primary key,
  name            varchar(120) not null,
  status          varchar(20)  not null default 'ACTIVE'
                    check (status in ('ACTIVE','SUSPENDED','CLOSED')),
  delivery_mode   varchar(20)  not null default 'MULTI_TENANT'
                    check (delivery_mode in ('MULTI_TENANT','SINGLE_DELIVERY')),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  deleted_at      timestamptz null
);

create table users (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       bigint not null references tenants(id),  -- 归属租户（主租户）
  email           varchar(254) not null,
  password_hash   varchar(128) not null,                   -- argon2id 编码串
  display_name    varchar(80)  not null,
  status          varchar(20)  not null default 'ACTIVE'
                    check (status in ('ACTIVE','DISABLED')),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  deleted_at      timestamptz null
);
-- 邮箱登录按小写归一全局唯一（同一邮箱不重复注册）
create unique index ux_users_email_lower on users (lower(email)) where deleted_at is null;

create table memberships (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       bigint not null references tenants(id),  -- 挂靠租户（可与用户主租户不同=服务商代运营）
  user_id         uuid not null references users(id),
  relation_kind   varchar(20) not null default 'EMPLOYEE'
                    check (relation_kind in ('EMPLOYEE','AGENCY')),
  status          varchar(20) not null default 'ACTIVE'
                    check (status in ('ACTIVE','REMOVED')),
  joined_at       timestamptz not null default now(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (tenant_id, user_id)
);

create table sessions (
  id              uuid primary key default gen_random_uuid(),  -- 内部主键，不直接作为 Cookie bearer
  token_hash      varchar(64) not null unique,                 -- 随机会话令牌的哈希，Cookie仅持原令牌
  csrf_token_hash varchar(64) not null,
  tenant_id       bigint not null,
  user_id         uuid not null references users(id),
  membership_id   uuid not null references memberships(id),
  user_agent_hash varchar(64),
  ip_inet         inet,
  created_at      timestamptz not null default now(),
  last_seen_at    timestamptz not null default now(),
  expires_at      timestamptz not null,          -- 滑动续期更新；绝对寿命上限 7 天由应用层强制
  absolute_expires_at timestamptz not null,       -- 滑动续期不能超过该值
  revoked_at      timestamptz null,              -- 吊销事实源：非 null 即失效
  revoke_reason   varchar(40)
);
create index ix_sessions_user_active on sessions(user_id) where revoked_at is null;
create index ix_sessions_expires on sessions(expires_at);

create table email_codes (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       bigint null,                  -- 登录前发码可为 null，见下方说明
  email           varchar(254) not null,
  purpose         varchar(20) not null check (purpose in ('LOGIN','RESET')),
  code_hash       varchar(128) not null,          -- 6 位码只存哈希
  challenge       varchar(64)  not null,          -- 一次性挑战值
  consumed_at     timestamptz null,
  attempts        int not null default 0,
  expires_at      timestamptz not null,
  created_at      timestamptz not null default now()
);
create index ix_email_codes_send on email_codes(lower(email), purpose, created_at desc);
```

email_codes.tenant_id 允许 null：登录验证码发生在身份解析之前（邮箱可能尚未归属任何租户），登录成功后再按 users/memberships 解析工作租户；其余业务表 tenant_id 一律 not null。

### 3.1 目录域支撑表（最小集，供外键闭合）

业务表对店铺/账户/商品/场次一律用 (tenant_id, 实体_id) 复合外键，下列为最小闭合定义；04 册中它们还有 native_id、状态、口径等更多业务列，建表迁移时按 04 册补齐，但唯一键与复合外键约束必须按下述方式建立。

```sql
create table business_entities (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   bigint not null references tenants(id),
  name        varchar(120) not null,
  kind        varchar(20) not null check (kind in ('ADVERTISER','MERCHANT')),
  status      varchar(20) not null default 'ACTIVE',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  deleted_at  timestamptz null,
  constraint ux_entities unique (tenant_id, id)
);

create table shops (
  id          uuid primary key default gen_random_uuid(),
  tenant_id   bigint not null references tenants(id),
  entity_id   uuid null references business_entities(id),
  platform    varchar(20) not null,          -- wxshop / douyinshop / redshop
  native_id   varchar(64) not null,
  name        varchar(120) not null,
  status      varchar(20) not null default 'BOUND',
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  deleted_at  timestamptz null,
  constraint ux_shops_native unique (tenant_id, platform, native_id),
  constraint ux_shops_tenant_id unique (tenant_id, id),
  foreign key (tenant_id, entity_id) references business_entities(tenant_id, id)
);

create table ad_accounts (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  entity_id     uuid null references business_entities(id),
  provider      varchar(20) not null,       -- tencent / ocean / xhs / beans
  product_line  varchar(30) not null,
  native_id     varchar(64) not null,
  name          varchar(120) not null,
  status        varchar(20) not null default 'BOUND'
                  check (status in ('BOUND','AUTHORIZED','DEGRADED','REVOKED')),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz null,
  constraint ux_accounts_native unique (tenant_id, provider, product_line, native_id),
  constraint ux_accounts_tenant_id unique (tenant_id, id),
  foreign key (tenant_id, entity_id) references business_entities(tenant_id, id)
);

-- 店铺与广告账户多对多关联
create table entity_links (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  shop_id       uuid not null references shops(id),
  account_id    uuid not null references ad_accounts(id),
  created_at    timestamptz not null default now(),
  constraint ux_entity_link unique (tenant_id, shop_id, account_id),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id),
  foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id)
);

create table live_sessions (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  shop_id       uuid not null,
  native_id     varchar(64) null,            -- 平台场次 id（授权前可能为空）
  title         varchar(120),
  status        varchar(20) not null default 'PLANNED',
  started_at    timestamptz null,
  ended_at      timestamptz null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint ux_live_sessions unique (tenant_id, id),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id)
);

create table products (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  shop_id       uuid not null,
  native_id     varchar(64) not null,
  name          varchar(200) not null,
  status        varchar(20) not null default 'ON_SHELF',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz null,
  constraint ux_products_native unique (tenant_id, shop_id, native_id),
  constraint ux_products_tenant_id unique (tenant_id, id),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id)
);
```

引用方约束写法（command_jobs、content_documents、workflow_runs 等同理）：

```sql
foreign key (tenant_id, shop_id) references shops(tenant_id, id),
foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id),
foreign key (tenant_id, live_session_id) references live_sessions(tenant_id, id),
foreign key (tenant_id, product_id) references products(tenant_id, id)
```

会话吊销语义：所有鉴权以 `sessions.revoked_at is null and expires_at > now()` 为准，Redis 缓存击穿或丢失不影响安全结论。

## 4. 指令任务（command_jobs / job_events）

严格对齐前端 V1.0 契约字段（蛇形命名）。

```sql
create table command_jobs (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         bigint not null,
  schema_version    varchar(8)  not null default '1.0',
  source            varchar(20) not null check (source in ('AUTOPILOT','PC_MANUAL','MOBILE_COMMAND')),
  shop_id           uuid not null,
  account_id        uuid null,
  live_session_id   uuid null,
  target_type       varchar(20) not null check (target_type in ('AD_PLAN','LIVE_SESSION')),
  target_id         varchar(64) not null,       -- 契约目标内部ID，服务端解析归属与native_id，禁止混用
  action_type       varchar(30) not null check (action_type in
                      ('CHANGE_DAILY_BUDGET','CHANGE_BID','SET_ROI_TARGET',
                       'PAUSE_AD_PLAN','RESUME_AD_PLAN','REQUEST_LIVE_START','REQUEST_LIVE_END')),
  old_value         jsonb null,                 -- JobValue 联合结构
  new_value         jsonb not null,
  intent            text not null,
  intent_hash       varchar(64) not null,       -- 不可变意图哈希（批准与执行前校验）
  status            varchar(20) not null check (status in
                      ('DRAFT','PENDING_APPROVAL','APPROVED','EXECUTING','VERIFYING',
                       'SUCCEEDED','FAILED','REJECTED','CANCELLED','EXPIRED','BLOCKED','MANUAL_REQUIRED')),
  risk_summary      jsonb not null,             -- {result,level,checks[],policy_version,checked_at}
  expires_at        timestamptz not null,
  error             jsonb null,                 -- {code,message,retryable,at}
  version           int not null default 1,
  idempotency_key   varchar(64) not null,
  created_by        uuid not null references users(id),
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  deleted_at        timestamptz null,
  constraint ux_command_jobs_idem unique (tenant_id, idempotency_key),
  constraint ux_command_jobs_tenant_id unique (tenant_id, id),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id),
  foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id),
  foreign key (tenant_id, live_session_id) references live_sessions(tenant_id, id),
  constraint ck_command_jobs_value check (jsonb_typeof(new_value) = 'object')
);
create index ix_command_jobs_status on command_jobs(tenant_id, status, updated_at desc);
create index ix_command_jobs_target on command_jobs(tenant_id, target_type, target_id);

create table job_events (
  id            bigint generated always as identity primary key,
  tenant_id     bigint not null,
  job_id        uuid not null references command_jobs(id),
  from_status   varchar(20),
  to_status     varchar(20) not null,
  actor_id      uuid null,
  detail        varchar(300) not null,          -- 中文流水文案
  reason_code   varchar(60),
  created_at    timestamptz not null default now()
);
create index ix_job_events_job on job_events(tenant_id, job_id, id);
```

JobValue 联合结构的服务端校验在 Pydantic 层完成（MONEY/RATIO/BOOLEAN/HANDOFF），数据库只保留 jsonb_typeof 兜底。

## 5. 执行内核域

```sql
create table execution_orders (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         bigint not null,
  job_id            uuid null,                  -- 仅COMMAND投影有值，其他业务不伪造command_job
  biz_type          varchar(20) not null,        -- COMMAND / CONFIG_TASK 等
  biz_id            uuid not null,
  account_id        uuid not null,
  provider          varchar(20) not null,
  product_line      varchar(30) not null,
  connector_id      varchar(20) not null,
  target_type       varchar(20) not null,
  target_id         varchar(64) not null,
  action_type       varchar(40) not null,
  intent_hash       varchar(64) not null,
  status            varchar(20) not null check (status in
                      ('PENDING_APPROVAL','PENDING','EXECUTING','VERIFYING',
                       'EFFECTIVE','FAILED','UNKNOWN','CANCELLED','PARTIAL','MANUAL')),
  scheduled_at      timestamptz not null default now(),
  cancellable       boolean not null default true,
  version           int not null default 1,
  idempotency_key   varchar(64) not null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  constraint ux_exec_orders_idem unique (tenant_id, idempotency_key),
  constraint ux_exec_orders_biz unique (tenant_id, biz_type, biz_id),
  constraint ux_exec_orders_tenant_id unique (tenant_id, id),
  foreign key (tenant_id, job_id) references command_jobs(tenant_id, id),
  foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id)
);
create index ix_exec_orders_pick on execution_orders(tenant_id, status, scheduled_at);
create index ix_exec_orders_biz on execution_orders(tenant_id, biz_type, biz_id);

create table execution_attempts (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             bigint not null,
  order_id              uuid not null references execution_orders(id),
  attempt_no            int not null,
  status                varchar(20) not null check (status in
                          ('PENDING','SENT','ACK_ACCEPTED','REJECTED','UNKNOWN')),
  platform_request_key  varchar(128) not null,   -- 平台稳定幂等号
  request_ref           uuid null,               -- platform_raw_payloads 存档
  response_ref          uuid null,
  error_code            varchar(60),
  started_at            timestamptz not null default now(),
  finished_at           timestamptz null,
  unique (tenant_id, order_id, attempt_no),
  unique (tenant_id, platform_request_key)       -- 同键第二次写直接查出在途
);

create table platform_receipts (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             bigint not null,
  attempt_id            uuid not null references execution_attempts(id),
  source                varchar(10) not null default 'PLATFORM',
  platform_receipt_id   varchar(128),
  accepted              boolean null,            -- null=结果未知
  received_at           timestamptz null,
  verification          varchar(10) not null default 'PENDING'
                          check (verification in ('PENDING','MATCHED','MISMATCH','UNKNOWN')),
  observed_value        jsonb null,
  verify_count          int not null default 0,
  verified_at           timestamptz null,
  raw_ref               uuid null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);
create index ix_receipts_verification on platform_receipts(tenant_id, verification);

create table execution_order_events (
  id            bigint generated always as identity primary key,
  tenant_id     bigint not null,
  order_id      uuid not null references execution_orders(id),
  from_status   varchar(20),
  to_status     varchar(20) not null,
  actor_id      uuid null,                    -- 用户动作为用户 id；Worker 动作为 null，reason_code 标来源
  reason_code   varchar(60),
  detail        varchar(300) not null,
  created_at    timestamptz not null default now()
);
create index ix_exec_events_order on execution_order_events(tenant_id, order_id, id);

create table target_execution_guards (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  account_id    uuid not null,
  provider      varchar(20) not null,
  product_line  varchar(30) not null,
  target_type   varchar(20) not null,
  target_id     varchar(64) not null,
  state         varchar(20) not null default 'IDLE'
                  check (state in ('IDLE','LEASED','QUARANTINED')),
  fence         bigint not null default 0,       -- 单调栅栏
  lease_owner   varchar(80),
  lease_until   timestamptz null,
  updated_at    timestamptz not null default now(),
  constraint ux_guard_target unique
    (tenant_id, account_id, provider, product_line, target_type, target_id),
  foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id)
);

create table idempotency_requests (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  caller_id     uuid not null,
  operation     varchar(60) not null,
  key           varchar(64) not null,
  request_hash  varchar(64) not null,           -- 同键异载荷 → 409
  state         varchar(10) not null check (state in ('IN_FLIGHT','DONE')),
  response_ref  jsonb null,
  created_at    timestamptz not null default now(),
  finished_at   timestamptz null,
  constraint ux_idempotency unique (tenant_id, caller_id, operation, key)
);

create table outbox_events (
  id            bigint generated always as identity primary key,
  event_id      uuid not null unique,
  tenant_id     bigint not null,
  aggregate_type varchar(30) not null,
  aggregate_id  varchar(64) not null,
  event_type    varchar(60) not null,
  payload       jsonb not null,
  state         varchar(10) not null default 'PENDING' check (state in ('PENDING','PUBLISHED')),
  attempts      int not null default 0,
  created_at    timestamptz not null default now(),
  published_at  timestamptz null
);
create index ix_outbox_pending on outbox_events(id)
  where state = 'PENDING';

create table consumer_inbox (
  id            bigint generated always as identity primary key,
  consumer      varchar(60) not null,
  event_id      uuid not null,
  received_at   timestamptz not null default now(),
  processed_at  timestamptz null,
  constraint ux_inbox unique (consumer, event_id)
);

create table scheduled_tasks (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  kind          varchar(40) not null,           -- VERIFY/EXPIRE/RECONCILE/...
  business_key  varchar(120) not null,
  state         varchar(12) not null default 'DUE'
                  check (state in ('DUE','CLAIMED','DONE','FAILED','RESCHEDULED')),
  payload       jsonb not null,
  due_at        timestamptz not null,
  attempts      int not null default 0,
  max_attempts  int not null default 8,
  lease_owner   varchar(80),
  lease_until   timestamptz null,
  last_error    varchar(300),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint ux_scheduled_biz unique (tenant_id, kind, business_key)
);
create index ix_scheduled_due on scheduled_tasks(state, due_at) where state in ('DUE','RESCHEDULED');

create table reconciliation_tasks (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  order_id      uuid not null references execution_orders(id),
  state         varchar(12) not null default 'OPEN'
                  check (state in ('OPEN','INVESTIGATING','RESOLVED')),
  resolution    varchar(24) check
                  (resolution in ('CONFIRMED_EXECUTED','CONFIRMED_NOT_EXECUTED','MANUAL_CLOSED')),
  evidence      jsonb not null default '[]'::jsonb,
  created_at    timestamptz not null default now(),
  resolved_at   timestamptz null,
  resolved_by   uuid null
);
```

所有执行域表逐张启用 RLS 四段策略（consumer_inbox 是跨租户系统表，不带 tenant_id、不启用租户 RLS，仅授权系统消费者）。

## 6. 额度账本

```sql
create table budget_ledgers (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             bigint not null,
  policy_id             uuid not null,
  bucket_key            varchar(80) not null,   -- 如 TENANT_DAILY / ACCOUNT:{id}
  limit_minor           bigint not null check (limit_minor > 0),
  confirmed_total_minor bigint not null default 0 check (confirmed_total_minor >= 0),
  reserved_increase_minor bigint not null default 0 check (reserved_increase_minor >= 0),
  currency              char(3) not null default 'CNY',
  version               int not null default 1,
  updated_at            timestamptz not null default now(),
  constraint ux_ledger_bucket unique (tenant_id, policy_id, bucket_key)
);

create table budget_reservations (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  ledger_id     uuid not null references budget_ledgers(id),
  job_id        uuid not null references command_jobs(id),
  order_id      uuid references execution_orders(id),
  delta_minor   bigint not null check (delta_minor > 0),
  state         varchar(10) not null check (state in ('HELD','SETTLED','RELEASED')),
  created_at    timestamptz not null default now(),
  settled_at    timestamptz null,
  constraint ux_reservation_job unique (tenant_id, job_id)
);
```

非负约束兜底，`B+R+delta<=limit` 在新增预占的额度服务内持行锁校验。不能给事实账本加全局 `check(B+R<=limit)`：平台外部调额可能已超限，真实B仍必须可入账，写差异/告警并冻结新增额。UNKNOWN保留预占；减额在核验后串行调B。R00需补差异事实和冻结原因字段，R02/M2-10实测，不宣称本示例已覆盖全部账务。

## 7. 内容域（与 16 册 SOP 配套部分）

```sql
create table content_documents (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  kind          varchar(12) not null check (kind in ('planning','scripts','graphics','videos','editing')),
  title         varchar(120) not null,
  product_id    uuid not null,
  parent_id     uuid null references content_documents(id),  -- 派生链（SOP 流转）
  workflow_run_id uuid null,                                -- 归属 SOP 运行，见 16 册
  status        varchar(10) not null default 'draft' check (status in ('draft','review')),
  brief         jsonb not null default '{}'::jsonb,
  body          text not null default '',
  scenes        jsonb not null default '[]'::jsonb,
  boards        jsonb not null default '[]'::jsonb,
  nodes         jsonb not null default '[]'::jsonb,
  edges         jsonb not null default '[]'::jsonb,
  clips         jsonb not null default '[]'::jsonb,
  settings      jsonb not null default '{}'::jsonb,
  prompt        text not null default '',
  messages      jsonb null,
  revision      int not null default 1,                     -- 云端保存乐观锁
  created_by    uuid not null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz null
);
create index ix_content_docs_parent on content_documents(tenant_id, parent_id);
create index ix_content_docs_product on content_documents(tenant_id, product_id, kind);

create table content_assets (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  name          varchar(200) not null,
  kind          varchar(8) not null check (kind in ('image','video','audio','text')),
  product_id    uuid not null,
  document_id   uuid null references content_documents(id),
  object_key    varchar(300) not null,                      -- S3/MinIO 私有 key
  mime          varchar(80) not null,
  size_bytes    bigint not null check (size_bytes >= 0),
  duration      numeric(10,2) not null default 0,
  text          text null,
  created_at    timestamptz not null default now(),
  deleted_at    timestamptz null
);

create table generation_jobs (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           bigint not null,
  document_id         uuid null references content_documents(id),
  type                varchar(16) not null check
                        (type in ('VIDEO','IMAGE','GRAPHIC','VIDEO_EDIT','SCRIPT')),
  provider            varchar(20) not null,
  external_job_id     varchar(128),                         -- 供应商任务号，查询兜底
  idempotency_key     varchar(64) not null,                  -- 调用方创建键，发送前落库
  provider_request_key varchar(128) not null,                -- 我方稳定请求键，不等外部ID返回
  submit_state        varchar(12) not null default 'NOT_SENT'
                        check (submit_state in ('NOT_SENT','SENT','ACKNOWLEDGED','UNKNOWN')),
  parameters          jsonb not null,
  status              varchar(20) not null check (status in
                        ('QUEUED','RUNNING','WAITING_PROVIDER','WAITING_REVIEW',
                         'SUCCEEDED','FAILED','CANCELLED')),
  progress            int not null default 0 check (progress between 0 and 100),
  result_asset_id     uuid null references content_assets(id),
  error               jsonb null,
  cancel_supported    boolean not null default false,
  estimated_cost_minor bigint null,
  actual_cost_minor   bigint null,
  created_by          uuid not null,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create unique index ux_generation_external on generation_jobs(provider, external_job_id)
  where external_job_id is not null;
create unique index ux_generation_idem on generation_jobs(tenant_id, idempotency_key);
create unique index ux_generation_request on generation_jobs(provider, provider_request_key);
```

`generation_steps`、`content_account_links` 以及 SOP 编排三张表（workflow_templates/runs/run_steps）按 16 册定义建表，列约定同本节。

## 8. AI 调用账本与事件投影

```sql
create table ai_invocations (
  id            bigint generated always as identity primary key,
  tenant_id     bigint not null,
  scenario      varchar(40) not null,        -- command_parse/copywriting/report_narrate/...
  model_key     varchar(80) not null,
  prompt_version varchar(40) not null,
  tokens_in     int not null default 0,
  tokens_out    int not null default 0,
  cost_minor    bigint not null default 0,
  status        varchar(12) not null check (status in ('OK','RATE_LIMITED','FAILED','FALLBACK')),
  latency_ms    int,
  created_at    timestamptz not null default now(),
  request_id    uuid
);
create index ix_ai_invoc_scenario on ai_invocations(tenant_id, scenario, created_at desc);

-- 分配序号时锁该租户水位行，递增与投影同事务提交
create table event_stream_heads (
  tenant_id     bigint primary key references tenants(id),
  head_seq      bigint not null default 0 check (head_seq >= 0)
);

create table event_projections (
  id            bigint generated always as identity primary key,
  tenant_id     bigint not null,
  tenant_seq    bigint not null,
  source_event_id uuid not null,
  scope_key     varchar(120) not null,       -- 订阅范围（用户/数据范围派生）
  entity_type   varchar(30) not null,
  entity_id     varchar(64) not null,
  version       int not null,
  event_type    varchar(60) not null,
  payload       jsonb not null,
  created_at    timestamptz not null default now(),
  unique (tenant_id, tenant_seq),
  unique (tenant_id, source_event_id)
);
-- id仅作内部标识；SSE使用租户绑定的tenant_seq，保留7天
create index ix_event_proj_scope on event_projections(tenant_id, scope_key, tenant_seq);
```

## 9. metric_facts 分区

```sql
create table metric_facts (
  id            bigint generated always as identity,
  tenant_id     bigint not null,
  fact_date     date not null,               -- Asia/Shanghai 归属日
  granularity   varchar(4) not null check (granularity in ('DAY','HOUR')),
  fact_hour     smallint null check (fact_hour between 0 and 23),
  provider      varchar(20) not null,
  product_line  varchar(30) not null,
  shop_id       uuid not null,
  account_id    uuid null,
  live_id       uuid null,
  product_id    uuid null,
  sku_id        uuid null,
  metric_name   varchar(40) not null,
  value_num     numeric(24,6) not null,
  unit          varchar(8) not null check (unit in ('MINOR','COUNT','RATE','RATIO','HOURS','BEAN')),
  currency      varchar(4) null check (currency in ('CNY','BEAN')),
  attribution_window varchar(80) not null,
  comparable_key varchar(200) not null,       -- 含归因窗口/币种/退款等规范化口径
  source_updated_at timestamptz null,        -- 未知不能拿fetched_at冒充
  fetched_at    timestamptz not null default now(),
  sync_run_id   uuid null,
  attrs         jsonb not null default '{}'::jsonb,
  primary key (id, fact_date),
  check ((granularity='DAY' and fact_hour is null) or (granularity='HOUR' and fact_hour is not null)),
  check (unit not in ('MINOR','BEAN') or value_num=trunc(value_num)),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id),
  foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id),
  foreign key (tenant_id, live_id) references live_sessions(tenant_id, id),
  foreign key (tenant_id, product_id) references products(tenant_id, id)
) partition by range (fact_date);

create table metric_facts_2026m10 partition of metric_facts
  for values from ('2026-10-01') to ('2026-11-01');
create unique index ux_metric_facts_biz on metric_facts
  (tenant_id, fact_date, granularity, fact_hour, provider, product_line,
   shop_id, account_id, live_id, product_id, sku_id, metric_name, comparable_key)
  nulls not distinct;
```

SKU完整表和对应复合FK在商品域迁移补齐，不能因本示例未列而省略。缺失指标不写零，真实0才写0；比率用分子分母重算。source_updated_at已知时，upsert用来源版本/时间拒绝旧页覆盖新行；未知时按同步批次已确认水位比较，不能靠抓取晚就认为数据新。

分区维护走专用维护账号与定时任务，DDL不经过应用账号。物化视图用 `refresh materialized view concurrently`，需要唯一索引；读模型仍需按租户/范围过滤。

## 10. 审计流水（只增不改）

```sql
create table audit_logs (
  id            bigint generated always as identity,
  tenant_id     bigint not null,
  actor_id      uuid null,
  action        varchar(60) not null,        -- job.approve / plan.write / connection.revoke ...
  object_type   varchar(30) not null,
  object_id     varchar(64) not null,
  before_hash   varchar(64),                  -- 变更前摘要（不存敏感明文）
  after_hash    varchar(64),
  request_id    uuid,
  ip_inet       inet,
  detail        jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  primary key (id, created_at)
) partition by range (created_at);          -- 按月分区，只追加
```

应用账号对 audit_logs 只有 insert/select，无 update/delete；分区归档用维护账号。

## 11. Alembic 迁移纪律（expand-contract）

1. 每次迁移只做一个方向的兼容变化，分两次发布：expand（加表/加列/加索引，代码双写或忽略新列）→ 代码切换 → contract（删旧列/旧约束）。
2. 主键、外键、check、RLS 策略、触发器在迁移里显式声明，不依赖 ORM 自动行为。
3. `create index` 在线上一律 `concurrently`，须放在不带事务的迁移脚本中（`with op.get_context().autocommit_block()`）；新表的索引随建表创建即可。
4. 加 check 约束对大表用 `not valid` + 后续 `validate constraint`（ validate 不阻塞写）。
5. 每个迁移在文件头用简体中文注释说明：背景、是否并发安全、回滚方式、对应用版本的兼容窗口。
6. 种子数据（权限点、预置角色 OWNER/OPERATOR/AGENCY_OPERATOR/AUDITOR/PLATFORM_ADMIN、风控默认策略）用独立数据迁移，与表结构迁移分开，便于私有化交付时覆盖。
7. CI 必须在空库跑通 `alembic upgrade head`，并保留一份接近生产数据量的迁移演练库验证耗时与锁等待。
