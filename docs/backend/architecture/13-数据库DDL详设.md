# 13 数据库 DDL 详设

R00-02实施决定见[数据库与认证冻结规格](../contracts/R00-02-数据库与认证规格.md)与[机器清单](../contracts/database-auth-contract.json)：全局身份/受限认证入口、实际复合FK、系统模板和事实键均以此闭合。本文分段DDL不代替Alembic迁移和真实权限测试。

本册把 04 册的表结构要点落成可直接用于 Alembic 迁移的 PostgreSQL 17 DDL 规范与核心表定义。命名、类型、公共字段以 04 册第 2 节为准；本册不重复全部约 50 张表，只给三类内容：(1) 库级初始化与通用模板；(2) 资金/执行/消息等最关键表的完整 DDL；(3) 分区与迁移操作规范。其余业务表按同一模板生成。

2026-10-26评审说明：本册仍是分段DDL设计，含占位模板与待补业务表，**不能作为整份可直接执行的迁移脚本**。本轮修正已确认的扩展名、分区主键、支撑唯一键与关键模型冲突；全部实际复合FK、认证仓储权限、RLS和迁移顺序须在R00/R02落为迁移并真实验收，见18册D01–D06、19册A09–A11。

## 1. 库级初始化

```sql
-- 仅迁移账号（表所有者）执行；应用运行账号不是所有者
create extension if not exists pgcrypto;   -- gen_random_uuid()
create extension if not exists vector;     -- pgvector 项目的 SQL 扩展名
create extension if not exists btree_gin;  -- jsonb 组合索引需要时

-- 角色密码由部署机密设置；此处无可直接登录的默认密码。
create role app_owner nologin nosuperuser nocreatedb nocreaterole nobypassrls;
create role app_migrate nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;
create role app_login nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;
create role app_auth nologin noinherit nosuperuser nocreatedb nocreaterole nobypassrls;
create role auth_fn_owner nologin noinherit nosuperuser nocreatedb nocreaterole bypassrls;
-- 部署时仅migrate/login/auth启用独立LOGIN及密码，应用不持有迁移连接。
grant app_owner to app_migrate;
grant auth_fn_owner to app_migrate; -- 独立迁移凭据，显式SET ROLE；不授给运行角色
revoke create on schema public from public;
create schema auth authorization auth_fn_owner;
revoke all on schema auth from public;
grant usage on schema public to app_login;
grant usage, create on schema public to app_owner;
grant usage on schema public to auth_fn_owner;
grant usage on schema auth to app_auth;
grant connect on database yizhi to app_login, app_auth, app_migrate;
alter default privileges for role auth_fn_owner revoke execute on functions from public;
```

对象逐表授权：业务可变表按需CRUD，audit_logs只SELECT/INSERT，系统字典只SELECT；认证敏感表不向app_login/app_auth直接授权。auth_fn_owner仅获认证必需表的列/动作权限，无业务资产权限。禁止全schema默认CRUD、TRUNCATE/REFERENCES/TRIGGER/运行角色继承所有者。完整函数白名单与授权模板见R00-02。

## 2. 通用模板

### 2.1 公共列与命名

