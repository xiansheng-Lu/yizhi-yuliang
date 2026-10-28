# M4-05 报告草稿、版本与安全预览

2026-10-28，BE/F08，WAITING_REVIEW（本地技术验证），依赖M4-03实际24项本地设施验证；人工E08复核保留。

计划/范围：0057_report_drafts下接0056_lookup_target_assets；analysis/drafts_models.py与drafts_service.py，现analysis.routes追加冻结客户端路径。GET drafts数组/详情、PUT版本、预览HTML；不涉及发布URL或公开访问。只允许编辑title/modules；analysisId/query/周期与冻结指标、序列、章节、洞察、coverage/attribution不可改写。

受控函数验证同用户/租户/当前scope与来源结果，按草稿ID串行和version CAS；新草稿version0→1，冲突409返回完整current。每成功版本同事务追加不可变history；运行身份无任意草稿/历史写权限。跨用户/租户/范围拒绝。固定HTML模板对标题、模块纯文本、指标名称/数值、日期和元数据全部escape，CSP default-src none，无动态脚本/外链执行。

三条DoD对应：真实分析结果后创建草稿并核对源字段/周期；两写者同版本只一端成功且历史不丢；编辑只改草稿，冻结来源不变，HTML恶意标签不执行。当前单元1项通过，真实PG/HTTP草稿专项3项通过；analysis24项回归通过。模型/平台/付费调用0；BE/F08签字PENDING。回滚以前向停用草稿写路由，保留历史，不破坏性downgrade。

## 实际验证与证据

WSL，UV_PROJECT_ENVIRONMENT=/home/lenovo/.cache/yuliang-venv。

- uv run --frozen python scripts/test_infrastructure.py --group report-drafts --details：3通过，9.96秒。
- uv run --frozen python scripts/test_infrastructure.py --group analysis --details：24通过，49.50秒。
- uv run --frozen pytest tests/unit/test_report_drafts.py -q：1通过，0.41秒。
- Ruff相关源码/测试通过；mypy分析11源码通过。

真实AnalysisService→AdqReportSync规范来源→Worker计算冻结结果后建立草稿，HTTP保存version1、重复旧版本409附完整current；两个真实并发事务同version只有一胜，历史2版可核对。修改coverage伪造分析证据被拒绝，原草稿不变；移除scope后读取拒绝。标题/正文script/img/iframe/svg等作为转义纯文本，响应CSP default-src none及nosniff/private no-store。报告周期来自原query，资金/模型/平台调用0。

边界：固定预览当前呈现所选模块、指标及纯文本，无公开URL/发布令牌；发布能力归M4-06a/b另卡。前端草稿副本保留/冲突交互仍需F08联合复核，本地技术通过不代签。下一READY M4-06a，已只读核对客户端发布契约，当前批次冻结后再切分支实施。

最终安全负例：0057身份字段比较改IS DISTINCT FROM，实际app_login直接调用受控SQL时schemaVersion/source/id/analysisId/version各自missing与JSON null共10种输入全22023拒绝，草稿及history均0行。uv run --frozen python scripts/test_infrastructure.py --group report-drafts --details最终4通过10.40秒。不是仅HTTP/Pydantic拒绝。
