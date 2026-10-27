# M1-03a 连接器端口、注册与请求约束

2026-10-27；负责人BE，无FE协作。输入基线9985a7d0afbca478683124613eca3f0cd9a37ae2。
依用户持续开发授权使用已技术验证M0/M1输入进行本地准备；G0/E08不代签。状态WAITING_REVIEW。

## 三条DoD

1. 六方法异步PlatformGateway明确ctx/query/change/command/ref/auth输入及分页、预检、受理、回读、令牌和断言输出；ACCEPTED与MATCHED分离。注册按provider+独立产品线显式进行，默认空表，缺适配器拒绝；非一期产品线不注册。上下文不代替能力/风控/审批/执行授权。合成ExamplePort仅在测试文件内，无生产假适配器。
2. 固定代码操作描述核对目录ID/摘要、连接器/产品线、HTTPS Host、路径、Schema；拒绝域名后缀欺骗、用户信息/非443端口、路径穿越/编码/查询透传、额外字段及跨租户/账户/产品线引用。写参数账户和既有目标需精确匹配服务端对象；签名器只能增添认证信息，不可更换业务方法、载荷、查询字段或Host。V9 wire productLine=shop显式映射内部适配器wxpromo，不隐式变更冻结契约。
3. trust_env=false，不使用LLM代理环境；不跟重定向，有界超时/响应。只有代码明确的幂等READ可最多三次重试，WRITE/TOKEN_REFRESH配置重试即拒绝；发送后超时保持UNKNOWN，较长Retry-After交后续调度。平台HTTP上下文内第三方库日志不留原始签名URL/请求详情，退出上下文后日志行为恢复。

## 实际验证与边界

[verification.json](verification.json)记录35项本卡U测试、221项unit/contract全回归、ruff/格式/mypy通过。httpx.MockTransport仅注入网络成功/故障输入，无外部DNS或真实平台调用；不能计为I/L4，也不代表已获得scope/账户授权或真实写能力。身份/队列等PG回归本卡无改动、不重复计数。无新依赖、迁移或HTTP业务路由。

端口和安全框架已实现，不包含ADQ/小店真实操作映射；实际适配器按后续任务逐项实现及取证。M1-03b接续原始载荷的脱敏留档与错误分类。BE待复核出站/引用/重试边界；停止调用新框架即可回退应用，无数据降级操作。