- 对外业务实体主键 `id uuid primary key default gen_random_uuid()`；租户为正bigint，内部事实/日志/投影流水可用bigint；外部平台标识另存native_id，不混用。
- 租户业务表 `tenant_id bigint not null`；users是全局身份，email_codes/auth_challenges/auth_events是登录前受限表；consumer_inbox是系统表；系统模板的tenant=0另加专用约束，不能套通用FK/写策略。
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
  for select using (tenant_id > 0 and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
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

workflow_templates沿用16册tenant=0系统行，需同时约束is_system和真实租户引用。系统角色/权限字典只读，成员/授权仍用正租户数据；不把未知身份记为系统行：

```sql
alter table workflow_templates add column owner_tenant_id bigint
  generated always as (nullif(tenant_id, 0)) stored references tenants(id);
alter table workflow_templates add constraint ck_template_owner
  check ((is_system and tenant_id = 0) or (not is_system and tenant_id > 0));
alter table workflow_templates add constraint ux_template_tenant unique (tenant_id, id);
alter table workflow_templates enable row level security;
alter table workflow_templates force row level security;
create policy workflow_templates_seed_owner on workflow_templates for all to app_owner
  using (true) with check (true); -- 仅NOLOGIN迁移所有者，运行身份不得继承
create policy workflow_templates_select_tenant on workflow_templates for select to app_login
  using (not is_system and tenant_id > 0
    and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
create policy workflow_templates_insert_tenant on workflow_templates for insert to app_login
  with check (not is_system and tenant_id > 0
    and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
create policy workflow_templates_update_tenant on workflow_templates for update to app_login
  using (not is_system and tenant_id > 0
    and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint)
  with check (not is_system and tenant_id > 0
    and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
create policy workflow_templates_delete_tenant on workflow_templates for delete to app_login
  using (not is_system and tenant_id > 0
    and tenant_id = nullif(current_setting('app.current_tenant', true), '')::bigint);
create policy workflow_templates_select_system on workflow_templates for select to app_login
  using (is_system and tenant_id = 0
    and nullif(current_setting('app.current_tenant', true), '')::bigint > 0);
-- 租户行四段策略同时限定not is_system、tenant_id>0及当前租户；UPDATE有USING/WITH CHECK。
alter table workflow_runs add column template_tenant_id bigint not null;
alter table workflow_runs add constraint ck_run_template_scope
  check (template_tenant_id = 0 or template_tenant_id = tenant_id);
alter table workflow_runs add constraint fk_run_template
  foreign key (template_tenant_id, template_id) references workflow_templates(tenant_id, id);
```

以上ALTER在M3创建16册表后执行，不是M0初始化；原单列template_id外键不能替代fk_run_template。系统模板唯一版本只由种子/维护流程写，app_login不能把租户行改为系统行。完整正反例见R00-02，当前未宣称已在数据库执行。

复合外键模板（防跨租户拼装）：

```sql
foreign key (tenant_id, campaign_id) references campaigns(tenant_id, id)
```

## 3. 身份与会话

```sql
create table tenants (
  id              bigint generated always as identity primary key check (id > 0),
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
  -- 全局身份不带tenant_id；成员关系是唯一业务租户归属来源。
  email           varchar(254) not null,
  password_hash   varchar(255) null,                       -- argon2id，验证码用户允许为空
  display_name    varchar(40) not null check (char_length(btrim(display_name)) between 1 and 40),
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
  kind            varchar(20) not null default 'INTERNAL'
                    check (kind in ('INTERNAL','AGENCY_STAFF','CUSTOMER')),
  authz_version   bigint not null default 1 check (authz_version > 0),
  status          varchar(20) not null default 'ACTIVE'
                    check (status in ('ACTIVE','REMOVED')),
  joined_at       timestamptz not null default now(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (tenant_id, user_id),
  unique (tenant_id, id),
  unique (tenant_id, id, user_id)
);

create table sessions (
  id              uuid primary key default gen_random_uuid(),  -- 内部主键，不直接作为 Cookie bearer
  token_hash      varchar(64) not null unique,                 -- 随机会话令牌的哈希，Cookie仅持原令牌
  csrf_token_hash varchar(64) not null,
  tenant_id       bigint not null references tenants(id),
  user_id         uuid not null references users(id),
  membership_id   uuid not null,
  authz_version   bigint not null check (authz_version > 0),
  version         int not null default 1 check (version > 0),
  user_agent_hash varchar(64),
  ip_inet         inet,
  created_at      timestamptz not null default now(),
  last_seen_at    timestamptz not null default now(),
  expires_at      timestamptz not null,          -- 滑动续期更新；绝对寿命上限 7 天由应用层强制
  absolute_expires_at timestamptz not null,       -- 滑动续期不能超过该值
  revoked_at      timestamptz null,              -- 吊销事实源：非 null 即失效
  revoke_reason   varchar(40),
  unique (tenant_id, id),
  foreign key (tenant_id, membership_id, user_id) references memberships(tenant_id, id, user_id),
  check (created_at < expires_at and expires_at <= absolute_expires_at),
  check (absolute_expires_at <= created_at + interval '7 days')
);
create index ix_sessions_user_active on sessions(user_id) where revoked_at is null;
create index ix_sessions_expires on sessions(expires_at);

create table email_codes (
  id              uuid primary key default gen_random_uuid(),
  tenant_id       bigint null,                  -- 登录前发码可为 null，见下方说明
  email           varchar(254) not null,
  purpose         varchar(20) not null check (purpose in ('LOGIN')),
  code_hash       varchar(64) not null,           -- HMAC(pepper,challengeId|purpose|code)
  verified_at     timestamptz null,
  consumed_at     timestamptz null,
  attempts        int not null default 0 check (attempts >= 0),
  max_attempts    int not null default 5 check (max_attempts between 1 and 10),
  expires_at      timestamptz not null,
  created_at      timestamptz not null default now()
);
create index ix_email_codes_send on email_codes(lower(email), purpose, created_at desc);
alter table email_codes add foreign key (tenant_id) references tenants(id);

create table auth_challenges (
  id              uuid primary key default gen_random_uuid(),
  cookie_token_hash varchar(64) not null unique,
  csrf_token_hash varchar(64) not null,
  expires_at      timestamptz not null,
  revoked_at      timestamptz null,
  created_at      timestamptz not null default now(),
  check (created_at < expires_at)
);

create table auth_events (
  id              bigint generated always as identity primary key,
  tenant_id       bigint null references tenants(id),
  user_id         uuid null references users(id),
  outcome         varchar(40) not null,
  email_digest    varchar(64) not null,
  ip_digest       varchar(64) null,
  request_id      varchar(64) not null,
  created_at      timestamptz not null default now(),
  check (tenant_id is null or tenant_id > 0)
);

create table auth_rate_limits (
  key_digest      varchar(64) not null,
  bucket_kind     varchar(20) not null check (bucket_kind in ('PASSWORD','EMAIL_SEND','EMAIL_VERIFY')),
  window_start    timestamptz not null,
  counter         int not null default 0 check (counter >= 0),
  blocked_until   timestamptz null,
  primary key (key_digest, bucket_kind, window_start)
);
```

users/email_codes/auth_challenges/auth_events/auth_rate_limits仅通过受限auth函数访问，不授运行角色直接表权限；email_codes和auth_events的tenant_id允许null而不是0，匹配成功后才按成员关系得出业务租户。函数信任边界/锁/次数/消费及吊销条件见R00-02，不提供任意user_id建会话的HTTP入口。

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
  entity_id   uuid null,
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
  entity_id     uuid null,
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
  unique (tenant_id, id, provider, product_line),
  foreign key (tenant_id, entity_id) references business_entities(tenant_id, id)
);

