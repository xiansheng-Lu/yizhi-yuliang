# M3-18 独立选题技术切片

2026-10-28，BE/F10。本切片状态TECH_VERIFIED_WAITING_REVIEW；M3-18整卡仍IN_PROGRESS，不是整卡完成或最终BE/F10签字。基线main bf2b2f6a071a53e716ba9e8560fccfe45991e179，本地分支codex/backend-topic-adgroup-queries。root明确授权本切片，真实模型/付费/平台调用0，应用默认runtime关闭，提示资源DRAFT。本轮已在root明确独占窗口实际运行，临时空库升级至head0073。

## 范围与依据

20册M3-18要求各aiScenario独立提示、候选/上下文与调用来源；16册110-113明确brief→topic→planning→script→storyboard，topic/planning都落kind=planning的body。当前已交付三场景只是topic借content_planning的兼容别名，本切片新增独立content_topic/prompt_key，不新增前端topics字段或选择器、不把选题写成未确认的完整脚本。

模型输出仍为严格{body:字符串}，只能对应topic步骤、目标planning.body。旧content_planning→topic别名保留，旧三场景资源字节、模板hash和version1不改。新资源content_prompts/topic/v1包含DRAFT模板/manifest/golden。0073_content_topic接0072_ai_ledger_actor_guard，只前向扩content_ai_requests.ai_scenario CHECK，不改已发布0069或旧请求/seed/在途run。

复用已验证的当前租户/店铺/commerce商品范围、受控上下文/PII与令牌过滤、报价/许可、MODEL调用复合关联、候选不提前写正文、晚回复拒绝、人工版本采纳、完成槽与业务先于费用锁序。没有新供应商配置、密钥读取、付费许可、平台操作或公共路由。新aiScenario由原AI路由接受，F10消费与最终具名契约复核仍另验。

## 静态验证

8项新增topic单元与原26项AI单元共34 passed，1.52秒。Ruff含0073 Python迁移通过；3改动源码+2新增测试mypy通过。

旧场景version1哈希固定回归：

- content_planning：720f870f224db2cfbc37d5053be9977b01031a069af763fa9ef69fa461d98ecb
- content_script：3dc695d148fb989df49955c1922dacd8471b4e5af865c37aa3e0d209333ce52a
- content_storyboard：d70561d5cc3667dd60d863bf8362d00f70932c0077544f8eb9495a48b93d011d

content_topic独立version1哈希：be3a95cbad5ebccec52667ebd518a9abdb078f4b4ae7763b5214067e039ea981。哈希只表明实际模板来源，不是人工质量评分或发布批准。

## 实际独占窗口验证

入口tests/integration/test_ai_content_topic.py，6项：

1. 真实只读系统策划模板创建run，MANUAL brief执行/采纳后topic READY；真实HTTP prepare/confirm产出MODEL候选且body不变；人工HTTP accept一次写planning.body，planning READY而script PENDING，prompt/model/call来源明确为content_topic。
2. brief依赖未完成不启动选题、不建调用请求。
3. content_topic不能用于planning等其他step，保持声明落点。
4. 旧topic→content_planning别名仍从原提示产出候选。
5. 回复期间人工改稿，旧回复STALE且不解锁planning。
6. 模型额外身份字段拒绝，无候选/正式正文覆盖。

全部使用隔离PG和本地合成HTTP模型fixture，报价/价格/预算均合成；测试中的ACTIVE prompt只登记于临时数据库，不发布生产DRAFT资源。本轮按授权串行一次执行：ai-content-topic 6 passed，10.11秒；随后必要ai-content 17 passed，17.55秒。无失败/源码修复，无真实模型、平台或付费调用；没有运行full。当前head0073已实际升级通过，DB窗口结束明确释放root/beans。

本子任务不改公共main/runner/台账/Git、beans文件或06/12。源码/SQL/测试及证据现已冻结，后续最终全量、复核与Git由root统一处理；完整商业模型质量、F10浏览器和其余caption/ASR场景均不在本次完成声明内。
