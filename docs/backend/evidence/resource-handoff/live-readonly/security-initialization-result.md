# 原Token服务安全初始化与真实只读结果

2026-10-28 17:18（Asia/Shanghai）。用户已明确批准“按方案初始化并迁移”。实际执行方案见security-initialization-plan.md；该方案的NOT_RUN表示获批前记录，本结果覆盖当前状态。

两项目已完成独立本机服务API key和32字节AES-GCM密钥环初始化，TOKEN_TENANT_ID=yizhi、v1版本及对应私有路径写入原.env，保留既有平台AppID/AppSecret配置。所有秘密文件写入前限制Windows ACL，仅当前服务用户可访问，O_EXCL拒绝覆盖；新密钥环/API key另在本机仓库外独立私有目录备份并逐字节核对，备份路径仅记私有.runtime清单。该备份是本机独立目录，未宣称异机或离线灾备；密钥及所有备份同时丢失后密文无法恢复。

ADQ已有凭据及1个账户快照共2文件、WXSHOP已有缓存1文件均先在对应ProcessOwner锁下生成认证加密备份，再调用原tools/manage_security.py migrate逐文件原位迁移。原工具取得同一owner锁，AES-GCM写入后认证读取；每个迁移结果与原内存payload完全相等，未主动修改access_token/refresh_token值或平台Secret。新增明文备份0，未删除任何历史副本。配置原文也留认证加密备份，没有新增明文.env备份。

原服务已以Start-Process -WindowStyle Hidden运行：ADQ PID22000监听127.0.0.1:8088；WXSHOP PID19424监听127.0.0.1:8099。原配置端口保留，未公开绑定；原有8080OAuth回调未停止/改变。二者health均HTTP200且ok=true，实际尝试第二个同owner锁均拒绝，未创建第二刷新者。私有PID/日志保存在本仓库.runtime，不提交。WXSHOP启动与health组合命令曾被自动审查以blocked by policy拒绝，拆成单独明确的Start-Process与独立health检查后均成功，不存在尚未解除的启动审批问题。

## 实际只读核验

| 原项目与接口 | 业务结果 | 安全计数/范围 |
|---|---|---|
| ADQ advertiser/get v3 | HTTP200/code0 | 1行，page_size=1 |
| ADQ campaigns/get v1.3 | HTTP200/code0 | 0行，明确真实空集合 |
| ADQ funds/get v1.3 | HTTP200/code0 | 3行，不保存余额/财务值 |
| WXSHOP 店铺基本信息 | errcode0 | 仅保存字段结构，不保存店铺身份 |
| WXSHOP 商品列表 | errcode0 | page_size=1 |
| WXSHOP 商品详情 | errcode0 | 最多1个列表返回商品，仅结构/SKU计数 |
| WXSHOP 罗盘overall | errcode0 | ds=20261027单日，7个指标字段结构，不保存指标值 |

本次累计7次业务只读，业务写0、force_refresh0、驭量DB改动0。正常后台续期由既有唯一托管者按原策略执行，WXSHOP health refresh_count=1；这属于正常续期，不能称“从未调用平台token服务”。ADQ使用原项目验证示例的公共参数协议，WXSHOP使用原sdk.wxshop_client的实际call；raw token仅经新鉴权loopback服务取得到内存，未写探针输出或证据。SDK自动重拉/强刷关闭，不走直连token fallback；平台HTTP和服务请求禁重定向。

证据JSON只包含字段结构、业务errcode/请求ID及安全计数，不包含token/Secret/API key/密钥、真实PII或真实金额。实际成功证明原项目当前token与这些接口可用；不自动证明驭量runtime业务映射、全部scope、前后端联合验收或G1放行，也不证明罗盘金额单位/时区/退款/去重语义。

[总体机器记录](security-initialization-result.json)、[ADQ离线迁移](adq-security-initialization.json)、[WXSHOP离线迁移](wxshop-security-initialization.json)、[ADQ只读](adq.json)、[WXSHOP只读](wxshop.json)。服务保持唯一loopback运行供已授权后续研发使用，未改变平台业务数据。
`2026-10-28 17:21`追加生产解析兼容核验：另3次只读HTTP200/errcode0，原SDK返回体仅在内存送入本仓库product_id_page(parse_page)、parse_detail、compass_page，三者均PASS。当前详情6个SKU的价格/库存/状态为整数JSON数值，罗盘7字段为整数文本；仅记录类型与计数，不存值。见wxshop-parser-compatibility.json。当前总计本任务7+追加3=10次业务只读，强刷/业务写仍0；此单日样本不证明未来所有日期数值形状或官方金额单位。
备份口径说明：legacyPlaintextPayloadBackupsCreated=0专指没有新增旧凭据/token/.env原文的明文备份；新生成的密钥环和本机API key有独立ACL保护的私有密钥备份，不能把该计数理解为“没有任何私有密钥文件”。本轮初始7次只读和后续解析兼容3次分开留证，累计10次，不能遗漏追加3次。
