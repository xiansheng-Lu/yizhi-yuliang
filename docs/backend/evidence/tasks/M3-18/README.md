# M3-18 内容模型候选生产（短策划/脚本/分镜技术切片）

2026-10-28，BE/F10。整卡IN_PROGRESS，三场景技术切片TECH_VERIFIED_WAITING_REVIEW；基线main0c613c4f9dc40b87e65383844f799e59403e64ab，本地codex/backend-ai-content-generation。真实供应商P验收和最终BE/F10具名复核未完成；本轮只有本地合成HTTP模型、真实PG/MinIO，无任何真实付费/平台调用，不沿用历史M2-01b已耗尽6次/20分许可，不读取或输出真实模型密钥。06/12伙伴改动保留，子任务无Git操作。

## 可核对实现

新增独立content_planning/content_script/content_storyboard协议、DRAFT提示/manifest/golden和内容JSON端口，不修改旧command_parse Manifest/Interpretation/解析接口。InvocationRequest使用已有copywriting计费类别，内容aiScenario与prompt_key另有受控映射，不由客户端指定任意模型/提示。每个prompt有独立模板hash和output_schema_ref，active DB路由与文件hash不匹配时拒绝。生产提示文件保持DRAFT；测试仅在临时库用合成模型、合成价格/预算登记ACTIVE，不修改持久生产配置。

规划/脚本仅返回body，分镜仅返回scenes(title/narration/visual/duration)，服务器分配scene ID并重构完整Document共享校验。模型额外身份/商品/执行字段、工具/思考/截断/非法JSON/超出用量等拒绝；无自动重试、修复或切备。失败不是RULES正文或虚构AI成功。输入与输出有明确上限：上下文7000 UTF8字节，模板+用户内容+固定开销≤8192保守输入单位，max_tokens2048，HTTP响应≤65536字节，1至20镜、每镜0.1至600秒。实际tokens按供应商回报记录，不把字节估计当实际tokens；未知用量/真实扣费保留UNKNOWN。

0069_ai_content_requests接0068_content_analysis_events：真实request/run/step/attempt、输入文档版本、当前shop/product、actor、上下文hash/规范白名单DTO、报价/许可、call/prompt/model复合FK及MODEL候选来源。candidate仍使用既有不可变历史，明确producer=MODEL且必须有实际ai_invocations OK/RESPONDED完成事实，不能手插标签冒充真实端口。报价/许可请求和prompt/model绑定由root共享UOW及0071补齐；最终0069至0072迁移链已在修后专项实际通过。

prepare当前权限/商品/文档/READY/模板场景一致后冻结上下文、调用路由和报价，不发模型。confirm检查明确金额/币种/报价hash和有效许可，模型密钥只由显式服务端ServerSecrets端口提供；同事务先置IN_FLIGHT占完成槽、model_begin推进一次run revision、acquire/start许可，提交后才事务外发送。晚到/失败回包仍保留真实调用/用量/UNKNOWN费用，绝不因DB失败自动发送第二次。相同确认只读已有状态，不重复许可/调用。

MODEL回复重新核当前scope、run revision、step attempt、输入doc version、声明落点及文件证明；保存候选和WAITING_REVIEW原子完成，正文仍不变。采纳复用M3-17b真实版本CAS/事务Flow prepared：新稿ID/完整payload冻结、首版1、不从旧父稿重新拼默认内容。只有人工accept才写正文/产物、DONE及解锁下游。本轮3场景实际走完整生产→候选→人工采纳，非纯DTO或手插候选测试。

## 数据边界

出站DTO只有brief五字段、当前body、授权素材kind/name及OPERATOR_BRIEF来源。商品ID在服务端使用commerce_products.id（V9）校验，内部UUID、native ID、私有key/bucket、签名URL、平台token、Cookie、堆栈不发送。commerce映射没有确认的商品卖点/标题不猜字段；当前卖点来自人工brief，不伪装平台事实。没有视觉适配器，不把metadata名称当看过图片；没有探测音视频时长，不开放音视频引用或ASR字幕。任意历史脚本/messages/订单/退款/结算不会自动拼入。

素材需要真实确认/当前scope、固定VersionId HEAD及实例HMAC，confirm重验，IN_FLIGHT登记SOP在途引用以阻止归档，完成释放；模型只收到kind/name，不收到私有地址或素材UUID。真实MinIO专项验证该链。

自由brief/body/素材名称二次拒绝已知手机号、邮箱、身份证号、令牌/Cookie标签、Bearer/sk类值、长token样字符串及明确收货地址等标签，错误只带安全字段位置，不回显原值；输出同样筛查后才生成服务端ID。字段白名单和这些模式不能证明识别所有自然语言PII（如未标注姓名/地址）；语义残余、运营数据出口规范与最终人工复核明确保留，未把正则检测包装为全面脱敏认证。

## 完成槽与撤权清理