-- 店铺与广告账户多对多关联
create table entity_links (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  shop_id       uuid not null,
  account_id    uuid not null,
  created_at    timestamptz not null default now(),
  constraint ux_entity_link unique (tenant_id, shop_id, account_id),
  unique (tenant_id, id),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id),
  foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id)
);

create table live_sessions (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  shop_id       uuid not null,
  native_id     varchar(64) null,            -- 平台场次 id（授权前可能为空）
  title         varchar(120),
  status        varchar(20) not null default 'PLANNED',
  started_at    timestamptz null,
  ended_at      timestamptz null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint ux_live_sessions unique (tenant_id, id),
  unique (tenant_id, id, shop_id),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id)
);

create table products (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  shop_id       uuid not null,
  native_id     varchar(64) not null,
  name          varchar(200) not null,
  status        varchar(20) not null default 'ON_SHELF',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz null,
  constraint ux_products_native unique (tenant_id, shop_id, native_id),
  constraint ux_products_tenant_id unique (tenant_id, id),
  unique (tenant_id, id, shop_id),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id)
);

-- SKU/计划最小外键支撑，真实镜像字段在M1按04册补齐。
create table skus (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  product_id    uuid not null,
  native_id     varchar(128) not null,
  unique (tenant_id, id),
  unique (tenant_id, id, product_id),
  unique (tenant_id, product_id, native_id),
  foreign key (tenant_id, product_id) references products(tenant_id, id)
);

