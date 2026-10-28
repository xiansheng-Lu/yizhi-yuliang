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

当前外部范围：无真实平台/模型/付费调用。未改变代理配置；X-Accel-Buffering头不等于真实代理无缓冲验收。50连接、代理/心跳/断线补偿组合、F12页面、M3-20b内容/生成全事件和具名人工复核仍待完成，整卡保持IN_PROGRESS。

## 共享权限指纹补项复验准备

保留以上首轮7 passed/12.22秒历史证据。主Agent追加shops.status/platform与accounts.provider/product_line至权限指纹后，本卡新增2项确定性真实SSE缓冲页回归，当前共9专项已获本轮独占DB授权并完成复验。测试先调用真实poll得到可见analysis任务页，再通过barrier将shop状态改为UNKNOWN或将合法无依赖账户的provider/product_line改为ocean/qianchuan，要求已缓冲事件在发帧前也被拒绝，只resync并关流。账户样本调用实际受控analysis_admit冻结scope，不伪造通知，不禁用FK；其裸账户无指标覆盖且不执行计算，仅验证权限变化。新增测试静态Ruff PASS；本轮9 passed/14.75秒，包括两个已poll缓冲页变更后仅resync并关流、无旧analysis.changed泄出的真实HTTP断言。原7项亦复验通过，窗口已明确释放，测试冻结。0068与共享源码未由本Agent修改。
