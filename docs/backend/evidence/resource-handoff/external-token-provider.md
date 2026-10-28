# 唯一托管token服务只读客户端

2026-10-28。源码核查：D:/codex/tencent-adq/services/adq_token_service.py 的api_get_token，D:/codex/wx_shop/services/wx_token_service.py 的api_get_token及sdk/wxshop_client.py ServiceTokenProvider。未读取.env、凭据文件或实际token，未启动/停止服务，未发真实请求。

两者均GET /api/token?raw=1，X-API-Key鉴权；ADQ增加account_id。响应ok、masked、access_token、remaining，ADQ另返回account_id。remaining为剩余秒数，而expires_in为原始TTL；客户端以remaining>0判断，不把TTL当剩余时间。GET cache.get()可由服务本身在需要时续期，故此处“只读”指客户端没有刷新/授权管理操作，并不承诺旧服务没有续期副作用。

新增独立ExternalTokenProvider：明确产品和回环IP端口，禁止DNS hostname/远程地址/URL凭据/路径/查询覆盖；密钥和返回token使用SecretStr；HTTP只读GET、5秒超时、禁止环境代理/重定向，不自动重试或强刷；拒绝非200、masked、非正整数剩余时间、ADQ账户不匹配及畸形响应，统一安全错误不包含上游正文。无持久缓存、refresh_token、自有刷新调用和日志。仅工程组件，尚未接入runtime；生产使用前需将具体应用×账户映射绑定私有配置并确认唯一服务部署，不能与native_auth刷新并用。用户已确认两项目归yizhi租户，但不凭租户名推定跨店账户范围。

验证：WSL backend下pytest tests/unit/test_external_tokens.py -q：12 passed，0.39s；ruff check两新文件PASS；mypy src/yuliang/modules/connectors/external_tokens.py PASS。全部httpx.MockTransport，真实平台/旧服务/付费调用0。负责人复核与真实联调PENDING。回滚仅撤销新增组件，不改变旧服务。

## 2026-10-28 受控运行时接线

ApplicationResource新增可选external_token_service（base_url、api_key文件引用、owner_confirmed），默认缺省仍使用原有加密令牌仓储。托管模式必须明确由原服务持有刷新权；token_owner_transferred不能同时为true。禁用资源不读取服务密钥。启用仍要求实际租户/店铺/账户、应用和scope依据，托管服务返回token不自动创建或延长本地授权。

托管配置下驭量拒绝新的本地授权/换码/续期，NativeAuthorization出站前也再次拒绝，避免旁路建立第二刷新者。token_for_read在取令牌前后均检查实际ACTIVE授权、有效期、应用/产品/租户/店铺/原生账户和版本；等待原服务响应时发生撤权或版本变化则拒绝使用。本轮只增加显式可选接线，未修改私有配置、未启用或调用真实服务。

实际验证：30项既有资源与新配置单元通过；5项真实PG集成通过（正常读取、等待期间撤权/版本变化、错误店铺/旧版本出站前拒绝），4.80秒；首次专项因合成catalog_id不符格式在测试准备失败，改正测试夹具后复跑通过。命令：WSL backend下python scripts/test_infrastructure.py --group external-token-runtime --details。托管HTTP为显式替身，真实平台/服务/付费调用0。