root公共配额通过0069最小definer access.workflow_action_occupancy只返回当前可见真实run的全actor action数量+IN_FLIGHT请求数量。AI请求self-RLS不使其他成员漏计预留，普通MANUAL不能抢完结槽。begin先IN_FLIGHT再写begin动作，complete先终态消费预留再写complete动作；账单UNKNOWN预占与动作完成槽分开。

access.content_ai_failure只接受真实request及安全枚举，NULL/未知原因拒绝；核tenant/原actor、call/prompt/config及许可实际关联，run锁→request锁；只同attempt仍RUNNING可FAILED/revision+model_failure，独立分支保留，旧attempt/已结束run不覆盖，重复无revision。不创建候选/正文/费用许可，不放宽事件当前scope，不复制既有候选正文到失败action.result（candidates固定空、安全step/history元数据）。用于撤权/旧回复/实际失败后的窄事实清理，API没有开放任意调用该SQL函数的路由。

## 验证与范围

- 26单元通过0.60秒：独立内容输出、服务器scene ID、PII/token与上下文白名单、模型身份注入/工具/思考/截断/超用量拒绝、302/429/503不跟转发不重试、非法源配置等。
- ai-content当前修后真实专项17通过17.93秒（初轮15通过15.41秒作为历史）：3场景实际MODEL生产/费用与call/prompt/model来源/无提前正文写入/人工采纳、并发确认只发送一次、人工改稿旧回复拒绝、撤权失败事实清理、429/503/非法身份/timeout不重试、敏感上下文及改报价前置拒绝、默认关闭和真实main HTTP、跨两合法actor self-RLS仍防完成槽抢占、预算不足/伪造或NULL finalizer拒绝、真实MinIO固定版本上下文/在途归档互斥/结束释放/归档后新请求拒绝。
- 本轮实际临时空PG升级到0072；root另行cost27专项通过8.11秒验证共享UOW外层回滚，非本子任务重复运行，不冒称自己跑的成本专项。
- 首轮4通过11失败为夹具调用形态错误：无await的async pytest直接调用同步service，在已有事件循环中执行asyncio.run而未发HTTP，被保守记UNKNOWN；真实HTTP路径当时已经通过。只将本卡无await测试改为同步调用，与FastAPI def线程池形态一致，随后初轮15全部通过，无业务权限/SQL放宽。同步端口应从同步路由/工作线程调用，不在已运行事件循环直接调用。
- 9文件mypy/Ruff通过，DB窗口已明确释放。没有真实模型质量、真实扣费核销、完整所有aiScenario或F10浏览器验收声明。

后续：本批最终回归/提交由root统一；M3-19真实生成账本不等于本卡文本候选，仍需对应供应商与成本证据。内容提示启用/生产模型P验证须另行明确资源与预算，当前默认runtime=None，缺配置受控CAPABILITY_UNAVAILABLE；可继续扩短内容场景与离线golden，不以缺真实key阻塞所有开发。

最终只读收口（本组15项通过之后）：write=True重读request亦LEFT JOIN真实permit.state，并以FOR UPDATE OF r只锁request；不改变费用、发送次数或业务状态。三场景实际用例新增同版本重复confirm.feeState与紧接GET相同断言；该断言及只读query已由本轮17专项真实PG复验通过，不再标记待测。修后9文件Ruff/mypy通过，源码再次冻结。

本轮审查收口17专项（17.93秒，唯一窗口一次运行通过）另实际覆盖：actor-only RLS下同run同requestKey唯一冲突映射409 IDEMPOTENCY_MISMATCH、不返回A对象，B ai_invocations/quote/request整事务回滚；confirm与reply统一run→request→按UUID排序的全部RUN/input文档→相关历史/快照/SOP素材→费用锁，scope失败finalizer和cleanup只重入已持业务锁；两run共享真实MinIO素材及copywriting预算的受控并发证明B预算前已持asset锁、A回包等待业务锁时尚未进入fee_finish，释放B后两者均CANDIDATE且仅发送2次。没有DB/角色/费用权限放宽。root独立ai-ledger21通过6.45秒并验证0072同tenant actor边界，作为root证据引用，不冒充本子任务执行。窗口结束已明确释放root/beans。

本批最终完整整合：1009集成通过、1可选重启跳过（953.14秒）；952单元/契约、240源码mypy/Ruff及0069至0072迁移Ruff通过，冻结源码哈希一致。开发库0072、48相关表仅5系统模板其余0，迁移角色NOLOGIN。详见../../ai-content-generation-verification.json；整卡未完成范围与BE/FE具名复核仍按上列保留。

独立content_topic后续切片也已实际通过：系统模板brief确认→topic候选→人工采纳→planning解锁，6专项及原17回归通过，旧三提示hash不变。见[topic/README.md](topic/README.md)及对应验证/合同；本批975单元、相关36回归及241源码mypy/Ruff通过，真实付费0，整卡其余范围和BE/F10保留。