create table campaigns (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  shop_id       uuid not null,
  account_id    uuid not null,
  provider      varchar(20) not null,
  product_line  varchar(30) not null,
  native_id     varchar(128) not null,
  unique (tenant_id, id),
  unique (tenant_id, id, shop_id, account_id),
  unique (tenant_id, account_id, provider, product_line, native_id),
  foreign key (tenant_id, shop_id, account_id) references entity_links(tenant_id, shop_id, account_id),
  foreign key (tenant_id, account_id, provider, product_line) references ad_accounts(tenant_id, id, provider, product_line)
);
```

引用方约束写法（command_jobs、content_documents、workflow_runs 等同理）：

```sql
foreign key (tenant_id, shop_id) references shops(tenant_id, id),
foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id),
foreign key (tenant_id, live_session_id) references live_sessions(tenant_id, id),
foreign key (tenant_id, product_id) references products(tenant_id, id)
```

会话吊销语义：每次检查sessions未撤销、滑动/绝对到期、users/tenants/memberships均有效。权限版本变化重新取权限和数据范围，旧SSE游标resync；Redis缓存不能覆盖这些事实。认证解析不接受客户端tenantId设置上下文。

## 4. 指令任务（command_jobs / job_events）

严格对齐前端 V1.0 契约字段（蛇形命名）。

```sql
create table command_jobs (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         bigint not null references tenants(id),
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
alter table command_jobs add foreign key (tenant_id, live_session_id, shop_id)
  references live_sessions(tenant_id, id, shop_id);
create index ix_command_jobs_target on command_jobs(tenant_id, target_type, target_id);

create table job_events (
  id            bigint generated always as identity primary key,
  tenant_id     bigint not null references tenants(id),
  job_id        uuid not null,
  from_status   varchar(20),
  to_status     varchar(20) not null,
  actor_id      uuid null references users(id),
  detail        varchar(300) not null,          -- 中文流水文案
  reason_code   varchar(60),
  created_at    timestamptz not null default now(),
  foreign key (tenant_id, job_id) references command_jobs(tenant_id, id)
);
create index ix_job_events_job on job_events(tenant_id, job_id, id);
```

JobValue 联合结构的服务端校验在 Pydantic 层完成（MONEY/RATIO/BOOLEAN/HANDOFF），数据库只保留 jsonb_typeof 兜底。

## 5. 执行内核域

```sql
create table execution_orders (
  id                uuid primary key default gen_random_uuid(),
  tenant_id         bigint not null references tenants(id),
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
  tenant_id             bigint not null references tenants(id),
  order_id              uuid not null,
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
  unique (tenant_id, platform_request_key),     -- 同键第二次写直接查出在途
  unique (tenant_id, id),
  foreign key (tenant_id, order_id) references execution_orders(tenant_id, id)
);

create table platform_receipts (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             bigint not null references tenants(id),
  attempt_id            uuid not null,
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
  updated_at            timestamptz not null default now(),
  foreign key (tenant_id, attempt_id) references execution_attempts(tenant_id, id)
);
create index ix_receipts_verification on platform_receipts(tenant_id, verification);

create table execution_order_events (
  id            bigint generated always as identity primary key,
  tenant_id     bigint not null references tenants(id),
  order_id      uuid not null,
  from_status   varchar(20),
  to_status     varchar(20) not null,
  actor_id      uuid null references users(id), -- 用户动作有值，Worker为null，reason_code标来源
  reason_code   varchar(60),
  detail        varchar(300) not null,
  created_at    timestamptz not null default now(),
  foreign key (tenant_id, order_id) references execution_orders(tenant_id, id)
);
create index ix_exec_events_order on execution_order_events(tenant_id, order_id, id);

create table target_execution_guards (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
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
  tenant_id     bigint not null references tenants(id),
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
  tenant_id     bigint not null references tenants(id),
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
  tenant_id     bigint not null references tenants(id),
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
  tenant_id     bigint not null references tenants(id),
  order_id      uuid not null,
  state         varchar(12) not null default 'OPEN'
                  check (state in ('OPEN','INVESTIGATING','RESOLVED')),
  resolution    varchar(24) check
                  (resolution in ('CONFIRMED_EXECUTED','CONFIRMED_NOT_EXECUTED','MANUAL_CLOSED')),
  evidence      jsonb not null default '[]'::jsonb,
  created_at    timestamptz not null default now(),
  resolved_at   timestamptz null,
  resolved_by   uuid null references users(id),
  foreign key (tenant_id, order_id) references execution_orders(tenant_id, id)
);
```

所有执行域表逐张启用 RLS 四段策略（consumer_inbox 是跨租户系统表，不带 tenant_id、不启用租户 RLS，仅授权系统消费者）。

## 6. 额度账本

```sql
-- 风控策略最小FK支撑，完整规则/版本由M2-08补齐，不在此改变风控行为。
create table risk_policies (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  version       int not null default 1 check (version > 0),
  unique (tenant_id, id)
);

create table budget_ledgers (
  id                    uuid primary key default gen_random_uuid(),
  tenant_id             bigint not null references tenants(id),
  policy_id             uuid not null,
  bucket_key            varchar(80) not null,   -- 如 TENANT_DAILY / ACCOUNT:{id}
  limit_minor           bigint not null check (limit_minor > 0),
  confirmed_total_minor bigint not null default 0 check (confirmed_total_minor >= 0),
  reserved_increase_minor bigint not null default 0 check (reserved_increase_minor >= 0),
  currency              char(3) not null default 'CNY',
  version               int not null default 1,
  updated_at            timestamptz not null default now(),
  constraint ux_ledger_bucket unique (tenant_id, policy_id, bucket_key),
  unique (tenant_id, id),
  foreign key (tenant_id, policy_id) references risk_policies(tenant_id, id)
);

create table budget_reservations (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  ledger_id     uuid not null,
  job_id        uuid not null,
  order_id      uuid null,
  delta_minor   bigint not null check (delta_minor > 0),
  state         varchar(10) not null check (state in ('HELD','SETTLED','RELEASED')),
  created_at    timestamptz not null default now(),
  settled_at    timestamptz null,
  constraint ux_reservation_job unique (tenant_id, job_id),
  foreign key (tenant_id, ledger_id) references budget_ledgers(tenant_id, id),
  foreign key (tenant_id, job_id) references command_jobs(tenant_id, id),
  foreign key (tenant_id, order_id) references execution_orders(tenant_id, id)
);
```

非负约束兜底，`B+R+delta<=limit` 在新增预占的额度服务内持行锁校验。不能给事实账本加全局 `check(B+R<=limit)`：平台外部调额可能已超限，真实B仍必须可入账，写差异/告警并冻结新增额。UNKNOWN保留预占；减额在核验后串行调B。R00需补差异事实和冻结原因字段，R02/M2-10实测，不宣称本示例已覆盖全部账务。

## 7. 内容域（与 16 册 SOP 配套部分）

```sql
create table content_documents (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  kind          varchar(12) not null check (kind in ('planning','scripts','graphics','videos','editing')),
  title         varchar(120) not null,
  product_id    uuid not null,
  parent_id     uuid null,                                  -- 派生链（SOP 流转）
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
  created_by    uuid not null references users(id),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz null,
  unique (tenant_id, id),
  foreign key (tenant_id, product_id) references products(tenant_id, id),
  foreign key (tenant_id, parent_id) references content_documents(tenant_id, id)
);
create index ix_content_docs_parent on content_documents(tenant_id, parent_id);
create index ix_content_docs_product on content_documents(tenant_id, product_id, kind);

create table content_assets (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null references tenants(id),
  name          varchar(200) not null,
  kind          varchar(8) not null check (kind in ('image','video','audio','text')),
  product_id    uuid not null,
  document_id   uuid null,
  object_key    varchar(300) not null,                      -- S3/MinIO 私有 key
  mime          varchar(80) not null,
  size_bytes    bigint not null check (size_bytes >= 0),
  duration      numeric(10,2) not null default 0,
  text          text null,
  created_at    timestamptz not null default now(),
  deleted_at    timestamptz null,
  unique (tenant_id, id),
  foreign key (tenant_id, product_id) references products(tenant_id, id),
  foreign key (tenant_id, document_id) references content_documents(tenant_id, id)
);

create table generation_jobs (
  id                  uuid primary key default gen_random_uuid(),
  tenant_id           bigint not null references tenants(id),
  document_id         uuid null,
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
  result_asset_id     uuid null,
  error               jsonb null,
  cancel_supported    boolean not null default false,
  estimated_cost_minor bigint null,
  actual_cost_minor   bigint null,
  created_by          uuid not null references users(id),
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, document_id) references content_documents(tenant_id, id),
  foreign key (tenant_id, result_asset_id) references content_assets(tenant_id, id)
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
  tenant_id     bigint not null references tenants(id),
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
  head_seq      bigint not null default 0 check (head_seq >= 0),
  retained_from_seq bigint not null default 1,
  check (retained_from_seq >= 1 and retained_from_seq <= head_seq + 1)
);

