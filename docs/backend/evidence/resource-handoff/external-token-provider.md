# 唯一托管token服务只读客户端

2026-10-28。源码核查：D:/codex/tencent-adq/services/adq_token_service.py 的api_get_token，D:/codex/wx_shop/services/wx_token_service.py 的api_get_token及sdk/wxshop_client.py ServiceTokenProvider。未读取.env、凭据文件或实际token，未启动/停止服务，未发真实请求。

两者均GET /api/token?raw=1，X-API-Key鉴权；ADQ增加account_id。响应ok、masked、access_token、remaining，ADQ另返回account_id。remaining为剩余秒数，而expires_in为原始TTL；客户端以remaining>0判断，不把TTL当剩余时间。GET cache.get()可由服务本身在需要时续期，故此处“只读”指客户端没有刷新/授权管理操作，并不承诺旧服务没有续期副作用。

新增独立ExternalTokenProvider：明确产品和回环IP端口，禁止DNS hostname/远程地址/URL凭据/路径/查询覆盖；密钥和返回token使用SecretStr；HTTP只读GET、5秒超时、禁止环境代理/重定向，不自动重试或强刷；拒绝非200、masked、非正整数剩余时间、ADQ账户不匹配及畸形响应，统一安全错误不包含上游正文。无持久缓存、refresh_token、自有刷新调用和日志。仅工程组件，尚未接入runtime；生产使用前需将具体应用×账户映射绑定私有配置并确认唯一服务部署，不能与native_auth刷新并用。用户已确认两项目归yizhi租户，但不凭租户名推定跨店账户范围。

验证：WSL backend下pytest tests/unit/test_external_tokens.py -q：12 passed，0.39s；ruff check两新文件PASS；mypy src/yuliang/modules/connectors/external_tokens.py PASS。全部httpx.MockTransport，真实平台/旧服务/付费调用0。负责人复核与真实联调PENDING。回滚仅撤销新增组件，不改变旧服务。
