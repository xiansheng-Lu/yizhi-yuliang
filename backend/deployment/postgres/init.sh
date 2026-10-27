#!/bin/sh
set -eu
# PostgreSQL官方入口以postgres执行，变量由本地忽略文件/部署机密提供。
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set=ON_ERROR_STOP=1 \
  --set=app_password="$YULIANG_APP_PASSWORD" \
  --set=auth_password="$YULIANG_AUTH_PASSWORD" \
  --set=migration_password="$YULIANG_MIGRATION_PASSWORD" <<'SQL'
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
SELECT 'CREATE ROLE app_login LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_login') \gexec
SELECT 'CREATE ROLE app_auth LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_auth') \gexec
SELECT 'CREATE ROLE app_migrate LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_migrate') \gexec
ALTER ROLE app_login PASSWORD :'app_password';
ALTER ROLE app_auth PASSWORD :'auth_password';
ALTER ROLE app_migrate PASSWORD :'migration_password';
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO app_login, app_migrate;
GRANT CREATE ON SCHEMA public TO app_migrate;
GRANT CONNECT ON DATABASE yizhi TO app_login, app_auth, app_migrate;
SQL
# 不把迁移/超级用户凭据注入业务进程；业务表对象权限由迁移按最小范围授予。
