# M4-09c 本地真实代理与50连接SSE验收准备

2026-10-28；**PLANNED / NOT_RUN**。仅只读盘点并写本文件；未启动/重启/重载代理，未拉镜像，未运行数据库/迁移/集成测试，未改源码、现有tests、0068、部署配置或Git。root当前独占最终完整回归session96111。本计划须本批合并后在新本地分支、明确文件归属与独占DB窗口下实施。

## 已观察到的本地条件（不是代理验收）

- 当前仓库部署入口为`backend/docker-compose.yml`，仅postgres/redis/rabbitmq/minio四类基础服务及回环端口，没有本项目nginx反向代理服务或已提交nginx.conf。`backend/deployment`主要为基础设施初始化及私有env，未读取env值。
- WSL实际存在`/usr/sbin/nginx`，`nginx -v`为nginx/1.28.3 (Ubuntu)；构建输出OpenSSL 3.5.5。`ps -C nginx`显示已有nginx进程，回环80端口有监听，**归属未认定，不能重载、停止或复用其配置**。
- WSL `/usr/bin/docker`可读，客户端/服务端均29.1.3。已缓存`nginx:1.27-alpine`镜像，image ID为`sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10`。不需要为本地代理测试联网拉镜像；优先使用原生nginx独立prefix。
- 本机软FD限制`ulimit -n`实际10240。全局nginx.conf中的worker_connections为768；这只是现状，不证明50连接端到端可用，隔离实例将有自己的配置和观测。
- `nginx -T`读取全局配置时报告语法ok，但因普通用户无法打开`/run/nginx.pid`而整体test failed。本次没有修系统目录或提升权限，不能把该结果记为配置PASS。新隔离prefix应使用可写的私有pid/log路径，避开现有实例。
- 当前项目四个基础容器处于运行/healthy状态；仅查看状态，没有exec、SQL、启停或修改。现有最终回归正在使用它们，本轮不占用。

盘点使用`rg --files`/`Get-ChildItem`、读取compose，WSL的command -v/nginx -v/-V、ps、ss、ulimit，以及docker version/image inspect/ps。容器状态、配置与响应头均不构成代理链、flush或负载测试。

## 隔离实际链路

拟链路：50个独立HTTP流 → **测试私有nginx回环端口** → 测试私有uvicorn回环端口 → 实际create_app/认证/SSE/CoreEventStream → runner隔离测试库和既有基础设施。

1. 由测试创建随机私有临时目录与两个未占用高端口；所有监听明确127.0.0.1，不用全局80、不绑定0.0.0.0，不改/etc/nginx，不复用现有nginx master。应用复用实际测试Settings及合成身份/业务数据，Cookie由真实登录生成；仅测试私有Origin白名单，不扩大生产CORS。
2. 原生nginx以独立`-p` prefix、`-c`配置启动；pid/error/access log全部在测试目录，无全局user/include指令。使用受测试控制的subprocess、记录自己PID，只终止自己创建的进程，finally检查回收。Windows若需要Start-Process必须Hidden；优先在WSL测试进程内管理，不弹窗。
3. 拟配置只代理既有`/v1/events`，上游HTTP/1.1，清除Connection跳头，保持Cookie/Origin/Last-Event-ID；`proxy_buffering off`、`proxy_cache off`、gzip off、read/send timeout 90秒，worker_connections至少256。这些是待实施参数，**当前没有配置文件落盘或语法/真实验证**。
4. 50个下游SSE连接同时占用约100个nginx上下游socket，再留控制请求余量；客户端连接池大于50，业务控制调用使用独立池，避免“负载”只是客户端等待可用连接。记录50个实际连接均收到完整首帧的时间，而不是只统计已创建协程。
5. 若原生nginx隔离启动不可用，备用为已缓存image ID的新私有容器，不拉镜像、不改现有compose；仍仅回环发布端口。容器到回环uvicorn的可达路径必须实际确认，不能默认Docker能访问宿主127.0.0.1。优先原生方案避免该不确定性。

## 实际场景与建议通过门槛

以下门槛是拟定的本地工程验收预算，尚未执行；若不满足先报告测得瓶颈，不能静默放宽后记PASS。

