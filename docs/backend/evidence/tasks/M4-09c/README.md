# M4-09c 分析与SOP事件技术切片

2026-10-28，BE；基线main591447df0a4d4b102d66143a3d5e5e4960a3be8f，本地codex/backend-workflow-execution-links-events。整卡IN_PROGRESS；此切片TECHNICAL_VERIFIED，不等于50连接/代理/F12或内容生成全事件通过。readiness.md为先前PLANNED阶段记录。

0068_content_analysis_events（down0067_content_account_links）新增：

- analysis_task×analysis.changed：真实任务插入及status/progress/result_id/error变化同事务产生通知，独立通知revision。重复受理/终态重放/租约续期不捏造阶段或新通知；成功结果由原GET任务/结果读取，不广播正文或快照。
- workflow_run×workflow.changed：基于0066实际run.revision/status变化，客户端重读真实run；保持团队当前overview.read与shop/product/account双scope，不将created_by当唯一读者。
- 源/投影均有显式真实analysis_task_id/workflow_run_id及tenant复合FK，两类job_id为空，不伪装command_job。源还通过实际user/creator复合FK绑定owner。当前RLS委托真实analysis_visible或workflow当前product/shop/account权限；原5类事件继续走既有权限函数和提交序投影。
- 共享platform/events.py仅由主Agent追加两类entity/topic，payload仍为status提示+资源ID/通知版本/时间，不包含事实、比较快照、lease/token、错误正文。无需新HTTP端点或修改api/events.py路由。

已完成6项单元0.63秒，专属文件Ruff及0068迁移Python mypy通过。获主Agent本轮明确唯一DB窗口后，实际PG/loopback SSE专项首轮7 passed/12.22秒，隔离空库真实执行head0068升级，随后明确释放DB并冻结代码。

实际覆盖：分析受理与10/40/75/90/100执行阶段形成6条独立revision通知、真实冻结结果可重读；相同受理/终态重放不重复；任务与通知共同回滚不跳版本，覆盖失败只产生一次FAILED；跨owner/跨租户与范围撤销过滤旧事件；app_login直接伪造源与resource head均42501拒绝。workflow真实可选人工skip使run revision1→2，重放不再更新，商品软删除后当前RLS不再返回历史提示。两类专用topic通过真实uvicorn/loopback HTTP+Cookie+Last-Event-ID续传，商品删除后实际AUTHORIZATION_CHANGED resync并关流。此为合成身份/数据与真实本地PG/SSE，不是真实平台业务或浏览器页面验收。

共享权限指纹由主Agent追加commerce_products的id/shop/account/deleted_at与entity_links双端ID，不包含名称/PII。analysis_task通知的version是独立资源提示revision，原Task HTTP模型没有version字段，不能拿progress充当版本；workflow通知version与真实run.revision对应。F12去重后需重新查询任务/冻结结果或run，当前尚未接客户端。

实际命令（WSL backend目录，UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv）：

- `uv run --frozen pytest tests/unit/test_content_analysis_events.py -q`
- `uv run --frozen python scripts/test_infrastructure.py --group content-analysis-events --details`
- `uv run --frozen ruff check alembic/versions/0068_content_analysis_events.py tests/integration/test_content_analysis_events.py tests/unit/test_content_analysis_events.py`
- `uv run --frozen mypy alembic/versions/0068_content_analysis_events.py`

原事件生产切片无真实平台/模型/付费调用，不以X-Accel-Buffering头代替代理验证；本地真实代理/50连接本批结果见下。F12页面、生产TLS、全内容/生成事件和具名人工复核仍待完成，整卡保持IN_PROGRESS。

## 共享权限指纹补项复验准备

保留以上首轮7 passed/12.22秒历史证据。主Agent追加shops.status/platform与accounts.provider/product_line至权限指纹后，本卡新增2项确定性真实SSE缓冲页回归，当前共9专项已获本轮独占DB授权并完成复验。测试先调用真实poll得到可见analysis任务页，再通过barrier将shop状态改为UNKNOWN或将合法无依赖账户的provider/product_line改为ocean/qianchuan，要求已缓冲事件在发帧前也被拒绝，只resync并关流。账户样本调用实际受控analysis_admit冻结scope，不伪造通知，不禁用FK；其裸账户无指标覆盖且不执行计算，仅验证权限变化。新增测试静态Ruff PASS；本轮9 passed/14.75秒，包括两个已poll缓冲页变更后仅resync并关流、无旧analysis.changed泄出的真实HTTP断言。原7项亦复验通过，窗口已明确释放，测试冻结。0068与共享源码未由本Agent修改。

