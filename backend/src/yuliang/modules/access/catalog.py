"""初始角色模板：页面由动作权限推导；模板不替代对象范围或平台动作许可。"""

READ = frozenset({"snapshot.read", "overview.read"})
ROLE_PERMISSIONS = {
    "OWNER": READ
    | {
        "task.approve",
        "policy.edit",
        "goal.edit",
        "report.publish",
        "connection.manage",
        "access.manage",
    },
    "OPERATOR": READ | {"task.create", "plan.write", "budget.edit", "content.write"},
    "AGENCY_OPERATOR": READ | {"task.create", "plan.write", "budget.edit"},
    "AUDITOR": READ,
    "PLATFORM_ADMIN": frozenset({"admin.access"}),
}
PAGE_PERMISSIONS = {
    "overview": {"overview.read", "snapshot.read"},
    "monitor": {"snapshot.read"},
    "products": {"snapshot.read"},
    "tasks": {"snapshot.read"},
    "plans": {"snapshot.read"},
    "decisions": {"snapshot.read"},
    "executions": {"snapshot.read"},
    "budgets": {"snapshot.read"},
    "reports": {"snapshot.read"},
    "analytics": {"snapshot.read"},
    "goals": {"snapshot.read"},
    "command": {"task.create"},
    **{
        name: {"content.write"}
        for name in (
            "planning",
            "scripts",
            "videos",
            "graphics",
            "editing",
            "assets",
            "experiments",
        )
    },
}


def workspace_pages(permissions: set[str]) -> list[str]:
    return sorted(page for page, required in PAGE_PERMISSIONS.items() if permissions & required)
