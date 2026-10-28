# M3-17b F10人工执行契约proposal

所有接口使用Cookie、X-Shop-Id，POST另需X-CSRF-Token。不是既有FE已接入声明；BE/F10最终具名复核保留。旧GET /workflow-runs/{id}不变；新GET /workflow-runs/{id}/execution读取revision、status、rootDocumentId、steps(code/status/attempt/candidateId/documentId/skipReason)、candidates及history。history含动作键/原因/当时revision与产物文档指针；新动作返回包含当前history摘要的持久结果快照，同版本GET及原键重放一致；后续其他动作后，GET反映最新全局状态，旧键仍返回原revision快照。旧不可变历史结果不改写。

统一动作字段：actionKey（run内稳定键）、expectedRunVersion（正整数）、expectedAttempt（正整数）。每个实际成功动作run revision+1；重放返回原结果不加。初始run revision=1、attempt=1。

POST /workflow-runs/{run}/steps/{code}/execute：

```json
{"actionKey":"manual-brief-1","expectedRunVersion":1,"expectedAttempt":1,"documentId":"00000000-0000-4000-8000-000000000100","documentVersion":1,"patch":{"body":"人工拟采纳正文"},"reviewNote":"","deriveKind":null}
```

patch只允许brief/body/scenes/prompt完整字段替换（brief为现Document完整五字段，scenes为完整数组）。id/productId/parentId/kind/version/updatedAt等字段不能patch，不接受任意JSON路径。MANUAL必须有patch，REVIEW必须有非空reviewNote且可无patch；REVIEW同样只确认人工候选，不自动发布素材或上传。documentId必须属于当前run，商品/店铺范围由服务端校验。若模板documentKind不同，必须明确deriveKind且匹配该step；例如scripts。execute保存候选后WAITING_REVIEW，正文不变。派生时候选document是从patched父稿产生的完整冻结新稿，父doc/version作为输入依据另存；采纳一次创建同一候选ID/version1，除服务端updatedAt外保持冻结payload一致，防默认图文prompt或视频节点继续使用旧父稿文本。

POST /workflow-runs/{run}/steps/{code}/accept：

```json
{"actionKey":"accept-brief-1","expectedRunVersion":2,"expectedAttempt":1,"candidateId":"00000000-0000-4000-8000-000000000101","documentVersion":1}
```

只传持久候选ID，不重新提交候选正文。采纳校验输入文档版本并原子保存/派生+引用+DONE+下游解锁；文档已被编辑返回409 VERSION_CONFLICT和current文档，候选保留。run/attempt过期409；同键异请求409 IDEMPOTENCY_MISMATCH；当前候选失效409 ILLEGAL_TRANSITION。素材证明不可由客户端提交或绕过。

POST /workflow-runs/{run}/steps/{code}/skip及/redo：

```json
{"actionKey":"skip-reference-1","expectedRunVersion":3,"expectedAttempt":1,"reason":"本次无补充参考"}
```

skip仅新版模板optional=true且READY；旧版及必需步409，空白原因422。redo要求DONE/SKIPPED/FAILED/WAITING_REVIEW，按闭包递增attempt并保留历史；闭包RUNNING返回RESULT_UNKNOWN且不重发。完成后run可redo重新RUNNING；ABANDONED不可动作。完成不触平台动作。

可选模板golden：

```json
{"schemaVersion":2,"code":"manual_optional_v2","name":"人工可选参考","productLines":[],"steps":[{"code":"brief","name":"需求","kind":"MANUAL","documentKind":"planning","dependsOn":[]},{"code":"reference","name":"参考","kind":"MANUAL","documentKind":"planning","dependsOn":["brief"],"optional":true},{"code":"script","name":"脚本","kind":"MANUAL","documentKind":"scripts","dependsOn":["brief","reference"],"optionalDependsOn":["reference"]}]}
```

缺schemaVersion为1且拒绝可选字段，全部必需；V2 optional默认false、optionalDependsOn默认空，必须为本步dependsOn唯一子集。SKIPPED仅在下游显式optionalDependsOn中才能满足依赖。schemaVersion与模板整数version分离，新语义通过追加模板版本生效，不能更新旧系统seed或在途run。

AI_ASSISTED/GENERATION当前返回422 CAPABILITY_UNAVAILABLE，说明尚未接入且未外部调用，不生成假候选、假job或推进revision。非法结构/字段422；无Cookie401、缺CSRF403、跨scope403/不可见对象404。没有前端UI/付费供应商验收声明。在run锁内写前检查动作历史：已有1000条则拒绝第1001个新动作且不改正文/状态/事件，原键重放不受配额阻断；候选文本4MiB/1000候选上限也明确拒绝，当前不支持分页。