## 本批本地代理与50连接专项实现

基线main0c613c4f9dc40b87e65383844f799e59403e64ab，新本地codex/backend-ai-content-generation。正式实现test_sse_proxy_load.py与sse_proxy_harness.py，收集3项测试、Ruff通过、`MYPYPATH=src uv run --frozen mypy --follow-imports=silent tests/integration/sse_proxy_harness.py tests/integration/test_sse_proxy_load.py`本两文件通过。本轮获唯一DB/本地进程授权后，3专项真实执行通过77.36秒，随后明确释放DB窗口并冻结。

测试使用本机nginx独立临时prefix与私有回环高端口，默认实例、8080和原token服务不动。包含正常代理与明确1byte/s限速负控制（首轮buffer开关假设不成立，见实测）；50条真实TCP流持续65秒、原生产25秒心跳/1秒poll不加速，实际业务提交与100个应见提示、首帧/增量延迟、断线续传且不新增业务任务、无Cookie/错误Origin拒绝。只读采样隔离库pg_stat_activity及自己nginx子进程/应用与客户端进程的CPU/RSS/FD和实际socket数；另验代理后的buffered撤权及workflow续传。配置、pid和日志仅测试私有临时目录，finally回收自己创建的进程与端口，不重载系统服务。

本轮空库实际升级至冻结head0071并执行`sse-proxy-load`组。设置`YULIANG_SSE_PROXY_EVIDENCE`才输出脱敏fifty-streams.json、buffered-revocation.json、workflow-proxy-resume.json；无Cookie、秘密或完整业务正文。生产TLS/F12/真实多用户和全内容生成事件仍另列，不能扩大成“本地load无法做”，也不能因本地未来通过而冒充它们完成。


## 本地真实代理/50连接实际结果

- WSL原生nginx1.28.3，私有临时prefix/高端口→私有uvicorn→真实隔离PG；3 passed/77.36秒。50条真实下游TCP及50上游TCP连接持续65.289秒，共用1个合成身份，**不是50独立商家/用户容量**。
- 每流至少2次生产25秒心跳，实测心跳间隔25.263–25.352秒。首帧p95 0.744秒/max0.811秒；真实业务调用开始→完整提示帧p95 1.229秒/max1.237秒。50流各收到2条实际AnalysisService任务通知，合计100条；原cursor续传只收到断开后的新任务，不重建业务任务。代理后的workflow真实创建/续传与buffered撤权resync关流通过。
- 13次资源采样：PG应用/认证连接峰值12，超过2秒idle-in-transaction为0，最长事务年龄0.0091秒；nginx worker RSS峰值4552KiB，合并应用+负载客户端测试进程RSS峰值170076KiB/FD峰值142。它们是观测值，不冒充纯应用内存或生产容量上限。
- 正常首帧直接测完整增量bytes，未用响应头或配置文本代替flush。首次负控制假设“buffering on且忽略XAccel必然阻塞首comment”实测不成立（1 fail/2 pass，9.70秒，尚未进入50流）；保留该事实，后改为明确nginx 1byte/s限速负控制并成功检出超时，准确标为限速检测，未放宽正常路径门槛。
- 只终止helper自己创建的Popen句柄，私有lifecycle/cleanup记录留在测试临时prefix；自身nginx/uvicorn退出，正常/故障/上游端口全部关闭。只读复核原有nginx七进程集合与盘点一致，未重载或停止已有实例、8080或原token服务。DB窗口已释放。
- 最终专属Ruff及targeted两文件mypy通过。数据见proxy-load/summary.json、fifty-streams.json、buffered-revocation.json、workflow-proxy-resume.json，只有合成ID/版本/脱敏测量，不含Cookie/secret/业务正文。

执行命令：`YULIANG_SSE_PROXY_EVIDENCE=/mnt/d/codex/yizhi-yuliang/docs/backend/evidence/tasks/M4-09c/proxy-load UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv uv run --frozen python scripts/test_infrastructure.py --group sse-proxy-load --details`（WSL backend目录）。生产TLS、F12浏览器、真实多用户及全内容生成事件继续PENDING/NOT_RUN，整卡IN_PROGRESS。

本批最终完整整合：1009集成通过、1可选重启跳过（953.14秒）；952单元/契约、240源码mypy/Ruff及0069至0072迁移Ruff通过，冻结源码哈希一致。开发库0072、48相关表仅5系统模板其余0，迁移角色NOLOGIN。详见../../ai-content-generation-verification.json；整卡未完成范围与BE/FE具名复核仍按上列保留。
