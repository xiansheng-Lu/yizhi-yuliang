# M0-02 信封、错误与请求关联

日期：2026-10-27（Asia/Shanghai）；代码基线 `c95a4f85ac095f38fea2d28afd4ab4eb3d98f998`；本地分支feat/backend-wsl-foundation。
负责人BE；用户明确继续开发并提供WSL Docker。工程采用R00技术基线，双端签字仍PENDING。
实际环境Python3.13.14 / WSL2 Ubuntu / Docker29.1.3，Linux虚拟环境独立于Windows。
状态WAITING_REVIEW，技术PASS未替代人工复核。机器证据见verification.json（含命令、结果、版本及源码SHA256）。

## 范围、输入与交付

输入M0-01工程技术基线。交付统一成功/失败信封、错误码映射、请求关联及契约测试。
前端协作F01，未修改前端源码，未宣称新增CSRF或异步素材已接线。

## 三条DoD与证据

1. 契约测试覆盖HTTP200/202及null；外层command-job-v1.0，内层advertising-v9.0不被抹平；退出保持200 JSON。
2. 422/409/423/429受控业务码、details及Retry-After验证通过；写请求不因retryable诱导自动重放，读取限流可重试。
3. 未知异常不反射连接串/堆栈；服务端生成requestId覆盖客户端伪造值，响应头/响应体/结构日志可关联；输入校验不反射敏感值。

`tests/contract/test_envelope.py`覆盖10项契约用例，包含在本轮17项unit/contract PASS中；ruff/mypy通过。
仍有一条TestClient弃用警告，未影响断言。无数据库迁移；回滚为反向提交装配/信封模块。
FE/BE需复核错误展示及真实客户端接线；浏览器联调NOT_RUN，保持WAITING_REVIEW。
