# V9.0 多平台前后端 API 契约

日期：2026-10-23。前端：当前项目；后端协作：陆文龙。本文件描述**待后端实现或逐项确认**的驭量 API，不声称这些端点已经部署。没有新增后端路由、数据库、平台执行器或支付逻辑。

## 1. 已读取的依据与版本

完整读取 `platform-api-reference/易致驭量V9.0_多平台功能重规划.md`、规范化 JSON 的1818条记录，以及四份 HTML 内嵌的全部接口数据。导入脚本逐条检查规范化路径：腾讯添加 `/v3.0/`，聚光添加 `/api/open`；巨量、小店保持原路径。保留原始标识和规范化路径，不按接口后缀猜测 HTTP 方法。

| 来源 | 条目 | 资料明确标注已核验路径 | 未提供路径 | HTTP 方法 |
|---|---:|---:|---:|---|
| 腾讯营销 API 3.0 | 367 | 未声明 | 0 | 原资料未给出 |
| 巨量引擎 | 1070 | 未声明 | 0 | 原资料未给出 |
| 微信小店 | 288 | 未声明 | 0 | 4 GET、284 POST |
| 聚光 | 93 | 43 | 50 | 原资料未给出 |

目录不包含应用 scope 授权、账户授权或成功联调证明。巨量目录不能确定千川专属适配器。微信豆专属写接口不在这四份目录中。

代码类型以 `src/lib/advertising.ts`、`src/lib/platform-catalog.ts` 和两份对应 Service 为准。`src/data/platform-api-catalog.json` 提供可共享的稳定 `catalogId`、原始文件、路径与 scope。生成方式：`.runtime/node.exe scripts/import-platform-catalog.mjs`。SHA256 对源文本统一 LF 后计算。

## 2. 传输与隔离

复用现有 `ApiClient` 的 Cookie 会话、`credentials: include`、`cache: no-store`、超时及错误处理。响应外壳沿用：

```json
{"schemaVersion":"command-job-v1.0","requestId":"后端请求ID","data":{}}
```

新建配置内部版本：`advertising-v9.0`；资源工作区内部版本：`platform-workspace-v9.0`。正式响应 `source` 必须为 `API`。Mock 不实现这些端点，也不返回可用能力或成功结果。

隔离键至少为：`tenantId + businessEntityId + shopId + provider + productLine + accountId + nativeObjectId`。时间范围、原生父对象与筛选条件也参与读取缓存键。所有读取取消过期请求；服务端不响应取消时，旧上下文结果也不能覆盖新页面。

`provider`：`tencent / ocean / xhs / wxshop / beans`。

`productLine`：`adq / shop / beans / ocean_marketing / qianchuan / dou_plus / local_push / juguang / wxshop`。

经营主体、店铺、账户独立建模，使用多对多 `links` 关联。`BOUND` 仅表示有关联，`AUTHORIZED` 才表示该关联已获权；两者均不能替代操作级授权。

## 3. 配置工作台端点

全部相对现有后端地址，统一前缀 `/v1/delivery/configuration/`。

| 方法 / 路径 | 请求 | 返回 |
|---|---|---|
| GET `catalog?product=` | 独立产品 ID | `ConfigurationCatalog`：主体、当前成员可访问的店铺、产品线账户、授权与 writable；店铺归属必须与账户主体一致 |
| GET `capabilities` | 完整 `ConfigurationContext`，数组以逗号连接 | `ConfigurationCapability`，有效上下文键、能力版本、API版本、时效、字段、单位、权限与核验凭证 |
| GET `options` | 上述上下文 + kind、q、cursor、parentId、category | `LookupPage`，合法资源 ID、平台 ID、名称、可用性、状态、规格、后续游标 |
| POST `audience-estimates` | configuration | 真实上下界、估算时间、口径。仅 `estimateSupported` 时允许请求 |
| POST `materials` | multipart：context JSON、file | 已有内容工厂资源的 `sourceMaterialId`、平台资源 ID、审核状态和规格；不得建立另一套素材系统 |
| POST `validations` | configuration、capabilityVersion | 全量字段/组合/权限/资金/资源/排期检查，一次性 validationToken、有效期、审批要求 |
| POST `tasks` | 相同完整配置、能力版本、validationToken、idempotencyKey | 后端任务状态、业务 ID、平台状态、回读核验状态 |
| GET `tasks?product=&accountId=&idempotencyKey=` | 原幂等键 | 原任务结果；结果未知时不自动创建第二次 |
| POST `tasks/{taskId}/cancellations` | product、accountId、expectedVersion、原创建幂等键、新撤回幂等键 | 原创建任务的新版本与撤回状态，保留原创建幂等键供查询 |

