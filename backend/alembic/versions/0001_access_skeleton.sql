-- M0-06权限骨架；角色种子和实际授权归M0-13，不自动给管理员业务写权。
CREATE TABLE permissions (
  code varchar(80) PRIMARY KEY,
  description text NOT NULL
);
CREATE TABLE role_templates (
  code varchar(30) PRIMARY KEY CHECK (code IN ('OWNER','OPERATOR','AGENCY_OPERATOR','AUDITOR','PLATFORM_ADMIN')),
  name varchar(80) NOT NULL
);
CREATE TABLE role_template_permissions (
  template_code varchar(30) NOT NULL REFERENCES role_templates(code),
  permission_code varchar(80) NOT NULL REFERENCES permissions(code),
  PRIMARY KEY (template_code, permission_code)
);
CREATE TABLE roles (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id bigint NOT NULL REFERENCES tenants(id),
  code varchar(80) NOT NULL,
  template_code varchar(30) REFERENCES role_templates(code),
  name varchar(80) NOT NULL,
  version integer NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id,id), UNIQUE (tenant_id,code)
);
CREATE TABLE role_permissions (
  tenant_id bigint NOT NULL REFERENCES tenants(id),
  role_id uuid NOT NULL,
  permission_code varchar(80) NOT NULL REFERENCES permissions(code),
  PRIMARY KEY (tenant_id,role_id,permission_code),
  FOREIGN KEY (tenant_id,role_id) REFERENCES roles(tenant_id,id)
);
CREATE TABLE role_grants (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id bigint NOT NULL REFERENCES tenants(id),
  membership_id uuid NOT NULL,
  role_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id,id), UNIQUE (tenant_id,membership_id,role_id),
  FOREIGN KEY (tenant_id,membership_id) REFERENCES memberships(tenant_id,id),
  FOREIGN KEY (tenant_id,role_id) REFERENCES roles(tenant_id,id)
);
CREATE TABLE data_scopes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id bigint NOT NULL REFERENCES tenants(id),
  membership_id uuid NOT NULL,
  scope_type varchar(20) NOT NULL CHECK (scope_type IN ('ALL_TENANT','ENTITY','SHOP','ACCOUNT')),
  entity_id uuid,
  shop_id uuid,
  account_id uuid,
  permissions_mask text[],
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id,id),
  FOREIGN KEY (tenant_id,membership_id) REFERENCES memberships(tenant_id,id),
  FOREIGN KEY (tenant_id,entity_id) REFERENCES business_entities(tenant_id,id),
  FOREIGN KEY (tenant_id,shop_id) REFERENCES shops(tenant_id,id),
  FOREIGN KEY (tenant_id,account_id) REFERENCES ad_accounts(tenant_id,id),
  CHECK (
    (scope_type = 'ALL_TENANT' AND num_nonnulls(entity_id,shop_id,account_id) = 0)
    OR (scope_type = 'ENTITY' AND entity_id IS NOT NULL AND num_nonnulls(entity_id,shop_id,account_id) = 1)
    OR (scope_type = 'SHOP' AND shop_id IS NOT NULL AND num_nonnulls(entity_id,shop_id,account_id) = 1)
    OR (scope_type = 'ACCOUNT' AND account_id IS NOT NULL AND num_nonnulls(entity_id,shop_id,account_id) = 1)
  )
);
