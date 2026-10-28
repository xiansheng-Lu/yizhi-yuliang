# M3-18 AI创作候选与上下文：开工准备

2026-10-28，BE/F10。状态PLANNED，未实现、未运行本卡DB或付费模型/平台调用。当前root最终全量独占；本文是下一分支的可执行设计，不是研发完成或真实验收。只新增本文件，不修改源码、迁移、配置、06/12或Git。

## 输入证据与真实缺口

- [20册M3-18](../../../architecture/20-后端逐任务开发与验收卡.md#m3-18)：直接依赖M3-17b、M2-01b、M5-02a；交付各aiScenario提示、候选采纳与调用记录。DoD要求授权商品/brief/素材上下文、无PII/令牌；带版本的声明落点采纳与prompt/model引用；预算/失败解释及旧回复保护。
- [16册](../../../architecture/16-内容SOP工作流详设.md)2.2、3.1、4、9节：AI_ASSISTED仅出候选，确认才写正文；示例aiScenario=content_storyboard；上下文只取本商品brief/卖点、授权素材、历史脚本。个人信息边界由[08册118-119行](../../../architecture/08-AI能力设计.md)约束：剔除收货人、手机号、详细地址、结算账户；令牌/Cookie/内部堆栈不入提示。
- M3-17b已有真实manual/review候选表、版本/attempt、幂等动作、采纳事务、Flow prepared完整冻结文稿。刚修复的契约必须保持：派生输出ID与冻结候选一致、version1、不得回到旧父稿重新拼prompt/节点；动作响应含本次history，1000动作写前配额，旧不可变历史不改。
- `aikernel/models.py` InvocationRequest已有copywriting场景，但Candidate本身无文稿。`prompts/registry.py` Manifest.prompt_key及output_schema_ref冻结为command_parse；`deepseek.prepare_call`显式只接command_parse，decode_response解析Interpretation（AD_PLAN/LIVE_SESSION）。不能拓宽旧解释对象或借旧prompt引用冒充内容候选。
- `0009_ai_ledger.sql` prompt_versions与ai_begin可登记按prompt_key/version的ACTIVE模板，冻结配置/模型/模板hash/output_schema_ref；InvocationLedger.begin/finish只持久调用身份/用量/摘要等，不保存原始输入。可作为新内容网关的调用事实基础，不需要改旧command_parse golden。
- `CostGate.quote/confirm/acquire/execute`已有先报价、明确确认、一次性许可、IN_FLIGHT提交后才事务外调用、UNKNOWN保留预占的真实端口。BillableRequest绑定request_key/scenario/price_key/输入输出上限/payload_hash；LLM许可不能换正文、上下文、模型或场景后复用。
- M2-01b既有通道只验证指令解析，不代表内容场景已P验收。其历史6次真实请求与原人民币20分许可已消耗，不得继承或追加调用。当前开发仅离线/真实临时PG与本地HTTP合成供应商，不读出/输出密钥，不自动启用ACTIVE生产配置或预算。

## 新内容场景与输出合同proposal

在既有InvocationRequest.scenario=copywriting下，新增受控aiScenario映射，而不是随便接受客户端prompt/model名：

| aiScenario（提案） | 白名单输入 | 唯一输出落点 |
| --- | --- | --- |
| content_topic | brief、同商品已授权脚本摘要 | planning.body |
| content_planning | brief、已确认主题 | planning.body |
| content_script | brief、选定planning.body | scripts.body |
| content_storyboard | brief、当前scripts.body | scripts.scenes |
| content_caption | 已确认且有来源的字幕文本/分镜文字 | 当前明确支持的文本落点；无真实语音探测时不可声称ASR字幕 |

首批优先真实可验的planning/script/storyboard，caption精确落点须按现Document能力和16册单列，不为凑场景数量编造音视频探测。模型只返回`{body}`或`{scenes:[{title,narration,visual,duration}]}`等专用严格Pydantic DTO；拒绝额外键/任意路径/身份/审批/平台动作。scene ID由服务端生成，duration按M3-16共享规则，列表/文本/总字节有界。不把command_parse Interpretation、模型字符串或“RULES”标识当成功正文。

每场景新ContentPromptBundle（版本模板、输入/输出schema、模板hash、配置参数、golden及变更原因），不能更改旧command_parse Manifest语义。数据库prompt_versions先DRAFT，技术测试在隔离fixture登记ACTIVE；生产启用仍走现配置权限/最终BE/F10复核。新字段和内容API可本地实现proposal及golden，不把逐字段先审批当开发阻塞。

## 上下文白名单与出站边界

1. 服务端按当前tenant/member/shop及commerce_products.id（V9稳定ID）解析商品，复用ContentService._shop/_product及账户scope。本地移除商品拒绝；不额外要求平台在售。不能接受客户端任意nativeId、canonical products.id或跨店商品作为映射。
2. 只构造显式DTO：当前文档brief五字段、选定body/scenes受控摘要、本商品绑定/来源与新鲜度标记、授权资产的内部引用/类型及经允许的文本摘要。commerce_products当前主要为稳定映射，不存在的商品标题/卖点不得从原始platform payload猜字段；现可用人工brief.sellingPoints，并标注来源OPERATOR，不伪装平台事实。
3. 历史脚本必须显式选择、同商品/同shop、每次当前scope复核，限定条数/总字节/版本；默认不把messages/全历史/订单/退款/结算/CRM拉进上下文。messages将来纳入时需逐字段白名单、大小限制及同样脱敏。
4. 素材只接受已确认、未归档、同商品范围的asset ID，复用真实固定VersionId HEAD/HMAC证明。首批文本模型只用受控文字，不发送私有对象key/bucket、签名下载URL、上传许可、token、图片/视频原始二进制。不以素材metadata.name中的任意文本绕过过滤；没有视觉适配器不宣称看过图片。无可信时长不生成可引用音视频片段。
5. 出站序列化器不允许任意dict/SDK请求或整行ORM；禁止订单收货人/手机号/详细地址/结算账户、平台令牌、Cookie、Authorization、内部异常。自由brief/body也可能夹带敏感内容，必须二次检测明确手机号/邮箱/身份证/令牌/Cookie等模式，高风险拒绝并返回受控字段位置，禁止静默发送。语义PII无法仅靠正则证明完全识别，残余风险和人工数据出口审查必须如实保留，不能把字段白名单等同所有自由文本天然无PII。
6. 用户文本与素材摘要标记为不可信数据，不当system指令；无工具、无网络检索、无任意URL访问、无自动执行/审批。输出也经敏感信息检测、声明落点及完整Document校验，拒绝模型产生未知素材ID或隐式商品切换。日志/异常只存请求ID/hash/受控reason，不写prompt全文、原始响应、密钥或被拒字段原值。

## 调用、预算与持久化流程

新增content AI request/attempt事实（迁移编号由root后分配）绑定tenant/run/step/attempt、doc及输入version、aiScenario、context_hash、prompt版本/hash、冻结模型路由、ai_invocation.call_id、报价/许可ID和candidateId。原始业务输入保留在既有文档受权历史，不复制到通用调用账本。候选正文留在受RLS保护的内容候选表并带producer=MODEL与call_id/prompt引用；不可伪造来源标签。当前表没有来源引用字段，须显式迁移/FK，不以自由JSON日志代替。

建议分两段API：prepare（构上下文、持久化request与调用STARTED、冻结prompt/route/body hash、报价）→confirm/execute（当前actor明确确认与一次性许可）→现有accept。prepare不能等同发送；缺有效报价/确认/许可/预算在网络之前拒绝。价格/预算不从客户端输入，生产无ACTIVE路由/价格/预算时明确不可用，不自动生成默认额度。

首批保守proposal：受控输入字节加固定消息开销≤8192，输出上限按新场景明确配置（如2048）；这不是继承M2-01b的512token历史许可，报价必须使用本次实际固定上限。供应商实际tokens超过上限/使用量不合法拒绝候选并保留已发送费用事实，不把未知用量或未知实际扣费写0。token估算必须说明保守边界或用可靠计数器，字节限额不冒充供应商实际token计量。

同步网关保留有界网络deadline（可复用8秒策略）、不跟重定向、不读代理环境、响应大小上限；不自动重试/修复/切备。DB连接/锁也要statement/lock timeout，不能把8秒网络说成所有故障端到端必然小于15秒。若内容场景无法可靠满足客户端15秒预算则必须先定义真实异步任务/恢复合同，不用空异步ID假装已执行。首次实现可限定短正文/有限镜头，不为长视频链无界等待。

在run锁下核对revision/attempt/step READY并检查动作配额，创建唯一生产attempt并进入RUNNING；提交后事务外发送，不持DB锁等网络。CostGate.start和调用ledger都先持久提交；相同请求恢复只读取已有状态，IN_FLIGHT/UNKNOWN不得重发。明确NOT_SENT可按现许可取消规则处理，已发未知保留预占与可查call ID。超时/429/503/结构错误返回UNAVAILABLE/FAILED受控原因，可提示人工编辑/手动候选；规则模板若出内容必须显式source=RULES并有版本来源，不能伪称模型生成。

回复后重新锁run并复核当前tenant/shop/product权限、run revision、step attempt、输入文档version、冻结上下文hash、素材证明及prompt/model绑定。期间人工改稿/redo/权限撤销/素材失效则记录STALE/REJECTED结果与真实调用费用，不能覆盖新稿或“重新发一次”。保存MODEL候选和WAITING_REVIEW状态原子提交，正文仍不改。推进run revision必须遵循每个真实状态动作+1和workflow.changed触发契约；不是把多个分离状态写入冒称一次原子动作。晚到回复不因同键重放绕过旧版本保护。

采纳只接受持久candidateId与当前版本，不接受客户端替换模型正文；复用M3-17b同事务采纳与Flow prepared冻结完整产物。MODEL生产者需受控内部端口而非调用MANUAL execute伪装来源；新增共享生产入口由root明确归属后修改，保持人工路径与历史不可变。

## 拟文件与迁移边界

- content/ai_content_models.py、ai_content_context.py、ai_content_service.py、ai_content_routes.py及真实SOP生产者适配。
- aikernel/content_prompts.py、新content_prompts/v1模板/golden/manifest，content_gateway.py和content_provider.py；只复用独立费用/调用账本公共协议，不破坏旧解析DTO/提示golden。若抽取通用HTTP传输须root统筹旧解析回归，避免复制时丢超时/不重试/费用UNKNOWN约束。
- 新迁移扩展candidate producer/model引用与AI request/attempt/报价绑定、幂等/复合FK、RLS与不可变历史；不得修改旧candidate/action历史。prompt内容以可审阅版本文件交付，生产状态不自动ACTIVE。
- 公共main/runner/settings/事件和共享WorkflowExecution生产端口由root分配。密钥仍仅ServerSecrets由宿主服务端注入，不增加读本机配置/环境枚举接口。

## 必要验证集与验收边界

1. U/contract：每场景真实输出DTO/golden、prompt hash/版本变化、白名单及PII/令牌拒绝、恶意输入无工具、未知素材/字段拒绝、上下文/token/输出上限、scene规范化与完整Document共享校验。
2. 实际临时PG+本地HTTP供应商：先报价/确认/许可再发送；并发同键仅一次网络；费用/调用/候选复合引用；失败后无正文覆盖；prompt/model/call来源可查，不能注入他租户call或候选。
3. 旧回复：模型等待期间人工编辑doc、step redo、scope撤销、资产归档/证明变化、prompt配置变化，逐项证明回复不覆盖当前文档、不新发付费请求，真实历史与UNKNOWN预占保留。
4. 故障：429/503/302/超时/断连/无usage/非法JSON/额外身份字段/输出超界；发送成功后DB落账失败、许可IN_FLIGHT恢复、候选落库回滚；无隐式修复/重试/切备。
5. 采纳并发、Flow冻结内容一致、history GET/重放一致及1001配额继续回归；验证真正MODEL生产者到WAITING_REVIEW，再由人工accept一次写入正确字段。不是只用纯DTO或手工插候选算完成。
6. 真实付费P验收必须另有当次明确预算/次数/模型/输入输出上限授权及安全资源，不沿用历史6次额度；此前先完成全部离线/真实PG/本地HTTP技术闭环。无key仅阻塞真实供应商验收，不阻塞协议/生产者/事务实现；技术证据准确标注合成供应商，不当真实模型质量或人工golden准确率。

本文件只准备，不宣称上述接口、模型上下文生产、提示发布或新迁移已存在。待当前批次主干同步并切新分支后实际实施；未实现内容与最终BE/F10签字分别保留。
