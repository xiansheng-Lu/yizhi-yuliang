#!/usr/bin/env bash
# WSL/Linux入口；只操作本项目，不删除命名卷。
set -euo pipefail
cd "$(dirname "$0")/.."
action="${1:-check}"
case "$action" in check|start|stop|restart|reinitialize) ;; *) echo 'usage: infra.sh check|start|stop|restart|reinitialize' >&2; exit 2;; esac
test "$(docker info --format '{{.OSType}}')" = linux
test -f deployment/.env.infrastructure || { echo 'Run python3 scripts/prepare_local.py first' >&2; exit 2; }
compose=(docker compose --project-name yuliang-local --env-file deployment/.env.infrastructure --file docker-compose.yml)
case "$action" in
  check) "${compose[@]}" config --quiet ;;
  start) "${compose[@]}" up -d --wait --wait-timeout 180 ;;
  stop) "${compose[@]}" stop ;;
  restart) "${compose[@]}" restart; "${compose[@]}" up -d --wait --wait-timeout 180 ;;
  reinitialize) "${compose[@]}" exec -T postgres sh /docker-entrypoint-initdb.d/10-yuliang.sh ;;
esac
