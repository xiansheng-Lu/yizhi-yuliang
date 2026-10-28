# M3-17b 开工准备（只读设计，不是实现）

2026-10-28，BE/F10。状态PLANNED；本文只记录下一批可开工设计。当前批次源码冻结，本文不代表execute/accept/skip/redo、AI内容候选、generation_jobs或SSE已交付。后续须主Agent完成当前审查修复/回归/主干同步并建立新分支，再分配迁移编号、共享接口和独占DB窗口。

## 规格与实际依赖证据

- [20册M3-17b](../../../architecture/20-后端逐任务开发与验收卡.md#m3-17b)：直接依赖M3-17a；候选采纳才写正文/产物，并发不重复；重做只失效依赖闭包，独立确认分支保留；必需步骤不能跳过，run完成不自动上传/投放，在途生成不重复启动。
- [16册第4节](../../../architecture/16-内容SOP工作流详设.md)：READY→RUNNING→WAITING_REVIEW→DONE；只有模板允许的可选步可READY→SKIPPED并记录原因；默认SKIPPED不能满足下游依赖，只有模板明确可选依赖例外。第5节要求实际SOP推进调用共享校验；第8节列execute/accept/skip路径及状态事件要求。
- [12册第7节](../../../architecture/12-核心状态机详设.md)：WAITING_PROVIDER未知只凭external_job_id查单；不能因超时/重做重发付费请求。新job/attempt须与旧事实区分，历史可追溯。
- M3-17a的workflow_templates/run/steps已有真实PG初始化、版本锁定、商品范围及根文档RUN引用。本卡继承commerce_products.id（V9 products），不混用canonical products.id，不新增平台在售要求。
- `aikernel/models.py`现Candidate只有call_id/source/status/requires_review，没有文稿内容；`aikernel/deepseek.py`的StructuredCandidate.interpretation为command_parse冻结指令解析，其目标类型AD_PLAN/LIVE_SESSION，不是内容候选。M3-18真实正文/分镜候选与上下文尚未具备，不能把现gateway接上便报告创作成功。
- 当前src/alembic没有generation_jobs/generation_steps业务实现。M3-19真实生成账本/供应商/成本与恢复链路尚未具备，不能临时伪造QUEUED/SUCCEEDED来补验收。
- `content/flows.py`的FlowService.create当前自己开两段business_transaction。直接从采纳事务调用会独立提交子稿；主Agent须补受控create_in_transaction共享入口并保留版本、请求键、锁与校验。禁止改用ContentService.flow随机兼容key。
- `platform/events.py` EventData.entity_type及0038/0040事件投影约束目前没有workflow/generation类型。主Agent负责新增实际事件类型、范围过滤、持久投影及主装配；不能冒充现job.changed，也不能以GET可读声称SSE已通。

## skip契约必须显式版本化

本次检索真实前端`frontend/src/lib/content-factory.ts`、`content-delivery.ts`，没有workflow/dependsOn/optional字段；现M3-17a StepDefinition也没有可选步或可选依赖标识。16册只有业务语义，未冻结具体字段名。因此新字段应作为可复核实现proposal明确版本化；AGENTS要求BE/F10最终确认，并不要求尚无前端消费者的新字段逐项先审批才允许本地开发。

不能修改历史template.definition、不能把requiresHumanConfirm=false解释成可跳过，也不能从MANUAL类型或步骤名称猜可选。现有已锁模板全部按必需步处理，skip明确拒绝。可直接本地实现向后兼容的version2提案并测试成功跳过，保留BE/F10最终具名复核：新版definition显式schemaVersion=2，step.optional默认false，step.optionalDependsOn默认[]且必须是本step.dependsOn子集。仅optional=true允许READY→SKIPPED且skip原因非空；下游某依赖为DONE即可满足，若为SKIPPED则仅当该依赖列入下游optionalDependsOn时满足。未知schemaVersion拒绝；缺schemaVersion按1解释，version1禁止出现新增字段且所有步必需。复制/追加新的模板version才采用schemaVersion2，既有seeds/definition及在途run不修改。schemaVersion描述结构语义，与模板整数version分别保存。该扩展尚无FE消费，作为实现proposal交付golden样例及兼容性证据，不宣称已经最终签字。

## 可立即实现的真实技术切片

新增`content/workflows_execution_models.py`、`workflows_execution_service.py`、`workflows_execution_routes.py`和独立闭包算法/测试；具体文件名可按目录约定调整。必要迁移编号由主Agent分配，不预占下一编号。

1. run增加revision；step记录attempt/current_candidate/document及输入版本；新增不可变attempt/candidate历史和动作幂等记录，唯一(tenant,run,step,attempt)及(tenant,run,actionKey)。候选保存服务端规范化后的受控落点与值、输入文档版本，不接受任意JSON路径写库。
2. MANUAL execute真实接收人工拟采纳内容，校验当前scope、依赖和完整目标Document（purpose=SOP），保存候选并进入WAITING_REVIEW；不覆盖正文，不假装调用AI。可按现Document合同直接实现白名单proposal并提供golden样例，最终由BE/F10复核，不等待每字段预审批：仅允许brief、body、scenes、prompt等明确字段的完整替换，禁止id/productId/parentId/kind/version/updatedAt等身份与并发字段，禁止任意JSON路径。重构完整Document后统一校验；素材字段若扩入白名单必须走实际固定版本证明。不能用空响应或快照确认冒充生成产物。
3. accept读取持久候选，锁run后按固定顺序锁文档/资产，核对expected run/attempt/document版本、候选仍当前且未失效；资产先做真实固定版本预检，再在事务内重验证明。候选写入正文/派生文档、引用登记、step DONE、下游READY计算在同一事务完成。重复同键返回原结果，异payload冲突；文档被人工修改返回409并保留候选。
4. 派生仅调用主Agent新增FlowService事务入口，显式kind/sourceVersion；稳定业务键`run:{id}:step:{code}:attempt:{n}:kind:{kind}`，不用随机key。同一run不同合法actor重试也不能重复子稿。
5. redo按模板显式dependsOn迭代求当前步及下游闭包，attempt递增，当前步回READY、下游回PENDING并清空当前机器产物指针；历史候选/文档/资产引用保留，不删除已产出事实，独立DONE分支保持。旧attempt回调/采纳拒绝。
6. 闭包内存在RUNNING/WAITING_PROVIDER外部动作时不能简单重置重发；先确认取消/未受理或对账结果。未具备实际生成账本时不能构造假的可重做provider job。
7. AI_ASSISTED/GENERATION缺真实适配器时明确CAPABILITY_UNAVAILABLE，不输出虚构候选/假job/成功扣费。未发送与供应商失败分开记录；不因缺资源就阻塞人工事务切片。
8. 全部必需步骤DONE及被version2模板明确允许的SKIPPED才可COMPLETED；原子结束在途RUN引用，保留历史引用。run完成不触发任何平台上传/投放。

## 并发与验证集合

实际create_app/Cookie/CSRF/当前scope与独占临时PG验证，不调用真实供应商、不重复付费。拟覆盖：

- 同execute/accept键并发只1attempt/1保存/1子稿；同键异payload409。
- 未采纳前正文不变；输入文档被人工编辑后采纳409，候选可读。
- 采纳中在保存/派生/引用/step后注入异常，所有事实回滚；重新请求可完成且不重复。
- 自环/断开图/菱形依赖及多分支redo闭包准确，独立人工DONE保留，历史版本可追溯。
- 必需skip拒绝；按version2 proposal测试可选skip、原因必填、默认不满足下游及显式optionalDependsOn例外，并确认version1缺字段仍必需。
- 旧attempt候选/回调拒绝；在途/UNKNOWN不会生成新付费请求。
- 跨tenant/shop/product/账户拒绝；素材固定VersionId/证明过期/被归档守卫保持。
- 实际SOP推进调用validate_content(purpose=SOP)；不是仅库层三purpose合成测试。
- 完成无平台动作；共享事件类型装配后验证投影提交原子性、范围可见性及真实SSE，否则准确保留SSE缺口。

可技术验证人工候选/采纳/重做，并不等于M3-18模型正文生成或M3-19供应商账本已实现；未具备的子范围始终单列。未实现项不能整体标记仅待人工复核。


## version2与人工候选golden提案

以下可作为下一批本地实现及契约测试输入，尚待最终签字，不修改任何已有模板：

```json
{"schemaVersion":2,"code":"manual_optional_v2","name":"人工可选分支","productLines":[],"steps":[{"code":"brief","name":"需求","kind":"MANUAL","dependsOn":[],"optional":false,"optionalDependsOn":[],"documentKind":"planning"},{"code":"reference","name":"补充参考","kind":"MANUAL","dependsOn":["brief"],"optional":true,"optionalDependsOn":[],"documentKind":"planning"},{"code":"script","name":"人工脚本","kind":"MANUAL","dependsOn":["brief","reference"],"optional":false,"optionalDependsOn":["reference"],"documentKind":"scripts"}]}
```

人工候选execute请求proposal：`{actionKey,expectedRunVersion,expectedAttempt,documentId,documentVersion,patch:{body:"人工拟采纳脚本"}}`。patch初始白名单为brief/body/scenes/prompt；示例只改body，其他字段从服务端当前Document读取并重构校验。execute保存候选与输入版本但不改正文；accept请求proposal为`{actionKey,expectedRunVersion,expectedAttempt,candidateId,documentVersion}`，服务端取持久候选，不重新接受客户端候选正文。确需派生时另含明确kind/sourceVersion并使用FlowService稳定run-step键。字段名与结构为新API提案，golden应覆盖422身份字段注入/未知字段、409旧版本、候选保留与实际采纳事务。
