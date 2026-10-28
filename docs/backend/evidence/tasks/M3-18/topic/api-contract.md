# F10 独立选题场景增量合同proposal

本文件补充M3-18原三场景合同；Document字段没有改变，最终BE/F10签字、浏览器验收与真实供应商P验收仍保留。runtime缺省关闭，新提示资源DRAFT，临时测试库ACTIVE不代表生产发布。

沿用Cookie/CSRF/X-Shop-Id、原prepare/confirm和人工accept接口，只增加aiScenario=content_topic：

```json
{"requestKey":"topic-attempt-1","expectedRunVersion":3,"expectedAttempt":1,"documentId":"00000000-0000-4000-8000-000000000100","documentVersion":2,"aiScenario":"content_topic","assetIds":[]}
```

POST `/v1/content/workflow-runs/{run}/steps/topic/ai/prepare`。示例revision3/version2对应系统模板初始brief执行并采纳后，实际值以GET当前状态为准。只有topic READY、AI_ASSISTED且documentKind=planning可调用；content_topic不能借用于planning/script等step。缺brief依赖时不调用模型、不建请求。

只返回body候选，模型没有topics数组、商品或身份字段；规划文档body仍需人工accept才更新。独立prompt_key=content_topic/version1及hash/model/call引用可核对。topic采纳后planning READY、script仍PENDING，不自动发下一次付费调用。输出golden示例为带简短理由的选题方向，不代表已核实商品事实或完成完整策划。

旧topic使用content_planning的别名继续接受，原三prompt文本/hash/version不变。切换场景必须使用新稳定请求键，不能拿旧requestKey覆写旧提示或候选来源。报价/确认/一次性许可、PII白名单、旧回复STALE和跨actor幂等冲突规则全部沿用现链。

本轮真实系统模板与HTTP合成模型6专项通过，原AI17回归通过；真实模型质量、完整caption/ASR/成片时长、F10 UI和最终具名复核未验收。
