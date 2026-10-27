# 后端开发入口

当前已交付工程、配置、响应信封、最小身份/目录迁移、事务租户上下文、RLS与受控范围管理；尚无登录HTTP接口、Worker或平台写能力。执行状态和未通过依赖以 [任务台账](../docs/backend/evidence/task-status.json) 为准。

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
uv run uvicorn yuliang.main:create_app --factory --host 127.0.0.1 --port 8000
```

GET /healthz只表示进程存活；不代表数据库/队列就绪，完整readyz/metrics属于M0-03。本轮没有开放业务接口，也没有授权真实账户写入。

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
范围管理目前为受控数据库函数与Python用例，尚未开放HTTP管理入口。
内容workflow模板与实际平台字段分别归后续M3/M1，不提前创建。
