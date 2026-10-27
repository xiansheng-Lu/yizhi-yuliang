# M0-01 工程与验证入口

日期：2026-10-27（Asia/Shanghai）；代码基线 `c95a4f85ac095f38fea2d28afd4ab4eb3d98f998`；本地分支feat/backend-wsl-foundation。
负责人BE；用户明确继续开发并提供WSL Docker。工程采用R00技术基线，双端签字仍PENDING。
实际环境Python3.13.14 / WSL2 Ubuntu / Docker29.1.3，Linux虚拟环境独立于Windows。
状态WAITING_REVIEW，技术PASS未替代人工复核。机器证据见verification.json（含命令、结果、版本及源码SHA256）。

## 范围、输入与交付

输入R00-04规则技术基线3728bff。交付pyproject/uv.lock、Python固定版本、应用装配、配置模型、测试分层、README；复核此前本地未跟踪工程，纳入可共享提交。

## 三条DoD与证据

1. 全新Linux虚拟环境执行`uv sync --frozen --python /home/lenovo/.local/share/yuliang-python313/python/bin/python3.13`成功，锁文件未变；核心依赖导入通过。
   Python通过官方发布包在本地准备，目录不入Git；标准uv下载曾缓慢，未降级项目版本。
2. 缺必需配置拒绝启动、配置repr及错误不含密钥、配置类型校验通过；测试隔离开发者.env，不因现有本地配置掩盖缺项。
3. unit/contract/integration按目录标记；严格标记检查，live默认排除，专门哨兵收集测试确认live未运行。

实际`ruff check`、`ruff format --check`、`mypy`全部PASS，17项unit/contract通过，另5项真实integration通过。
本卡不计业务登录、迁移、Worker及门禁完成。无业务迁移，回滚用反向提交；本地配置和虚拟环境不入仓库。
BE复核安装入口与配置，FE需确认公共入口/契约协作范围；人工记录均PENDING。下一项M0-04真实设施验证已独立留证。
