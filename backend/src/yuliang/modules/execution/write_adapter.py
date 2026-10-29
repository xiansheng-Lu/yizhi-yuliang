"""显式装配的生产WRITE adapter注册表；默认空且不自动启用。

DispatchRuntime 需要按 (action, provider, product_line) 装配 ExecutorFactory。
本模块提供生产装配入口，worker 进程通过 approved_execution_runtime() 获取。

默认行为：返回空 DispatchRuntime → resolve() 返回 None → dispatch_service 持久 BLOCKED_EXECUTOR。
生产启用条件：环境变量 YULIANG_ENABLE_WRITE_DISPATCHER=true，且
调用方在进程启动时 register(...) 了至少一个 factory。

不硬编码任何外部平台端点（腾讯/巨量引擎的真实 URL、签名规则、凭证
均由 ResourceBundle + OAuth 提供），本模块只负责注册表和装配框架。
"""

from yuliang.modules.execution.dispatch_runtime import DispatchRuntime, ExecutorFactory

# ---------------------------------------------------------------------------
# 生产 WRITE adapter 注册表
# ---------------------------------------------------------------------------

# (action, provider, product_line) -> ExecutorFactory
# 空字典 = 默认关闭；显式 register 后才启用
_REGISTRY: dict[tuple[str, str, str], ExecutorFactory] = {}


def register(
    action: str, provider: str, product_line: str, factory: ExecutorFactory
) -> None:
    """生产进程启动时显式调用；不可重复注册同一 key。"""
    key = (action, provider, product_line)
    if key in _REGISTRY or not callable(factory):
        raise ValueError("WRITE adapter 仅可显式装配一次且不可覆盖")
    _REGISTRY[key] = factory


def unregister_all() -> None:
    """测试清理入口；生产不调用。"""
    _REGISTRY.clear()


# ---------------------------------------------------------------------------
# 生产装配到 DispatchRuntime
# ---------------------------------------------------------------------------

def build_production_runtime() -> DispatchRuntime:
    """从注册表构造 DispatchRuntime；无注册时返回空（默认关闭）。"""
    runtime = DispatchRuntime()
    for (action, provider, product_line), factory in _REGISTRY.items():
        runtime.register(action, provider, product_line, factory)
    return runtime
