"""显式本地设施验证入口，凭据仅传子进程环境，不打印连接串。"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def infrastructure_environment(
    root: Path, lifecycle: bool = False
) -> tuple[dict[str, str], list[str]]:
    values = dict(
        line.split("=", 1)
        for line in (root / "deployment/.env.infrastructure")
        .read_text(encoding="utf-8")
        .splitlines()
        if line and not line.startswith("#")
    )
    env = os.environ.copy()
    env.update(
        YULIANG_TEST_DATABASE_URL=(
            f"postgresql://app_login:{values['YULIANG_APP_PASSWORD']}@127.0.0.1:15432/yizhi"
        ),
        YULIANG_TEST_ADMIN_DATABASE_URL=(
            f"postgresql://postgres:{values['YULIANG_POSTGRES_PASSWORD']}@127.0.0.1:15432/yizhi"
        ),
        YULIANG_TEST_MIGRATION_DATABASE_URL=(
            f"postgresql://app_migrate:{values['YULIANG_MIGRATION_PASSWORD']}@127.0.0.1:15432/yizhi"
        ),
        YULIANG_TEST_AUTH_DATABASE_URL=(
            f"postgresql://app_auth:{values['YULIANG_AUTH_PASSWORD']}@127.0.0.1:15432/yizhi"
        ),
        YULIANG_TEST_REDIS_URL=(f"redis://:{values['YULIANG_REDIS_PASSWORD']}@127.0.0.1:16379/0"),
        YULIANG_TEST_BROKER_URL=(
            f"amqp://yuliang:{values['YULIANG_BROKER_PASSWORD']}@127.0.0.1:15672/yuliang"
        ),
        YULIANG_TEST_S3_URL="http://127.0.0.1:19000",
        YULIANG_TEST_S3_SECRET=values["YULIANG_MINIO_PASSWORD"],
        YULIANG_TEST_LIFECYCLE="1" if lifecycle else "0",
    )
    return env, list(values.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lifecycle", action="store_true", help="包含本项目服务重启与持久化验证")
    parser.add_argument("--details", action="store_true", help="输出已遮蔽本地凭据的短失败诊断")
    parser.add_argument(
        "--group",
        choices=(
            "all",
            "messages",
            "worker",
            "identity",
            "mail",
            "cost",
            "ai-ledger",
            "gateway",
            "parser",
            "catalog",
            "capability",
            "payload",
            "governance",
            "oauth",
            "sync",
            "facts",
            "freshness",
            "connections",
            "applications",
            "transitions",
            "risk",
            "rules",
            "intent",
            "budgets",
            "reservations",
            "guards",
            "idempotency",
            "attempts",
            "receipts",
            "settlement",
            "budget-governance",
            "execution-recovery",
            "projections",
            "approvals",
            "readback",
            "events",
            "sse",
            "configuration",
            "analysis",
            "integration-context",
            "read-routes",
            "adq-assets",
            "native-handoff",
            "adq-finance",
            "adq-reports",
            "shop-products",
            "shop-details",
            "shop-compass",
            "shop-orders",
            "external-token-runtime",
            "content-documents",
            "resource-lookups",
            "content-uploads",
            "lookup-targets",
            "report-drafts",
            "analysis-comparisons",
            "content-assets",
            "frontend-snapshot",
            "report-publications",
            "plan-read-projections",
            "content-validation",
            "resource-queries",
            "resource-query-plans",
            "adgroup-query-pagination",
            "execution-evidence-reads",
            "query-evidence-regression",
            "topic-adgroup-regression",
            "content-flows",
            "content-workflows",
            "content-imports",
            "overview-facts",
            "workflow-execution",
            "content-account-links",
            "content-analysis-events",
            "ai-content",
            "ai-content-topic",
            "generation-jobs",
            "sse-proxy-load",
        ),
        default="all",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lifecycle = "tests/integration/test_infrastructure_lifecycle.py"
    paths = {
        # 重启演练先运行，避免后续Worker复用在演练前建立的进程级MQ连接池。
        "all": [lifecycle]
        + [
            str(path.relative_to(root))
            for path in sorted((root / "tests/integration").glob("test_*.py"))
            if path.name != "test_infrastructure_lifecycle.py"
        ],
        "messages": ["tests/integration/test_durable_messaging.py"],
        "worker": ["tests/integration/test_worker.py"],
        "mail": ["tests/integration/test_email_codes.py"],
        "cost": ["tests/integration/test_ai_cost_gate.py"],
        "ai-ledger": ["tests/integration/test_ai_ledger.py"],
        "gateway": ["tests/integration/test_deepseek_gateway.py"],
        "catalog": ["tests/integration/test_platform_catalog.py"],
        "capability": ["tests/integration/test_capability_evidence.py"],
        "payload": ["tests/integration/test_platform_payloads.py"],
        "governance": ["tests/integration/test_platform_governance.py"],
        "oauth": ["tests/integration/test_oauth_vault.py"],
        "sync": ["tests/integration/test_sync_checkpoints.py"],
        "facts": ["tests/integration/test_metric_facts.py"],
        "freshness": ["tests/integration/test_resource_freshness.py"],
        "connections": ["tests/integration/test_connection_management.py"],
        "applications": ["tests/integration/test_job_applications.py"],
        "transitions": ["tests/integration/test_job_transitions.py"],
        "risk": ["tests/integration/test_risk_framework.py"],
        "rules": ["tests/integration/test_risk_rules.py"],
        "intent": ["tests/integration/test_intent_binding.py"],
        "budgets": ["tests/integration/test_budget_initialization.py"],
        "reservations": ["tests/integration/test_budget_reservations.py"],
        "guards": ["tests/integration/test_target_guards.py"],
        "idempotency": ["tests/integration/test_api_idempotency.py"],
        "attempts": ["tests/integration/test_execution_attempts.py"],
        "receipts": ["tests/integration/test_execution_receipts.py"],
        "settlement": ["tests/integration/test_budget_settlement.py"],
        "budget-governance": ["tests/integration/test_budget_governance.py"],
        "execution-recovery": [
            "tests/integration/test_execution_recovery.py",
            "tests/integration/test_execution_recovery_worker.py",
        ],
        "projections": ["tests/integration/test_job_projections.py"],
        "approvals": ["tests/integration/test_approvals.py"],
        "readback": ["tests/integration/test_independent_readback.py"],
        "events": ["tests/integration/test_event_projections.py"],
        "sse": ["tests/integration/test_core_sse.py", "tests/integration/test_connector_events.py"],
        "configuration": ["tests/integration/test_configuration_context.py"],
        "analysis": ["tests/integration/test_analysis_admission.py"],
        "integration-context": ["tests/integration/test_integration_context.py"],
        "read-routes": ["tests/integration/test_read_route_sources.py"],
        "adq-assets": ["tests/integration/test_adq_asset_mirrors.py"],
        "native-handoff": ["tests/integration/test_native_platform_handoff.py"],
        "adq-finance": ["tests/integration/test_adq_finance_reads.py"],
        "adq-reports": ["tests/integration/test_adq_reports.py"],
        "shop-products": ["tests/integration/test_shop_products.py"],
        "shop-details": ["tests/integration/test_shop_product_details.py"],
        "shop-compass": ["tests/integration/test_shop_compass.py"],
        "shop-orders": ["tests/integration/test_shop_orders.py"],
        "external-token-runtime": ["tests/integration/test_external_token_runtime.py"],
        "content-documents": ["tests/integration/test_cloud_documents.py"],
        "resource-lookups": ["tests/integration/test_resource_lookups.py"],
        "content-uploads": ["tests/integration/test_content_uploads.py"],
        "lookup-targets": ["tests/integration/test_lookup_targets.py"],
        "report-drafts": ["tests/integration/test_report_drafts.py"],
        "analysis-comparisons": ["tests/integration/test_analysis_comparisons.py"],
        "content-assets": ["tests/integration/test_content_assets.py"],
        "frontend-snapshot": ["tests/integration/test_frontend_snapshot.py"],
        "report-publications": ["tests/integration/test_report_publications.py"],
        "plan-read-projections": ["tests/integration/test_plan_read_projections.py"],
        "content-validation": ["tests/integration/test_content_validation.py"],
        "resource-queries": ["tests/integration/test_resource_queries.py"],
        "resource-query-plans": ["tests/integration/test_resource_query_plans.py"],
        "adgroup-query-pagination": ["tests/integration/test_adgroup_query_pagination.py"],
        "execution-evidence-reads": ["tests/integration/test_execution_evidence_reads.py"],
        "query-evidence-regression": [
            "tests/integration/test_resource_queries.py",
            "tests/integration/test_plan_read_projections.py",
            "tests/integration/test_job_projections.py",
        ],
        "topic-adgroup-regression": [
            "tests/integration/test_resource_queries.py",
            "tests/integration/test_plan_read_projections.py",
            "tests/integration/test_content_workflows.py",
            "tests/integration/test_workflow_execution.py",
        ],
        "content-flows": ["tests/integration/test_content_flows.py"],
        "content-workflows": ["tests/integration/test_content_workflows.py"],
        "content-imports": ["tests/integration/test_content_imports.py"],
        "overview-facts": ["tests/integration/test_overview_facts.py"],
        "workflow-execution": ["tests/integration/test_workflow_execution.py"],
        "content-account-links": ["tests/integration/test_content_account_links.py"],
        "content-analysis-events": ["tests/integration/test_content_analysis_events.py"],
        "ai-content": ["tests/integration/test_ai_content.py"],
        "ai-content-topic": ["tests/integration/test_ai_content_topic.py"],
        "generation-jobs": ["tests/integration/test_generation_jobs.py"],
        "sse-proxy-load": ["tests/integration/test_sse_proxy_load.py"],
        "parser": [
            "tests/integration/test_command_parse.py",
            "tests/integration/test_workspace.py",
            "tests/integration/test_deepseek_gateway.py",
        ],
        "identity": [
            "tests/integration/test_identity_database.py",
            "tests/integration/test_password_sessions.py",
        ],
    }[args.group]
    env, secret_values = infrastructure_environment(root, args.lifecycle)
    # --tb=no避免第三方库在失败栈中展开凭据；失败仍保留测试名与状态。
    result = subprocess.run(  # noqa: S603 -- 固定测试入口，禁止任意命令。
        [
            sys.executable,
            "-m",
            "pytest",
            *paths,
            "-m",
            "integration",
            "-q",
            "--tb=short" if args.details else "--tb=no",
            "-rN",
            "--show-capture=no",
        ],
        cwd=root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    for value in secret_values:
        output = output.replace(value, "[REDACTED]")
    print(output, end="")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