`ConfigurationContext` 包括产品、场景、经营主体、账户、店铺、目标、对象 ID 列表、版位、优化/投放策略、出价方式、订单类型、资金类型、成本模式、素材方式。返回的 `contextKey` 必须与前端类型函数完全一致。不能只按账户缓存所有目标或出价方式的能力。

`options.kind`：regions、businessDistricts、customLocations、interests、behaviors、intentions、keywords、audiences、targets、creatives、platformTemplates、creators、groups、portraitCertificates。

资源必须来自真实 API，并返回 `id / platformId / name / eligible`。关键词也需平台核验，不能把任意输入词当作合法标签。业务资源对象一律保留后端 ID 与平台 ID；中文名称只用于展示。

素材预览只接收 HTTPS 地址，不把本地选中文件称作已上传。资源在审核中、无授权或已失效时，`eligible=false` 并给出原因。所有人群包的来源、授权、状态和兼容性由后端验证。

### 字段状态与核验凭证

每个字段有独立 `state / reason / required / options / min / max / step / unit / specs`。状态枚举：

| state | 含义 |
|---|---|
| PUBLIC | 公开资料支持；只可编辑本地意向，不是账户许可 |
| AVAILABLE | 当前账户和上下文可用 |
| API_PENDING | API 支持但后端未接入 |
| AUTH_REQUIRED | 需要授权 |
| NOT_APPLICABLE | 当前场景不适用 |
| UNCONFIRMED | 需进一步确认 |

缺失字段默认不开放；未授权账户不能通过 `AVAILABLE` 字段绕过账户门控。字段选项也可单独禁用并说明原因。版位、目标、账户、出价、资金或订单模式变化会清理依赖字段，随后重新读取有效能力。

`verification` 独立记录：catalogIds、pathVerified、applicationScopeGranted、accountAuthorized、backendIntegrated、requestSchemaVerified、executionVerified、proofId、checkedAt。API版本与实际请求方法必须由后端凭证确认；不能从目录条数推断。

真实创建必须同时满足：正式模式、实际关联账户 writable、成员权限、有效能力、相关创建目录依据、路径/应用scope/账户/后端/参数/真实执行核验全部通过、前端校验无错误、后端校验 token 与完整配置完全匹配、用户确认风险、没有未核查的幂等记录。

### 金额与排期

草稿 `budget.amount` 和 `bidValue` 保存十进制字符串。`CNY_MINOR` 页面按元输入，前端精确换算分用于上下限检查；`BEAN` 使用整数微信豆；`RATIO` 为比例。后端依据能力版本进行最终转换，不从固定出价或默认币种推断。不同资金类型切换清空金额，避免沿用另一种资金单位。

预算类型、出价方式和 ROI/目标成本单位全部来自能力；年龄上下界/允许值、最多标签数、人群包规则也由能力返回。日期按北京时间，结束须晚于开始；每日时段验证星期、有效时间、跨日拆分和重叠。

## 4. 多平台资源与经营闭环端点

统一前缀 `/v1/delivery/integrations/`。

| 方法 / 路径 | 用途 |
|---|---|
| GET `context?productLine=` | 返回 `IntegrationContext`：主体、店铺、原生账户、多对多关联、每个 catalogId 的操作类型、资源、权限、可编辑字段、七阶段凭证、有效期 |
| POST `queries` | 只读：scope、resource、filters(search/cursor/nativeParentId)。不得借此触发写入 |
| POST `previews` | scope、change(catalogId/targetId/nativeId/expectedVersion/changes/reason)，返回原值/目标值/校验/审批要求和 token |
| POST `commands` | 相同 scope/change + previewToken + idempotencyKey，通过审计网关提交 |
| GET `commands?scopeKey=&idempotencyKey=` | 回读原申请，不自动重放 |
| POST `commands/{taskId}/cancellations` | 撤回后端明确标记 cancellable 的未执行申请，校验 expectedVersion 和新的幂等键 |

