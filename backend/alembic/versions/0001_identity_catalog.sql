-- 源自R00-02冻结13册的P01/P02/P03最小集；实际迁移固定保存，不运行时读取设计文档。
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