create table event_projections (
  id            bigint generated always as identity primary key,
  tenant_id     bigint not null references tenants(id),
  tenant_seq    bigint not null check (tenant_seq > 0),
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
  tenant_id     bigint not null references tenants(id),
  fact_date     date not null,               -- Asia/Shanghai 归属日
  granularity   varchar(4) not null check (granularity in ('DAY','HOUR')),
  fact_hour     smallint null check (fact_hour between 0 and 23),
  provider      varchar(20) not null,
  product_line  varchar(30) not null,
  entity_kind   varchar(12) not null check
                  (entity_kind in ('SHOP','ACCOUNT','CAMPAIGN','LIVE','PRODUCT','SKU','VIDEO')),
  native_entity_id varchar(128) not null,     -- 来源对象标识，不冒充内部UUID
  shop_id       uuid not null,
  account_id    uuid null,
  campaign_id   uuid null,
  live_id       uuid null,
  product_id    uuid null,
  sku_id        uuid null,
  metric_name   varchar(40) not null,
  value_num     numeric(24,6) not null,
  unit          varchar(8) not null check (unit in ('MINOR','COUNT','RATE','RATIO','HOURS','BEAN')),
  currency      varchar(4) null check (currency in ('CNY','BEAN')),
  timezone      varchar(40) not null default 'Asia/Shanghai' check (timezone = 'Asia/Shanghai'),
  attribution_window varchar(80) not null,
  comparable_key varchar(200) not null,       -- 含归因窗口/币种/退款等规范化口径
  source_updated_at timestamptz null,        -- 未知不能拿fetched_at冒充
  fetched_at    timestamptz not null default now(),
  sync_run_id   uuid null,
  attrs         jsonb not null default '{}'::jsonb,
  primary key (id, fact_date),
  check ((granularity='DAY' and fact_hour is null) or (granularity='HOUR' and fact_hour is not null)),
  check (unit not in ('MINOR','BEAN') or value_num=trunc(value_num)),
  check ((unit = 'MINOR' and currency is not distinct from 'CNY')
    or (unit = 'BEAN' and currency is not distinct from 'BEAN')
    or (unit not in ('MINOR','BEAN') and currency is null)),
  check (sku_id is null or product_id is not null),
  check (campaign_id is null or account_id is not null),
  check (entity_kind <> 'CAMPAIGN' or (campaign_id is not null and account_id is not null)),
  check (entity_kind <> 'SKU' or sku_id is not null),
  check (entity_kind <> 'PRODUCT' or product_id is not null),
  check (entity_kind <> 'LIVE' or live_id is not null),
  check (entity_kind <> 'ACCOUNT' or account_id is not null),
  foreign key (tenant_id, shop_id) references shops(tenant_id, id),
  foreign key (tenant_id, account_id) references ad_accounts(tenant_id, id),
  foreign key (tenant_id, shop_id, account_id) references entity_links(tenant_id, shop_id, account_id),
  foreign key (tenant_id, account_id, provider, product_line) references ad_accounts(tenant_id, id, provider, product_line),
  foreign key (tenant_id, live_id, shop_id) references live_sessions(tenant_id, id, shop_id),
  foreign key (tenant_id, product_id, shop_id) references products(tenant_id, id, shop_id),
  foreign key (tenant_id, sku_id, product_id) references skus(tenant_id, id, product_id),
  foreign key (tenant_id, campaign_id, shop_id, account_id) references campaigns(tenant_id, id, shop_id, account_id)
) partition by range (fact_date);

