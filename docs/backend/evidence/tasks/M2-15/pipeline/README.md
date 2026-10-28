# M2-15 真实消息管线验收

状态：本地合成真实消息管线技术 PASS，最终 **7 passed/75.99s**，测试隔离库迁移至 `0077_approved_dispatch_worker`。整卡人工验收与真实平台动作边界由主台账维护。

最终实际命令（Windows PowerShell 发起、WSL 执行）：

```powershell
wsl bash -lc "cd /mnt/d/codex/yizhi-yuliang/backend && UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv uv run --frozen python scripts/test_infrastructure.py --group approved-execution-pipeline --details"
```

最终使用正式批准 producer、真实 Rabbit、真实独立 prefork Worker 的 execute/system 队列与真实 loopback TCP HTTP。正式 `handle` 后、ACK 前的 `publish_recovery` 负责初始恢复派发，测试没有手工直接发布恢复事件。两个进程故障点仅用测试屏障，父进程先将既有 guard/recovery/outbox 时钟推进到期后释放子进程退出；Inspector 的 DB 权限、事实核对与收口代码均实际运行。

- 双 Worker、重复投递、批准发布确认丢失、伪造消息拒绝后合法消息分别保持单次物理写；默认空 registry 与当前授权撤销均为零 attempt/物理写。
- prepare 已登记 SENT 后、HTTP 前子进程退出：物理写 0；SYSTEM Inspector 实际依据为 NOT_SENT/E1/network_attempts=0。
- HTTP 已返回、响应落库前子进程退出：物理写 1；SYSTEM Inspector 实际依据为 UNKNOWN/E0/network_attempts=1，未知继续核对，不伪造成功或安全重写资格。此场景另模拟真实恢复 sender 已发送但确认丢失一次，原批准任务重投后仍无第二写。
- 清理后独立核验 7 个自建 loopback 端口均关闭、自建 Worker 进程 0、14 个私有队列与 7 个私有 exchange 均不存在，见 `cleanup.json`。未修改原 token/nginx 等服务，窗口已向主 Agent 明确释放。

历史非最终运行记录保留：首轮 3 failed/163.82s，由本 agent SIGINT 结束后正常清理；安全诊断轮 7 failed/60.21s；单例定位 1 failed/13.84s，原因是测试 Worker 导入既有 pytest fixture 模块，依赖 `state_cases` 的 pytest 路径不在真实 Celery 进程中，已拆为独立测试 DTO/serializer。修后默认关闭单例 1 passed/14.20s；随后整组 6 passed/1 failed/74.99s。该次失败为测试伪造消息的 REJECTED 标记未在随后合法投递前清理，已修后纳入最终 7 项通过。

历史 6 项通过时，`for_job` 只读取恢复 DUE、初始恢复事件未自动发布，故当时未声称 Inspector 已完成。该独立审查缺口已由 owner 与主 Agent 补正式 handoff，并在最终增强 7 项中实际通过。

文件：`backend/tests/integration/test_approved_execution_pipeline.py`、`approved_execution_worker.py`。生产 Worker 使用正式批准事件处理函数；测试入口仅显式替换 `approved_execution_runtime()`，生产默认 registry 仍为空。固定合成 DTO 与固定测试 transport 将已通过生产出口检查的请求转接随机 loopback HTTP 端口，内部使用真实 TCP `AsyncHTTPTransport(retries=0)`，不放宽生产 host/path/签名校验、不启用真实平台 WRITE。

实际七项：A 创建/B 批准的真实批准 producer、双 Worker 单请求与回执；默认关闭持久阻断且零 attempt/HTTP；发布已被 Rabbit 接受但发布确认丢失后的原事件重投；伪造信封实际被拒；当前平台授权撤销；SENT 准备已提交后子进程退出；实际请求返回而响应未落库前子进程退出。后两项均通过真实 SYSTEM Inspector，无第二写。

SENT 与发送资格分别核对：0032 的 prepare 已登记 SENT，`dispatch_consumed_at` 在后续出站准入时写入。不能把前者称为 PENDING，也不能把无响应记录当作平台成功。HTTP handler 用独立数据库连接核验 SENT 与已消耗资格在实际请求到达前已提交。

静态：两文件 Ruff 和 Python 编译通过。独立审查曾发现 0076 producer 与 0077 派发记录对 aggregate_type 要求不一致，owner 已统一并经过最终实际链验证。

人工签字、真实 WRITE source profile、正式平台动作适配与真实平台验收继续保留。本切片不付费、不调用真实平台。