资源种类：campaigns、adgroups、promotionOrders、creatives、targeting、keywords、reports、funds、products、stock、orders、lives、compass、settlements、aftersales、distribution、audit、decisions、goals、allocations、executions。

DOU+ 使用 promotionOrders，不能返回 project/promotion 伪装订单；聚光分 campaign/unit/creative，并以真实原生父 ID 下钻。千川未提供专属路径时 operations 为空。微信小店所有资源都是经营/商品数据；推广联盟计划不能进入广告计划表。

查询返回 `PlatformResourcePage`，外层及每行 `scopeKey` 均需匹配。每行仅接收原生 ID、名称、状态、版本、更新时间、允许的数字指标、归因窗口、资金类型和真实关联 ID。未知字段及订单个人信息被适配器丢弃。

金额类型严格区分：AD_BUDGET 配置额度、AD_CASH_BALANCE 广告钱包、MERCHANT_SETTLEMENT 店铺结算、BEAN 微信豆。经营数据不能返回广告钱包余额，广告数据不能夹带店铺结算。缺失值保留 null，真实零保留 0。

报表需同时给出 syncedAt、timezone、granularity、attributionWindow、refundHandling、deduplication、warnings。前端不会把不同日期/窗口/退款口径直接求和，也不将店铺支付 GMV直接代作广告归因 GMV。

新读操作要求目录、路径、scope、账户、后端、参数核验；读操作不要求此前已有写入执行。平台修改还要求真实执行核验、成员权限、资源版本、差异预览和二次确认。

## 5. 结果、错误与幂等

配置任务：PENDING / AWAITING_APPROVAL / PAYMENT_REQUIRED / PROCESSING / SUCCEEDED / FAILED / REJECTED / CANCELLED。平台变更沿用后端完整任务状态，回读字段为 PENDING / MATCHED / UNKNOWN / MISMATCH。SUCCEEDED 必须包含平台业务 ID 且回读 MATCHED，否则前端拒绝把它当作已完成。撤回须由后端明确允许，携带原创建幂等键与 expectedVersion；撤回结果未知时查询原任务，不自动重发。

404：接口待接入，保留草稿，不假报创建成功。401/403：清晰提示登录或授权失效。409：版本冲突，必须重新回读和预览。422：无副作用的参数拒绝。429：限流，不自动重放写入。网络/超时/无效响应：按结果未知保留原幂等键，锁定重复提交并提供查询。

后端必须保证同一幂等键返回同一任务，校验 token 绑定完整 payload、账户、能力版本、有效期；所有审批、资金上限与平台副作用只由服务端处理。Cookie、平台Secret、校验 token 和授权凭证不写入本地草稿。

## 6. 陆文龙待提供

1. 上述两套驭量契约的已部署地址、正式/沙箱模式和错误码；不要仅反馈“平台开放 API 存在”。
2. 主体、店铺、账户、视频号、直播间、商品原生 ID 的真实授权关系和成员权限。
3. 腾讯 3.0 的实际方法、scope、优化目标权限、目标/版位/定向/出价组合、素材规格与审核、覆盖估算实测。
4. 巨量营销 / 千川 / DOU+ / 本地推分别使用的域名、版本、账户权限、参数与回读样本。
5. 聚光 50 条未核验路径，尤其新创编、简单投、定向包；43条带标记路径的实际 HTTP 方法与授权。
6. 微信小店的商品/库存/订单/罗盘/售后/资金报表、刷新延迟、退款口径和广告归因对账规则。订单/资金响应须最小化个人信息。
7. 微信豆专属合法写接口及授权证明；没有时只保留草稿、方案导出和官方人工入口。

接口契约及界面已经准备，真实联调、平台创建与支付不属于本次前端完成证明。
