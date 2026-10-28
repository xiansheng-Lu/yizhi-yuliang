# M4-06a 固定版本发布与幂等

2026-10-28，BE/F08，WAITING_REVIEW（已实现范围的本地技术验证）；依赖M4-05真实PG/HTTP草稿与版本历史技术证据，人工E08门禁保留。用户已授权持续研发。本批本地分支codex/backend-comparison-assets，以main739b721为基线；主Agent实施，0058—0061迁移与其他卡测试串行。

范围与交付：0061_report_publications、新publication服务/路由与显式公开源配置。POST /v1/business-analysis/drafts/{id}/publications仅接version/idempotencyKey；GET publications列表与按ID读取，读取范围继承原分析账户、当前成员和角色。草稿FOR SHARE锁与发布键事务锁序列化版本绑定，原键重试返回原publication；改键或改版不会篡改历史。发布snapshot、版本、origin、nonce/能力摘要与时限不可变，仅允许一次吊销状态变化。原草稿后续修改不改变公开报告或导出。

三条DoD：同版本同键并发与重试同一记录，冲突键/旧版本受控拒绝；修改草稿产生新版本且旧发布不变；当前scope拒绝越权，返回后端显式配置的HTTP源与实际路由。客户端不能传origin、token、过期时间或替换快照。真实loopback HTTP及PG/Redis专项已运行通过，具体输入/结果见verification.json。

本地HTTP可访问和配置格式校验不证明公网DNS/TLS部署。默认未配置origin与独立链接密钥时发布能力关闭，返回明确CAPABILITY_UNAVAILABLE，无假客户链接。生产需可信HTTPS源、流量入口与独立密钥实际配置，随后验证可达性；本卡不擅自部署。

回滚：停用公开配置/发布路由，保留不可变记录；不破坏性downgrade。人工BE/F08复核PENDING，正式DONE不由Agent代签。检查与精确结果见verification.json及本批整合证据。

## 最终实际验证

真实PG/Redis/HTTP专项6项通过，13.91秒；安全/配置/转义单元7项通过，0.50秒。实际监听loopback的uvicorn经无Cookie httpx客户端200读取；并经现有ApiClient/ApiAnalysisAdapter消费PG产生的发布记录，发布/详情/历史均通过。TS传输重放4次fetch，无额外真实网络。当前origin服务仅本机验收，不声称公网可用或实际浏览器UI验收。完整命令、负例与边界见verification.json；人工BE/F08/F13仍PENDING。
