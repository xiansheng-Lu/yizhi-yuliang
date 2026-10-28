# M3-19a 实际生成账本API

Cookie、X-Shop-Id为服务器身份/店铺范围；POST需要X-CSRF-Token，未知query身份覆盖拒绝。

- POST `/v1/content/generations/quotes`：GenerationInput（requestKey/runId/stepCode/expectedRunVersion/expectedAttempt/documentId/documentVersion/kind/provider/priceKey/prompt）。服务器核实际文档版本/商品/素材，查实际provider+kind价格单位证明，不接受客户端units/actualCost/status。当前只有IMAGE/GRAPHIC的一请求JOB有代码支持，实际价格仍默认无配置。
- POST `/confirmations`：GenerationCreate（input及quoteId/quoteHash/maximumMinor/currency）显式确认。确认的是当前actor、固定请求与价格版本，不能复用LLM确认或伪confirmed=true。
- POST 空路径：GenerationCreate创建账本。先按原requestKey同hash复用，异hash409；新任务核真实确认/expiry/step在途并与CostGate许可、固定引用同事务。
- GET `/{id}`：当前原actor且当前文档/店铺/商品授权范围的状态/提交态/版本、估算与实际费用及billingState，不含permit token/hash、私有文件地址或完整prompt。
- POST `/{id}/cancel-unsent`：仅NOT_SENT释放许可/引用并取消；SENT/UNKNOWN明确不能称已停止或退款。

HTTP默认Unavailable供应商，不暴露供应商submit/任意账单写接口。服务内部dispatch只有先持久SENT再一次submit；query只能原键查单。真实供应商连接、回调、媒体导入、费用凭据及采纳由M3-19b/c继续，现SUCCEEDED关闭，最多真实私有素材核验后WAITING_REVIEW。

业务状态和费用状态独立：FAILED仍可查UNKNOWN费用；KNOWN费用不因之后未附账单降级，也不阻断合法待审素材更新；矛盾新KNOWN回执拒绝覆盖原结算。


派发记录属于原actor自己的真实job，SELECT/INSERT绑定原requestKey/payloadHash；INSERT仅允许QUEUED/NOT_SENT且原GENERATION permit已IN_FLIGHT。其他actor即使知道job/key/hash也不能预插记录堵发送或取消。变更锁序固定run→job→排序后的全部输入/输出素材→费用预算，取消/FAILED清理不在预算后补拿run/asset锁。
