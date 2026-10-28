# M3-19a 生成账本与成本确认准备

**PLANNED，仅源码/架构只读准备。未实现本卡、未新建空表、未执行DB/迁移/供应商/平台/付费/Git操作。**
主Agent当前独占最终full regression session96111；仅新增本准备文档。正式开发须主干同步后新本地分支领取，DDL编号向主Agent协调。

## 真实依赖与现状

20册M3-19a依赖M3-17b、M3-15a、M5-02a。现M3-17b已具备实际MANUAL/REVIEW execute→WAITING_REVIEW→人工accept、稳定action键/版本/attempt/闭包redo；`workflows_execution_service.execute`明确拒绝AI_ASSISTED/GENERATION，不能把现有人工候选当供应商生成已接。

素材基线是UploadService确认后的content_assets固定object_version；AssetService.prepare_reference/prepare_document做实际固定版本HEAD/HMAC，事务内reference_in_transaction/validate_document锁内重验。当前只确认图片/文本，音视频或未知时长继续关闭，不能用客户端mime/size/duration代替媒体事实。

M5-02a实际代码：

- `aikernel/cost_models.py` BillableRequest区分kind=LLM/GENERATION，包含稳定UUID request_key、scenario、price_key、精确整数input/output units及payload_hash；CostQuote绑定quote_id/request_key/max_cost_minor/currency/expiry/quote_hash；CallPermit一次性secret仅hash进账本；BillingOutcome UNKNOWN禁止金额/币种/回执，KNOWN必须三者齐备。
- `aikernel/cost_gate.py` CostGate.quote/confirm/acquire/start/finish/cancel_unsent/execute，调用已存在受限access.ai_fee_*函数。许可先持久IN_FLIGHT后事务外call，异常保留UNKNOWN预占，不因记账失败重发，不把未知归零释放。
- 0012 `ai_price_versions`已有kind/单位TOKEN|JOB|SECOND/scale/币种/精确费率/有效窗/VERIFIED与proof_hash；`ai_cost_quotes`含actor/request fingerprint/价格快照/最大额/expiry/确认时间；`ai_cost_permits`为RESERVED/IN_FLIGHT/UNKNOWN/SETTLED/CANCELLED，唯一tenant+kind+request_key。
- `aikernel/ledger.py` InvocationLedger.begin/finish是**LLM模型调用**账本，记录prompt/model/usage/cost；InvocationRequest场景目前command_parse/decision_diagnose/report_narrate/copywriting。不得把供应商视频/图片费用伪装成tokens或模型调用结果。
- `connectors/governance.py` PlatformGovernance已有Redis准入/退避语义；其平台账户/通道键不可冒充生成供应商范围。本卡先使用独立受控generation端口/稳定tenant+provider预算域；将来复用其原子限流原语需明确生成范围和资源配置，不能借ADQ账户额度发供应商调用。

当前没有真实供应商报价/单位/查询或去重协议证据，默认没有GENERATION的有效价格/预算/确认/适配器。报价缺口必须明确拒绝，不能给0估价或写实际费用。

## 账本与状态设计

依据08册、13册generation_jobs片段，业务七态保持：

`QUEUED / RUNNING / WAITING_PROVIDER / WAITING_REVIEW / SUCCEEDED / FAILED / CANCELLED`。

另有独立提交子态：`NOT_SENT / SENT / ACKNOWLEDGED / UNKNOWN`。UNKNOWN不是第八个业务态；已可能发出而回执不明落WAITING_PROVIDER+UNKNOWN，费用许可保持UNKNOWN/IN_FLIGHT。

计划新content/generation_models.py、generation_service.py、generation_repository.py、generation_routes.py与独立供应商端口协议（默认Unavailable，绝不实际调用）；测试和证据配套。SOP GENERATION接线须与workflow owner协调，不直接旁改执行共享代码。

必要DDL（编号另分配，不在本准备创建）：

