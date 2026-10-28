# 当前结论（2026-10-28 17:18更新）

用户批准初始化及迁移后，缺失安全配置已补齐、旧缓存已认证加密迁移，唯一127.0.0.1:8099原服务health200/ok。原SDK真实店铺info、1页商品、最多1详情及昨日1天罗盘共4次均errcode0，强刷/业务写0；仅保存结构与安全计数。见[完整执行结果](security-initialization-result.md)及[实际只读JSON](wxshop.json)。下述17:03阻塞是历史检查点，已解除，不作为当前阻塞。

---
# 微信小店真实只读核验检查点

2026-10-28 17:03起（Asia/Shanghai）。用户已明确允许两个原项目的只读接口调用；本轮没有把研发授权与真实写入/付费许可混同。

当前没有发生平台请求。原wx_shop `.env`监听端口为8099，未显式配置WX_TOKEN_SERVICE_URL；127.0.0.1:8099不可达。另一个8080监听经Windows/WSL进程路径核对属于`/home/lenovo/yuliang-oauth-callback/app.py`回调服务，不是微信小店token服务，未误用。flask、requests、cryptography依赖可导入。

核对安全版`services/wx_token_service.py`及`cache/security.py`，启动必须有WX_TOKEN_API_KEY，缓存加密必须有TOKEN_ENCRYPTION_KEY_FILE/TOKEN_KEY_VERSION/TOKEN_TENANT_ID；原项目.env与本次继承环境这四项均缺失。服务源码108行在缺API key时即拒绝，故未重复启动已知会失败的服务，未创建新秘密、未降级鉴权、未读取/转换旧token缓存。ProcessOwner对同缓存路径持有进程终身OS锁，第二实例拒绝；未运行第二刷新者。父任务随后授权必要时以原loopback配置恢复唯一服务，但未授权绕过缺失私有安全配置。

需要的下一步是提供已有安全配置引用，或明确授权按安全部署手册初始化密钥及对旧缓存执行显式迁移。无需重新申请已获准的业务只读权限。此前原项目已调用开发者接口的历史事实与当前安全版token服务未运行分别记录，不能用历史汇总页面证明当前认证成功。

已准备忽略目录`.runtime/wxshop_readonly_probe.py`，计划仅经已鉴权唯一服务GET /api/token?raw=1取得内存令牌，再使用原sdk.wxshop_client的实际call协议：店铺info、商品列表page_size=1、至多1个商品详情、昨日1天罗盘overall。关闭SDK自动重拉token、所有HTTP禁止重定向；不创建直连token provider、不force_refresh。无原项目或冻结工程代码变更。

只保存字段结构、业务errcode、请求ID及安全计数；PII/真实业务字段值/商品店铺ID/token/secret不入证据。当前上述实际请求均NOT_RUN，平台调用0、强刷0、服务生命周期变更0、DB改动0。该核验即使未来成功，仍是原项目SDK真实只读，不自动等于驭量runtime业务绑定与G1验收完成。

官方商品详情及罗盘overall文档本次再次web open失败，未取得正文；本地SDK/collector可证明调用形状，不能据其金额换算代码确认官方单位、退款/时区/去重语义。相关来源仍保留独立口径依据缺口。