create table metric_facts_2026m10 partition of metric_facts
  for values from ('2026-10-01') to ('2026-11-01');
create unique index ux_metric_facts_biz on metric_facts
  (tenant_id, fact_date, granularity, fact_hour, provider, product_line,
   entity_kind, native_entity_id, shop_id, account_id, campaign_id, live_id, product_id, sku_id,
   metric_name, unit, currency, timezone, attribution_window, comparable_key)
  nulls not distinct;
```

skus/campaigns的最小外键支撑定义见3.1，真实镜像字段归M1实现；不得先创建引用它们的事实迁移再补被引用表。可选维度用MATCH SIMPLE语义，同时用CHECK要求SKU有商品、CAMPAIGN有计划/账户等必要归属；同租户不代表同店铺。未关联店铺的账户数据先留同步隔离区，不塞虚构shop_id。缺失指标不写零，真实0才写0；比率按分子分母重算。source_updated_at或来源版本已知时拒绝旧页覆盖；未知时按同账户/流串行同步的已确认批次水位比较，不能靠抓取晚就认为数据新。

分区维护走专用维护账号与定时任务，DDL不经过应用账号。物化视图用 `refresh materialized view concurrently`，需要唯一索引；读模型仍需按租户/范围过滤。

## 10. 审计流水（只增不改）

```sql
create table audit_logs (
  id            bigint generated always as identity,
  tenant_id     bigint not null references tenants(id),
  actor_id      uuid null references users(id),
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

```sql
grant select, insert on audit_logs to app_login;
revoke update, delete, truncate, references, trigger on audit_logs from app_login;
-- 不授予子分区直接权限；归档/删除分区仅经独立维护任务。
```

## 11. Alembic 迁移纪律（expand-contract）

1. 每次迁移只做一个方向的兼容变化，分两次发布：expand（加表/加列/加索引，代码双写或忽略新列）→ 代码切换 → contract（删旧列/旧约束）。
2. 主键、外键、check、RLS 策略、触发器在迁移里显式声明，不依赖 ORM 自动行为。
3. `create index` 在线上一律 `concurrently`，须放在不带事务的迁移脚本中（`with op.get_context().autocommit_block()`）；新表的索引随建表创建即可。
4. 加 check 约束对大表用 `not valid` + 后续 `validate constraint`（ validate 不阻塞写）。
5. 每个迁移在文件头用简体中文注释说明：背景、是否并发安全、回滚方式、对应用版本的兼容窗口。
6. 种子数据（权限点、预置角色 OWNER/OPERATOR/AGENCY_OPERATOR/AUDITOR/PLATFORM_ADMIN、风控默认策略）用独立数据迁移，与表结构迁移分开，便于私有化交付时覆盖。
7. CI 必须在空库跑通 `alembic upgrade head`，并保留一份接近生产数据量的迁移演练库验证耗时与锁等待。