| 场景 | 实际步骤 | 必须观察的事实 / 建议门槛 |
| --- | --- | --- |
| 空范围首帧flush | 当前真实cursor下无新事件，分别直连与经过代理建立流，读取原始增量bytes并解析完整`: connected`帧 | 两路径都无需等待25秒心跳；代理50条全部收到，首帧p95≤2秒、最大≤5秒；保留逐连接耗时与真实路由，不以200或响应头代替 |
| 缓冲负控制（建议） | 独立故障配置开启buffering并忽略上游X-Accel-Buffering，仅测试首帧短deadline，之后销毁故障实例 | 测试应能检出延迟；不能把上游响应头自动关闭缓冲的情形误当“buffering on负例”。负例为预期失败证据，不混入正常通过计数 |
| 50条并发持续流 | 50个真实SSE HTTP连接全部首帧后持续至少65秒，生产POLL_SECONDS/25秒heartbeat不加速；明确是50条TCP流，可共用同一合成登录身份，不冒充50租户 | 每连接至少2个真实心跳，无意外EOF/5xx/超时/缺帧；报告实际峰值连接数、心跳间隔分布（拟最大35秒）、存活数/失败数及时间跨度 |
| 真实业务事件 | 合成fixture经实际AnalysisService/受控执行器及WorkflowService/人工动作提交queued/阶段/成功、run revision变更；禁止直接插通知源 | 授权50流可按真实entity/revision重读对应任务/结果/run，版本顺序正确、无跳过应见事件、原键重放不重复通知；事件到达p95≤3秒、最大≤5秒（记录控制调用时间括号与到帧时间，不把它伪称精确commit延迟） |
| 断线续传 | 保存实际最后id，关闭部分客户端；通过真实业务生产缺口事件，再经代理携带Last-Event-ID重连 | 只续传之后应见事件，去重按entity+通知revision，cursor单调；不重放任何执行写。analysis通知revision不冒充Task.progress，workflow通知版本核对run.revision |
| 保留边界 | 复用真实保留/游标隔离夹具制造旧cursor/跨租户/超前cursor，再经代理读取 | resync原因与当前tenant cursor正确，不能用直接清空表绕过留存策略并伪称生命周期验证 |
| 既有权限及buffered撤销 | 复用当前已验证的poll→barrier→scope变化方法，经代理测试shop状态失效、账户平台线变化或产品删除；并测试注销 | 已缓冲旧权限事件也不能发出，仅合法resync/关流；真实当前重读不可见。记录从撤销操作到流关闭耗时；另一个仍授权控制身份不能被全局误关 |
| 错误隔离 | 未登录/错误Origin/跨owner或跨租户身份分别经过同代理 | 不得由代理复用Cookie或缓存他人事件；实际HTTP状态/关流和无敏感帧，不能只测后端直连 |

读取客户端应使用httpx实际stream/aiter_raw及增量帧解析，并以monotonic记录完整帧到达时间；不要先缓存整个无限响应，也不要把配置文件中“off”当flush证明。禁止在access log记录Cookie、Authorization、完整query或业务payload；证据只保留合成ID、事件种类/版本、连接索引、脱敏请求ID及时间指标。

## 资源、数据库与可核对指标

- 开始前检查自身私有端口可用、nginx/uvicorn PID、配置hash、二进制实际版本；记录测试唯一run ID、head迁移和精确命令。测试结束确保50客户端、自己创建的两个服务与临时日志均被可控回收，不影响现有nginx或基础容器。
- 用只读系统观测记录测试进程RSS/CPU/FD、自己的已建立上下游socket数、客户端当前/峰值连接数、应用连接池timeouts和HTTP失败数；不读取其他进程env或服务secret。
- 获DB窗口后才能只读采样该隔离库pg_stat_activity：连接state计数、idle in transaction计数和最长事务年龄，不收集SQL正文/连接密码。预期SSE空闲时没有持续idle in transaction；数据库连接应回到池内，不能把50个长期流误实现成50个长期数据库事务。按实际pool上限核对连接峰值，不能未经测量宣称“50连接无资源增长”。
- 输出机器可核对summary.json与逐连接timings.jsonl：目标/实际并发、成功首帧数、首帧p50/p95/max、每流心跳数/最大间隔、事件应见/实见/重复数、续传缺口、撤权泄漏计数、unexpected EOF/timeout/5xx、资源采样、最终清理结果。敏感响应体不入证据。
- 当前只证明存在本地运行条件，所有上述测量均NOT_RUN。后续应先专项、再由主Agent统一必要回归/全量；不与他人DB测试或迁移并行。

## 可以本地推进与仍缺外部验收

本机已有nginx与缓存镜像、可用FD预算和实际SSE后端，本地真实代理/50连接验收**具备实施条件**，没有生产TLS或F12也能做，不能据外部缺口停在静态调研。

仍需另列：生产Ingress/反向代理真实配置、HTTPS证书/域名、跨地域网络与真实多用户容量、F12浏览器SSE接线与退避/轮询UI。它们不阻塞本地loopback验收，但本地通过也不能替代这些项目。M3-20b生成全事件尚未完成，M4-09c整卡继续IN_PROGRESS；此文件仅PLANNED，不填具名签字或代理/负载PASS。
