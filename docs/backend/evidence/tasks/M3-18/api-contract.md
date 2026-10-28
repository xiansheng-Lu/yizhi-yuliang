# F10 内容AI短场景合同proposal

新API尚无既有前端消费者，作为可复核实现proposal，BE/F10最终确认保留。Cookie与X-Shop-Id必需，POST另需X-CSRF-Token。商品ID沿用服务端文档绑定的commerce_products.id（V9），请求不能指定任意商品/native ID/模型/密钥或system prompt。

POST `/v1/content/workflow-runs/{run}/steps/{code}/ai/prepare`：

```json
{"requestKey":"content-script-attempt-1","expectedRunVersion":1,"expectedAttempt":1,"documentId":"00000000-0000-4000-8000-000000000100","documentVersion":1,"aiScenario":"content_script","assetIds":[]}
```

只支持受控映射：planning/topic→content_planning(目标planning.body)，script→content_script(目标scripts.body)，storyboard→content_storyboard(目标scripts.scenes)。模板必须AI_ASSISTED且声明documentKind一致，步骤READY、输入文档属于当前run且版本匹配。source为planning而目标scripts时由服务器冻结真实派生文稿，用户不传新文稿ID。assetIds最多8个，不向模型发送这些UUID/URL；只发经过授权与实际固定版本核验的类型/名称。

返回id/state=PREPARED、inputVersion/attempt、aiScenario、callId/promptId/modelConfigId/promptVersion/model/contextHash、candidateId=null、quote{id/hash/maximumCostMinor/currency/expiresAt}及feeState。prepare不调用模型、不更新正文；生产runtime缺省None返回422 CAPABILITY_UNAVAILABLE。DRAFT提示文件未自动变成生产ACTIVE配置。

POST `/v1/content/ai-requests/{id}/confirm`：

```json
{"quoteHash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","maximumCostMinor":1,"currency":"CNY","confirmed":true}
```

示例金额/hash仅golden形状，不是实际价格。必须原请求actor明确确认实际响应报价；金额/币种/hash变化409。scope/doc/run/attempt/素材已变则拒绝且不发送。没有有效许可/预算不发。确认提交许可与IN_FLIGHT/model_begin后只发送一次；同期重复可读IN_FLIGHT，终态重复只读原请求事实，不自动重试。无许可token明文持久或返回，无客户端模型key参数。

成功只产生state=CANDIDATE、candidateId与真实模型调用引用，run step进入WAITING_REVIEW；正文不变。通过既有`/workflow-runs/{run}/steps/{code}/accept`带当前run revision/attempt/candidateId/input documentVersion人工采纳。不能把调用OK当已采纳或已上传。服务端生成scenes ID，模型不能指定ID、商品、审批或任意字段路径。

GET `/v1/content/ai-requests/{id}`返回当前身份可见的自身请求事实。AIrequest self-RLS，其他同范围成员仍可在既有workflow execution读取已挂接候选并按当前权限采纳，费用确认不转交actor。响应不包含prompt全文、出站body、原始响应、secret/permit token。feeState使用可见费用账本状态，调用用量可知不等于费用已核销；UNKNOWN保留预占。

旧回复返回STALE/STALE_REPLY；scope撤销返回STALE/SCOPE_CHANGED并只做受控失败事实清理，不写正文/候选。模型超时/429/503/结构错误返回FAILED或UNKNOWN及安全原因。已发送后记录失败返回RESULT_UNKNOWN，不把请求重置PREPARED。未授权401/403，不可见对象404，非法字段/敏感内容/上下文超限422，旧版本/幂等异请求409。同键prepare相同请求只读已持久事实，异内容拒绝。

内容输出golden：

```json
{"body":"介绍商品实际用途，等待人工复核后采用。"}
```

```json
{"scenes":[{"title":"用途","narration":"展示实际用途。","visual":"展示已确认商品，不虚构性能。","duration":3.0}]}
```

模型输出只允许上述所选schema，不能额外返回call_id/productId/assetId/approval等字段。真实模型质量/P验收和F10浏览器体验不在本次合成HTTP技术结果中。caption/图片理解/音视频生成/ASR/长文异步均未开放。输入网络deadline8秒不代表所有DB故障的端到端15秒绝对保证；长场景需另立异步合同。

审查补充：requestKey唯一范围为tenant/run，虽然请求context是actor私有，另一合法成员同键prepare也只得到受控409 IDEMPOTENCY_MISMATCH，不返回原请求ID/报价/context；其本次AI账本与报价创建整事务回滚。feeState重复confirm和紧接GET的同版本实际断言已由修后17专项通过。业务锁顺序仅服务器内部约束，不增加客户端字段或改变确认金额。
