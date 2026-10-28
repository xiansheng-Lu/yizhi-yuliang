# M4-06b 公开阅读、吊销、过期和导出

2026-10-28，BE/F08/F13，WAITING_REVIEW（已实现范围的本地技术验证）；承接本批M4-06a实际技术验证，人工E08与F08/F13复核PENDING。范围为随机能力链接、无Cookie HTML、显式吊销、服务端期限、Redis有界访问与已发布版本HTML导出，无平台/模型/供应商调用。

256位随机nonce加独立链接密钥以HMAC-SHA256生成256位不可预测能力；数据库只存nonce与token的SHA256，持有数据库摘要不能重建链接。令牌同时绑定publication ID；原密钥用于认证管理端重建同一链接，轮换密钥前必须保留既有密钥。更换密钥会导致旧记录管理端链接重建明确失败；已有匿名URL仍按原摘要、吊销/到期检查，不悄悄更换公开版本。当前为单密钥配置，轮换keyring另行设计。

公开路径/reports/public/{id}?token=...在ASGI入口即去query，只以SecretStr传内部状态；UUID路径不含令牌。代理/访问入口也应避免记录原query，应用不能替外层代理保证日志。匿名仅能得到选中可见模块的纯文本、指标与冻结逐日趋势表，不输出原始分析ID、店铺/账户目录、隐藏模块、素材私链或管理接口。标题、名称、正文、日期和所有值转义，CSP default-src none、nosniff、no-referrer、noindex及no-store，无外部资源。GET /v1/business-analysis/publications/{id}/export要求真实身份并下载同一不可变HTML。

公开链接属于作者主动发布的能力；退出登录/撤销管理权限不自动回收已交付链接，需显式吊销或期限到达。POST /publications/{id}/revoke要求Cookie、CSRF、原作者与当前发布权限。吊销/过期/错误token统一404；Redis按真实连接IP的独立审计HMAC摘要限流，无客户端X-Forwarded-For信任，限额默认每分钟60，超限429+Retry-After，Redis故障不放行。反向代理部署需要真实可信入口设计，不能以本地结果代替。

三条DoD：无Cookie新客户端读取固定版本及吊销/过期后拒绝；token仅解锁对应HTML且不能复用到管理/目录；限流/恶意标签转义/HTML导出与固定版本正确，系统凭据不进入报告。实际专项6项通过，精确范围与结果见verification.json。正文由作者编辑部分仍需BE/F08/F13内容复核，不自动公开内部运营秘密。

回滚以前向关闭入口或逐条吊销，保留发布与草稿历史。真实公网浏览器/TLS、前端完整编辑发布交互和具名复核尚未验收。

## 最终实际验证

真实PG/Redis/HTTP专项6项通过，13.91秒；安全/配置/转义单元7项通过，0.50秒。实际监听loopback的uvicorn经无Cookie httpx客户端200读取；并经现有ApiClient/ApiAnalysisAdapter消费PG产生的发布记录，发布/详情/历史均通过。TS传输重放4次fetch，无额外真实网络。当前origin服务仅本机验收，不声称公网可用或实际浏览器UI验收。完整命令、负例与边界见verification.json；人工BE/F08/F13仍PENDING。