- generation_jobs：真实tenant/actor/shop/product/document+documentVersion、可空run/runStep+attempt、type/provider、稳定client creation key/provider_request_key、精确规范payload_hash/安全参数快照、quote_id+quote_hash/permit_id、业务七态与提交四态、version/lease/fence、可空external_job_id、冻结输入asset versions、result_asset_id、estimated/actual费用及currency/proof状态、当前错误安全摘要/时间。
- generation_attempts或等价不可变dispatch记录：稳定requestKey、frozen payload hash、SENT提交时间、fence、外部id/查询结果证明。不能等external_job_id返回后才生成内部去重键。
- 同tenant+creationKey唯一，同provider+provider_request_key唯一；同tenant+run_step在业务在途状态QUEUED/RUNNING/WAITING_PROVIDER/WAITING_REVIEW仅一个active generation（partial unique约束）。不同attempt不能绕过旧UNKNOWN任务再次收费；需先证明旧任务终态或人工核对明确解锁。
- 复合FK绑定实际content document/run/step/shop/product和实际quote/permit；普通app角色不能任意覆盖身份、请求hash、费用/供应商回执；采用受限CAS/必要最小SECURITY DEFINER函数，固定search_path与当前actor/tenant/实际scope，禁止为锁扩大业务表UPDATE。
- 不把模型InvocationLedger与供应商generation ledger合表。费用汇总按kind及来源分开，预估/预占/实际/UNKNOWN分别展示；同一个生成许可只结算一次。

## 报价、确认与提交原子边界

1. quote只读真实当前文档/商品/素材版本及run-step scope，构造固定规范参数hash；请求含同一个稳定request_key。报价价格版本必须kind=GENERATION且有经核对来源、真实币种/单位/scale、有效窗。未证明的单位或时长不能造计费units；无价格明确NOT_INTEGRATED/CAPABILITY_UNAVAILABLE。
2. 用户显式确认真实quoteId/quoteHash/maximum/currency。确认由当前服务端actor执行；浏览器自动勾选、此前模型费用确认、本地请求字段confirmed=true不能替代生成报价确认。
3. create优先按原creationKey查已有job；同hash返回原job，异hash409，不消费第二许可。新任务重新核验quote expiry、价格与预算状态、文档版本、run/step attempt、当前素材固定证明、actor×shop×product权限；原子持久job/在途约束/费用许可关联/GENERATION引用。
4. 现CostGate.start单独business_transaction，不能直接在外层generation事务中调用并声称原子。正式实现需复用现access.ai_fee_start在同一持久dispatch事务，或为CostGate补经主Agent审阅的unit-of-work入口，使许可IN_FLIGHT、job.submit_state=SENT、稳定provider_request_key和payload_hash同事务提交；不另造绕过费用门禁。
5. 提交事务成功后才允许事务外一次外部send。SENT后即使进程在真正网络发送前崩溃，也按“可能发送”处理，不能凭本机未记外部ID断言NOT_SENT/零费用。回复丢失/落库失败/超时变WAITING_PROVIDER+UNKNOWN；只用原request_key查单，不用新键重发。
6. 费用许可SecretStr不能写明文DB/日志或客户端。必须在实现前解决“进程重启后的安全许可引用/结算”边界：经受限job→permit绑定的恢复入口，或独立服务端密钥域加密能力；不复用OAuth token加密上下文。若暂未具备可信恢复凭据则保持UNKNOWN与预占、仅查单/人工，不能造SETTLED。
7. 未发送且可证明NOT_SENT可调用cancel_unsent释放；SENT/UNKNOWN不自动cancel退款。供应商真实cancel/实际费用/结果采纳属于M3-19b/c，未有能力时明确拒绝。成片也先WAITING_REVIEW，不自动覆盖文档或创建SUCCEEDED结果资产。

## 可立即做的真实技术验证

- 真实PG/Redis/MinIO+合成供应商端口；绝不调用真实模型或供应商。维护角色提供明确标记synthetic的价格版本/币种单位依据，不当作实际价目。
- 同键同hash重复/并发返回同job、异payload409；两个请求争同run_step只有一个在途；UNKNOWN任务不能借新creationKey/attempt绕过。
- 真实quote确认/expiry/价格撤销/额度不足/当前角色或文档版本撤销/固定文件版本漂移，均在发送前拒绝；未知单位不报0。
- kill/fault在job保存、费用预占、SENT提交、供应商接受但应答丢失、回执落库失败、费用结算前后：账本/job/引用事务闭合，稳定key不丢、供应商submit计数最多一次、UNKNOWN不释放不重发。
- 分离七态与提交子态、可空外部ID；UNKNOWN恢复只调用query(original_key)；无查询协议时挂起人工，不假进度/不重付费。
- 真实Cookie/CSRF/当前范围与跨tenant/shop/product/actor拒绝、固定版本引用/历史保留；模型ledger与生成费用汇总不混合、不把预估当实际。

M3-19a可证明上述技术链路；真实供应商提交/回调/费用预算许可是E05和M3-19b/c另行验收。仅合成端口通过不代表供应商实际接入或真实价格已核验。
