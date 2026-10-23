# 易致驭量前端 RESTful API 全量接口文档 V1.0

导出日期：2026-10-22（Asia/Shanghai）。接收方：后端负责人陆文龙。范围：已交付 G1-B1 前端，基线提交 `84e3a6f51ccd960442c09ec9985079bd04703fe3`。这是当前前端实际声明的对接契约，后端尚未确认或完成联调；导出不修改前端业务代码，也不新增后端实现。

当前共有 **11 个 HTTP 操作、11 个 URL 模板、21 个 PC 业务路由**。21页的数据统一由快照承载，页面数量不等于API数量。没有调用的后续业务接口在第9节列为待定义，不能把它们当作已存在的接口。所有JSON样例均是构造的协议示例，包含API/PLATFORM来源时也不代表已有真实平台执行。

## 1 使用导出包

中文说明覆盖请求、响应、字段、状态、权限、错误和联调步骤。`openapi.json`为OpenAPI 3.1格式，`postman_collection.json`和`postman_environment.json`供接口调试导入；`examples`提供每个操作的独立请求与完整响应。`validation.txt`记录本地结构与样例检查，`source-manifest.json`记录代码来源、SHA-256和基线。

Postman导入后先将baseUrl替换为**已授权测试后端地址**，填写测试账号并完成Cookie会话。包内默认地址为不可用的example.invalid，不会自动连接生产。任务流程使用jobId/jobVersion变量，成功响应会更新它们；新逻辑请求须更换idempotencyKey，重试同一逻辑请求须复用原键。先逐项运行，勿把包含取消、更新配置和退出登录的全量集合直接当作一条正常业务流水运行。

## 2 连接与通用约定

- 前端服务地址为 `http://127.0.0.1:3000`；后端baseUrl由陆文龙提供，不能误填此前端地址。当前没有Next.js业务Route Handler。
- 设置 `NEXT_PUBLIC_DATA_MODE=api`、`NEXT_PUBLIC_API_BASE_URL=<后端根地址>` 后重启前端；baseUrl不重复附加/v1，因为各请求路径自带/v1。构建模式需重新构建。
- 请求均 `credentials: include`、`cache: no-store`；Accept:application/json；有body时Content-Type:application/json。当前不发送Authorization、CSRF或Idempotency-Key请求头。
- Cookie名、Secure/SameSite/HttpOnly属性、续期、跨域CORS白名单及CSRF协议待确认；导出没有虚构具体Cookie名。前端路由守卫不是安全边界。
- 当前API Client超时15秒，超时显示TIMEOUT和结果未知；不自动重试写请求，不回退Mock。
- 命名必须保持：快照/基础实体多为camelCase，command_job和ReportingScope为snake_case。不得擅自统一命名后直接返回，否则现有前端读不到字段。
- 金额采用CNY整数分，5000元=500000分、3.20元=320分；基础实体的*Minor字段也默认CNY，当前没有单独currency字段。ROI是无单位正比值，不是出价或百分比。
- 事件时间用UTC ISO 8601，UI按Asia/Shanghai展示；统计date是该时区自然日。未知允许null的数值返回null，集合为空返回[]，不可把未知伪装成0。

成功信封必须是JSON，即使data为null：

```json
{
  "data": {},
  "requestId": "example-request",
  "schemaVersion": "command-job-v1.0"
}
```

`schemaVersion`固定command-job-v1.0（快照同值），`requestId`必须为字符串，`data`键必须存在；CommandJob内部另有`schema_version:"1.0"`。当前所有成功方法均调用response.json()，因此**204、空body、HTML登录重定向页面会触发INVALID_RESPONSE**；请返回2xx JSON。API模式Identity.mode、CommandJob.mode须为API，快照和复盘source为API，风险source为SERVER，执行回执source为PLATFORM。

错误信封类型已声明，但当前客户端只根据HTTP状态映射通用错误，**不会读取后端error.message、requestId或Retry-After来展示或重试**。以下错误包供后端诊断与未来前端接入，不能依赖其中的具体message已在UI展示：

```json
{
  "error": {
    "code": "CONFLICT",
    "message": "任务版本已变化，请刷新后重新确认"
  },
  "requestId": "example-request-conflict"
}
```

| HTTP / 客户端事件 | 当前前端代码 | 行为 |
|---|---|---|
| 400或422 | INVALID_INPUT | 显示输入/契约错误 |
| 401 | UNAUTHENTICATED | getIdentity转为null并进入登录；其他请求显示错误 |
| 403 | FORBIDDEN | 显示无权限，不改本地任务为批准 |
| 404 | NO_DATA | 资源不存在 |
| 409 | CONFLICT | 版本/幂等冲突，应刷新后重新确认 |
| 429、5xx及其余非2xx | NETWORK_ERROR | 当前统一显示请求错误，不自动重试 |
| 网络失败 | NETWORK_ERROR | 失败，不回退Mock |
| 超时/中断 | TIMEOUT | 结果未知，先核对任务，禁止盲目重发 |
| JSON/版本/关键来源或任务校验失败 | INVALID_RESPONSE | 拒绝该响应 |
| API地址为空 | API_NOT_CONFIGURED | 不发请求 |

## 3 HTTP操作总表

| # | 方法 | 路径 | 用途 | 权限 |
|---|---|---|---|---|
| 1 | GET | `/v1/auth/session` | 获取当前会话 | 已登录；无会话返回401 |
| 2 | POST | `/v1/auth/login` | 登录 | 无需已有会话；后端认证 |
| 3 | POST | `/v1/auth/logout` | 退出登录 | 当前会话 |
| 4 | GET | `/v1/frontend/snapshot` | 读取全量前端快照 | snapshot.read |
| 5 | POST | `/v1/command-interpretations` | 解析自然语言指令 | task.create（当前Mock/UI建议，后端确认） |
| 6 | POST | `/v1/command-jobs` | 创建标准任务 | task.create |
| 7 | POST | `/v1/command-jobs/{job_id}/confirm` | 确认对象与风险并提交审批 | task.create |
| 8 | POST | `/v1/command-jobs/{job_id}/approvals` | 批准或驳回任务 | task.approve |
| 9 | POST | `/v1/command-jobs/{job_id}/cancel` | 取消未执行任务 | task.create（当前Mock/UI；正式规则待确认） |
| 10 | PUT | `/v1/risk-policies/{id}` | 更新经营目标与风控配置 | policy.edit |
| 11 | PUT | `/v1/frontend/reporting-scope` | 更新共享统计范围 | snapshot.read，且对象属于授权范围 |

权限名来自当前Mock适配器和UI，用于后端对齐。正式鉴权必须由后端独立验证用户、租户、店铺、账户及目标对象，不能信任浏览器字段或permissions。task.execute当前只控制演示推进按钮，API没有对应执行请求；是否暴露给正式身份需要后端确认。

## 4 各接口请求与响应

### 1 获取当前会话

`GET /v1/auth/session`

权限：已登录；无会话返回401。当前客户端把401转换为null，然后由路由守卫进入登录页。其他错误保留为错误状态。返回mode必须为API；tenantId非空、permissions必须为数组。

请求：无请求体，无query参数。

成功：HTTP 200，`ApiSuccess<Identity>`。完整可用JSON见 [响应样例](examples/getSession.response.json)。


### 2 登录

`POST /v1/auth/login`

权限：无需已有会话；后端认证。后端建立正式会话。当前前端仅发送email/password；验证码、OAuth、CSRF、续期和Cookie属性均待确认。不要沿用Mock固定账号作为正式认证。

请求：`LoginRequest`，application/json。无其他query参数。

```json
{
  "email": "operator@example.invalid",
  "password": "示例占位值，不是真实密码"
}
```

成功：HTTP 200，`ApiSuccess<Identity>`。完整可用JSON见 [响应样例](examples/login.response.json)。


### 3 退出登录

`POST /v1/auth/logout`

权限：当前会话。无请求体。后端撤销会话并返回带data:null的JSON信封；当前客户端会调用response.json()，204或空响应会被视为INVALID_RESPONSE。

请求：无请求体，无query参数。

成功：HTTP 200，`ApiSuccess<null>`。完整可用JSON见 [响应样例](examples/logout.response.json)。

```json
{
  "data": null,
  "requestId": "example-request",
  "schemaVersion": "command-job-v1.0"
}
```


### 4 读取全量前端快照

`GET /v1/frontend/snapshot`

权限：snapshot.read。当前没有查询参数、分页参数或If-None-Match。返回当前身份授权范围内的全部实体及当前scope，前端再按scope筛选计划、指标和任务。所有十二个集合键必须是数组，即使为空；不得把分页对象直接放进数组字段。源必须为API、时区Asia/Shanghai，每条任务须通过正式任务检查。

请求：无请求体，无query参数。

成功：HTTP 200，`ApiSuccess<FrontendSnapshot>`。完整可用JSON见 [响应样例](examples/getSnapshot.response.json)。


### 5 解析自然语言指令

`POST /v1/command-interpretations`

权限：task.create（当前Mock/UI建议，后端确认）。只返回查询或动作候选，不创建、不审批、不执行任务。QUERY关联已有快照场次，不包含直播数据本体；场次或计划不唯一时给出候选，用户选择后才允许创建。UNRECOGNIZED.text用于解释无法解析的原因。

请求：`InterpretCommandRequest`，application/json。无其他query参数。

```json
{
  "text": "把千川A计划日预算调到5000",
  "scope": {
    "shop_id": "example-shop",
    "live_session_id": "example-live",
    "connector_id": "all",
    "date": "2026-10-22",
    "timezone": "Asia/Shanghai"
  }
}
```

成功：HTTP 200，`ApiSuccess<CommandInterpretation>`。完整可用JSON见 [响应样例](examples/interpretCommand.response.json)。


### 6 创建标准任务

`POST /v1/command-jobs`

权限：task.create。允许PC_MANUAL或MOBILE_COMMAND；浏览器ApiClient拒绝AUTOPILOT。后端从对象推导tenant/account/old_value，生成job_id、风险、版本和有效期。正常为DRAFT；业务风控阻断可返回完整BLOCKED任务供UI展示。创建不是确认、审批或执行。幂等键在JSON请求体，不是Idempotency-Key请求头。

请求：`CreateJobRequest`，application/json。无其他query参数。

```json
{
  "source": "MOBILE_COMMAND",
  "shop_id": "example-shop",
  "target_type": "AD_PLAN",
  "target_id": "example-plan",
  "action_type": "CHANGE_DAILY_BUDGET",
  "new_value": {
    "kind": "MONEY",
    "currency": "CNY",
    "amount_minor": 500000
  },
  "intent": "把千川A计划日预算调到5000",
  "idempotency_key": "example-idempotency-001"
}
```

成功：HTTP 200或201，`ApiSuccess<CommandJob>`。完整可用JSON见 [响应样例](examples/createJob.response.json)。


### 7 确认对象与风险并提交审批

`POST /v1/command-jobs/{job_id}/confirm`

权限：task.create。路径与请求体job_id保持一致。重新核对旧值、对象、规则版本和权限，普通动作DRAFT→PENDING_APPROVAL；直播交接DRAFT→MANUAL_REQUIRED；条件变化返回BLOCKED或409。

请求：`JobVersionRequest`，application/json。无其他query参数。

```json
{
  "job_id": "example-job-001",
  "expected_version": 1
}
```

成功：HTTP 200，`ApiSuccess<CommandJob>`。完整可用JSON见 [响应样例](examples/confirmJob.response.json)。


### 8 批准或驳回任务

`POST /v1/command-jobs/{job_id}/approvals`

权限：task.approve。只对待审批版本处理APPROVE或REJECT。当前返回值必须是完整CommandJob，不是单独Approval。人工批准只进入APPROVED，不能直接宣称执行成功。审批职责分离及低风险SERVER_POLICY授权由后端决定。

请求：`ReviewJobRequest`，application/json。无其他query参数。

```json
{
  "job_id": "example-job-001",
  "expected_version": 2,
  "decision": "APPROVE"
}
```

成功：HTTP 200，`ApiSuccess<CommandJob>`。完整可用JSON见 [响应样例](examples/reviewJob.response.json)。


### 9 取消未执行任务

`POST /v1/command-jobs/{job_id}/cancel`

权限：task.create（当前Mock/UI；正式规则待确认）。当前演示允许DRAFT/PENDING_APPROVAL/APPROVED取消，EXECUTING/VERIFYING不可从前端直接取消。过期、终态或版本过旧返回409。后端应确定审批后取消的权限和额度释放规则。

请求：`JobVersionRequest`，application/json。无其他query参数。

```json
{
  "job_id": "example-job-001",
  "expected_version": 2
}
```

成功：HTTP 200，`ApiSuccess<CommandJob>`。完整可用JSON见 [响应样例](examples/cancelJob.response.json)。


### 10 更新经营目标与风控配置

`PUT /v1/risk-policies/{id}`

权限：policy.edit。发送完整RiskPolicy，包括id、tenantId、version。后端不得信任主体字段；检查版本后递增并返回新对象。保存经营ROI目标不直接改计划ROI或出价；更改AUTOPILOT工作模式不授予自动审批。当前UI使用riskPolicies[0]，有效配置应排第一。

请求：`RiskPolicy`，application/json。无其他query参数。

```json
{
  "id": "example-policy",
  "tenantId": "example-tenant",
  "dailyBudgetMinor": 2000000,
  "targetRoi": 6,
  "maxChangePercent": 50,
  "cooldownMinutes": 5,
  "mode": "COPILOT",
  "version": 1
}
```

成功：HTTP 200，`ApiSuccess<RiskPolicy>`。完整可用JSON见 [响应样例](examples/updatePolicy.response.json)。


### 11 更新共享统计范围

`PUT /v1/frontend/reporting-scope`

权限：snapshot.read，且对象属于授权范围。返回data:null的JSON信封。后续GET快照应返回该scope，并保留有权访问的实体以便切换；当前客户端忽略PUT的返回data，随后reload。scope的存储维度、跨设备共享方式待确认，不能据客户端all返回越权数据。

请求：`ReportingScope`，application/json。无其他query参数。

```json
{
  "shop_id": "example-shop",
  "live_session_id": "example-live",
  "connector_id": "all",
  "date": "2026-10-22",
  "timezone": "Asia/Shanghai"
}
```

成功：HTTP 200，`ApiSuccess<null>`。完整可用JSON见 [响应样例](examples/setScope.response.json)。

```json
{
  "data": null,
  "requestId": "example-request",
  "schemaVersion": "command-job-v1.0"
}
```



## 5 全量数据字典

以下表覆盖当前前端实际消费的所有基础实体、快照和任务嵌套模型。必填表示键必须存在；`null`表示键存在但值可空，和可省略不同。源文件的Mock/API联合类型在OpenAPI中收紧为正式API来源，Mock专用mock_scenario不导出为正式字段。约束字段的最小值、动作与值对应、对象归属、时间先后及SUCCEEDED回执条件仍需要后端业务校验，不能仅靠通用JSON Schema。

### ConnectorId

```text
"adq" / "wxpromo" / "beans" / "wxshop" / "qianchuan" / "douyinshop" / "juguang" / "redshop"
```

### Identity

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| userId | string | 是 | 服务端用户ID |
| displayName | string | 是 | 运营身份显示名 |
| tenantId | string | 是 | 所属企业ID |
| shopIds | string[] | 是 | 会话可访问店铺ID列表 |
| permissions | string[] | 是 | 权限字符串数组，见权限表 |
| mode | "API" | 是 | 正式会话固定API，不能返回Mock身份 |

### Tenant

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| name | string | 是 | 显示名称 |

### Shop

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| tenantId | string | 是 | 所属企业ID |
| name | string | 是 | 显示名称 |
| connectorId | ConnectorId | 是 | 独立平台连接器ID |

### AdAccount

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| tenantId | string | 是 | 所属企业ID |
| shopId | string | 是 | 所属店铺ID |
| connectorId | ConnectorId | 是 | 独立平台连接器ID |
| name | string | 是 | 显示名称 |

### LiveSession

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| tenantId | string | 是 | 所属企业ID |
| shopId | string | 是 | 所属店铺ID |
| name | string | 是 | 显示名称 |
| sourceUpdatedAt | string (date-time) | 是 | 数据源更新时间，UTC ISO 8601 |
| fetchedAt | string (date-time) | 是 | 后端采集时间，UTC ISO 8601 |
| online | integer 或 null | 是 | 在线人数；未知为null |
| productClicks | integer 或 null | 是 | 商品点击数；未知为null |
| status | "MOCK_LIVE" / "UNKNOWN" | 是 | 当前 TS 仅 MOCK_LIVE / UNKNOWN。真实 API 请勿伪装 MOCK_LIVE；暂用 UNKNOWN。LIVE / ENDED 等真实状态必须先双方确认并修改前端模型和UI。 |

### AdPlan

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| nativePlanId | string | 是 | 平台原生计划ID，不能替代内部ID |
| tenantId | string | 是 | 所属企业ID |
| shopId | string | 是 | 所属店铺ID |
| adAccountId | string | 是 | 所属广告账户ID |
| liveSessionId | string | 否，可省略 | 关联直播场次；AdPlan中可省略 |
| name | string | 是 | 显示名称 |
| aliases | string[] | 否，可省略 | 计划别名，可省略；不授权前端猜测唯一对象 |
| budgetMinor | integer | 是 | CNY 整数分；禁止发送浮点元。 |
| bidMinor | integer | 是 | CNY 整数分；禁止发送浮点元。 |
| targetRoi | number | 是 | 无货币单位的投放ROI比值，不是出价。 |
| enabled | boolean | 是 | 广告计划开关；不影响已产生的消耗与GMV |
| spendMinor | integer 或 null | 是 | 同范围历史广告消耗，CNY整数分；未知为null |
| paidGmvMinor | integer 或 null | 是 | 同范围支付归因GMV，CNY整数分；未知为null |
| sourceUpdatedAt | string (date-time) | 是 | 数据源更新时间，UTC ISO 8601 |
| attributionWindow | string | 是 | 归因口径/窗口说明，不能与经营利润混用 |

### Product

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| tenantId | string | 是 | 所属企业ID |
| shopId | string | 是 | 所属店铺ID |
| skuId | string | 是 | 商品SKU标识 |
| name | string | 是 | 显示名称 |
| priceMinor | integer | 是 | CNY 整数分；禁止发送浮点元。 |
| stock | integer 或 null | 是 | 库存；未知为null |
| clicks | integer 或 null | 是 | 点击样本量；未知为null |
| paidOrders | integer 或 null | 是 | 支付订单样本数；未知为null |
| speechAdvice | string | 是 | 主播讲解建议/提示文本 |

### Material

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| tenantId | string | 是 | 所属企业ID |
| productId | string | 是 | 关联商品ID |
| name | string | 是 | 显示名称 |
| version | string | 是 | 素材业务版本字符串，例如V1；不是任务整数乐观锁 |
| kind | "VIDEO" / "IMAGE" / "SCRIPT" | 是 | VIDEO视频 / IMAGE图片 / SCRIPT脚本 |
| status | "DRAFT" / "REVIEW" / "READY" | 是 | 素材DRAFT草稿 / REVIEW审核 / READY可用 |

### RiskPolicy

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| tenantId | string | 是 | 所属企业ID |
| dailyBudgetMinor | integer | 是 | CNY 整数分；禁止发送浮点元。 |
| targetRoi | number | 是 | 企业经营ROI目标，无单位；保存不直接更改计划targetRoi或bidMinor |
| maxChangePercent | number | 是 | 预算/出价单次变动百分比，>0且≤100 |
| cooldownMinutes | integer | 是 | 同计划成功动作冷却期，整数分钟≥0 |
| mode | "MANUAL" / "COPILOT" / "AUTOPILOT" | 是 | MANUAL人工指令 / COPILOT辅助 / AUTOPILOT自主投放候选；工作模式不授予审批权限 |
| version | integer | 是 | 经营目标与风控配置的乐观锁版本；保存时递增 |

### ReplaySegment

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| liveSessionId | string | 是 | 关联直播场次内部ID，必须提供 |
| productId | string | 是 | 关联商品ID |
| startSeconds | number | 是 | 片段起点，相对获权视频时间，秒 |
| endSeconds | number | 是 | 片段终点，应大于起点，秒 |
| transcript | string | 是 | 片段转写文本 |
| advice | string | 是 | 片段诊断建议 |
| videoUrl | string 或 null | 是 | 获权视频访问URL或null；签名与授权方案待确认 |

### ReplayReport

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | string | 是 | 内部对象ID |
| liveSessionId | string | 是 | 关联直播场次内部ID，必须提供 |
| source | "API" | 是 | 实际来源；API快照/复盘为API，风控为SERVER，回执为PLATFORM |
| sections | { title: string; evidenceIds: string[]; text: string }[] | 是 | 复盘章节，含title/evidenceIds/text |

### Connector

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| id | ConnectorId | 是 | 八类独立ConnectorId之一，见连接器枚举 |
| status | "UNKNOWN" / "READ_ONLY" / "SUPPORTED" / "UNSUPPORTED" / "DEGRADED" | 是 | 连接器能力核验状态：UNKNOWN未知、READ_ONLY只读、SUPPORTED支持、UNSUPPORTED不支持、DEGRADED降级 |
| scopes | string[] | 是 | 已核验的权限范围，未知不填假授权 |
| checkedAt | string (date-time) 或 null | 是 | 连接器能力核验时间，未知为null |

### ReportingScope

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| shop_id | string | 是 | 具体ID或 all；不得据此扩大服务端授权范围。 |
| live_session_id | string | 是 | 具体ID或 all；不得据此扩大服务端授权范围。 |
| connector_id | string | 是 | all 或 ConnectorId；当前 TS 写为 string，后端须检查枚举与授权。 |
| date | string (date) | 是 | 统计自然日YYYY-MM-DD，Asia/Shanghai |
| timezone | "Asia/Shanghai" | 是 | 固定Asia/Shanghai |

### CommandAction

```text
"CHANGE_DAILY_BUDGET" / "CHANGE_BID" / "SET_ROI_TARGET" / "PAUSE_AD_PLAN" / "RESUME_AD_PLAN" / "REQUEST_LIVE_START" / "REQUEST_LIVE_END"
```

### JobSource

```text
"AUTOPILOT" / "PC_MANUAL" / "MOBILE_COMMAND"
```

### JobStatus

```text
"DRAFT" / "PENDING_APPROVAL" / "APPROVED" / "EXECUTING" / "VERIFYING" / "SUCCEEDED" / "FAILED" / "REJECTED" / "CANCELLED" / "EXPIRED" / "BLOCKED" / "MANUAL_REQUIRED"
```

### JobValue

```text
{ kind: "MONEY"; currency: "CNY"; amount_minor: integer } 或 { kind: "RATIO"; value: number } 或 { kind: "BOOLEAN"; value: boolean } 或 { kind: "HANDOFF"; value: "START" / "END" }
```

### RiskSummary

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| source | "SERVER" | 是 | 实际来源；API快照/复盘为API，风控为SERVER，回执为PLATFORM |
| result | "REVIEW_REQUIRED" / "BLOCKED" / "MANUAL_ONLY" | 是 | 风险结果：REVIEW_REQUIRED/BLOCKED/MANUAL_ONLY |
| level | "LOW" / "MEDIUM" / "HIGH" | 是 | 风险等级：LOW/MEDIUM/HIGH |
| checks | { code: string; passed: boolean; message: string }[] | 是 | 风险检查明细数组，code/passed/message |
| policy_version | integer | 是 | 本次风险检查依据的策略版本 |
| checked_at | string (date-time) | 是 | 风险检查时间 |

### JobApproval

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| state | "NOT_REQUESTED" / "PENDING" / "APPROVED" / "REJECTED" | 是 | 审批状态；不等于任务状态 |
| actor_id | string 或 null | 是 | 后端确认的审批人ID，未审批为null |
| decided_at | string (date-time) 或 null | 是 | 审批决定时间，未决定为null |
| method | "MANUAL" 或 "SERVER_POLICY" 或 null | 是 | MANUAL/SERVER_POLICY/null；前端不能自行授予白名单批准 |
| approved_version | integer 或 null | 是 | 审批对应的任务版本，未审批为null |

### JobReceipt

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| receipt_id | string | 是 | 内部执行回执ID |
| source | "PLATFORM" | 是 | 实际来源；API快照/复盘为API，风控为SERVER，回执为PLATFORM |
| platform_receipt_id | string 或 null | 是 | 真实平台回执ID或null；不得伪造 |
| accepted | boolean 或 null | 是 | true受理/false拒绝/null未知或尚未回执 |
| received_at | string (date-time) 或 null | 是 | 回执收到时间或null |
| verification | "PENDING" / "MATCHED" / "MISMATCH" / "UNKNOWN" | 是 | PENDING/MATCHED/MISMATCH/UNKNOWN，与受理分开 |
| observed_value | JobValue 或 null | 是 | 回读观察值或null |
| verified_at | string (date-time) 或 null | 是 | 核验完成时间或null |

### JobError

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| code | string | 是 | 错误或检查代码；JobError.code为业务自由字符串 |
| message | string | 是 | 可展示的说明，不含敏感平台密钥 |
| retryable | boolean | 是 | 是否可重试的后端提示；前端不得盲目重试写操作 |
| at | string (date-time) | 是 | 事件时间，UTC ISO 8601 |

### CommandJob

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| schema_version | "1.0" | 是 | 任务模型版本1.0，与响应信封版本不同 |
| job_id | string | 是 | 后端生成任务ID；路径和body一致 |
| source | JobSource | 是 | 实际来源；API快照/复盘为API，风控为SERVER，回执为PLATFORM |
| tenant_id | string | 是 | 任务主体企业；由服务端从会话与对象确定 |
| shop_id | string | 是 | 任务/统计店铺；scope允许all但不扩大权限 |
| account_id | string 或 null | 是 | 目标广告账户；直播交接为null |
| live_session_id | string 或 null | 是 | 关联直播场次；任务可null、scope可all |
| target_type | "AD_PLAN" / "LIVE_SESSION" | 是 | AD_PLAN或LIVE_SESSION |
| target_id | string | 是 | 具体计划/场次内部ID |
| action_type | CommandAction | 是 | 独立动作，预算、出价、ROI及直播交接不可混淆 |
| old_value | JobValue 或 null | 是 | 服务端回读原值，不接受客户端伪造；交接为null |
| new_value | JobValue | 是 | 目标类型化变更值；与动作一致 |
| intent | string | 是 | 原始用户指令或后端策略意图；UI上限500字符 |
| status | JobStatus | 是 | 按当前枚举返回；参见任务与直播状态约束 |
| created_at | string (date-time) | 是 | 任务创建时间，UTC ISO 8601 |
| updated_at | string (date-time) | 是 | 任务更新时间，UTC ISO 8601 |
| expires_at | string (date-time) | 是 | 未执行任务有效期；执行中未知结果不能安全认定过期 |
| risk_summary | RiskSummary | 是 | 完整风险预检/阻断信息 |
| approval | JobApproval | 是 | 同一任务的审批信息 |
| execution_receipt | JobReceipt 或 null | 是 | 受理与核验回执；尚未执行为null |
| error | JobError 或 null | 是 | 任务失败/阻断原因；正常为null |
| version | integer | 是 | 配置或任务版本；Material.version为业务版本文本 |
| idempotency_key | string | 是 | 创建请求幂等键，位于请求体；相同意图重试复用 |
| mode | "API" | 是 | 正式任务固定API；Mock任务不能冒充正式任务 |
| history | { status: JobStatus; at: string (date-time); detail: string }[] | 是 | 只读状态流水，每项status/at/detail |

### FrontendSnapshot

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| schemaVersion | "command-job-v1.0" | 是 | 响应/快照版本，必须command-job-v1.0 |
| revision | integer | 是 | 快照修订号；正式一致性策略待后端确定 |
| source | "API" | 是 | 实际来源；API快照/复盘为API，风控为SERVER，回执为PLATFORM |
| tenants | Tenant[] | 是 | 授权范围内企业列表 |
| shops | Shop[] | 是 | 授权店铺列表 |
| adAccounts | AdAccount[] | 是 | 授权广告账户列表 |
| liveSessions | LiveSession[] | 是 | 关联直播场次列表 |
| plans | AdPlan[] | 是 | 广告计划列表，包括暂停计划历史数据 |
| products | Product[] | 是 | 商品货盘列表 |
| materials | Material[] | 是 | 素材资产列表 |
| tasks | CommandJob[] | 是 | 统一CommandJob列表；不是独立审批数组 |
| scope | ReportingScope | 是 | 当前共享ReportingScope |
| riskPolicies | RiskPolicy[] | 是 | 风控配置列表；当前有效项需排第一 |
| replaySegments | ReplaySegment[] | 是 | 回放与话术片段 |
| replayReports | ReplayReport[] | 是 | 直播复盘报告列表 |
| connectors | Connector[] | 是 | 8类独立连接器能力列表 |

### LoginRequest

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| email | string | 是 | 当前登录表单使用邮箱字段；认证系统待确认 |
| password | string | 是 | 当前登录表单密码字段；使用正式安全认证 |

### CreateJobRequest

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| source | "PC_MANUAL" / "MOBILE_COMMAND" | 是 | 真实 AUTOPILOT 由后端创建；当前 ApiClient 拒绝浏览器以该来源创建。 |
| shop_id | string | 是 | 任务/统计店铺；scope允许all但不扩大权限 |
| target_type | "AD_PLAN" / "LIVE_SESSION" | 是 | AD_PLAN或LIVE_SESSION |
| target_id | string | 是 | 具体计划/场次内部ID |
| action_type | CommandAction | 是 | 独立动作，预算、出价、ROI及直播交接不可混淆 |
| new_value | JobValue | 是 | 目标类型化变更值；与动作一致 |
| intent | string | 是 | 原始用户指令或后端策略意图；UI上限500字符 |
| idempotency_key | string | 是 | 创建请求幂等键，位于请求体；相同意图重试复用 |

### JobVersionRequest

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| job_id | string | 是 | 后端生成任务ID；路径和body一致 |
| expected_version | integer | 是 | 要操作的任务版本；与最新响应version一致 |

### CommandInterpretation

```text
{ kind: "QUERY"; text: string; live_session_id: string 或 null } 或 { kind: "ACTION"; text: string; action_type: CommandAction; new_value: JobValue; target_type: "AD_PLAN" / "LIVE_SESSION"; candidate_ids: string[] } 或 { kind: "UNRECOGNIZED"; text: string }
```

### ErrorCode

```text
"UNAUTHENTICATED" / "FORBIDDEN" / "NO_DATA" / "API_NOT_CONFIGURED" / "NETWORK_ERROR" / "TIMEOUT" / "INVALID_RESPONSE" / "INVALID_INPUT" / "CONFLICT"
```

### ApiFailure

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| error | { code: ErrorCode; message: string } | 是 | HTTP错误code/message；当前ApiClient不解析此body用于展示 |
| requestId | string | 是 | 后端请求追踪ID，必须为字符串 |


审批请求另有`ReviewJobRequest = JobVersionRequest + {decision: "APPROVE" 或 "REJECT"}`；指令解析请求为`{text:string,scope:ReportingScope}`。ApiSuccess泛型统一对应第2节信封。`CommandJob.history`每项为`{status,at,detail}`；`ReplayReport.sections`每项为`{title,evidenceIds,text}`，均不可只返回字符串。

## 6 command_job流程与一致性

主流程为DRAFT → PENDING_APPROVAL → APPROVED → EXECUTING → VERIFYING → SUCCEEDED。创建后用户核对目标账户、旧值、新值、差额和风险，再调用confirm。批准不等于执行；受理不等于核验。正式执行和回读由后端完成，前端只刷新快照读取进度。SUCCEEDED要求已有PLATFORM回执、accepted=true、verification=MATCHED；Mock回执不得返回API模式。批准版本与随后递增的任务版本不是同一个概念。

| 分支 | 约定 |
|---|---|
| BLOCKED | 风控/旧值/规则版本变化阻断，不通过审批绕过；可返回2xx完整任务使UI展示风险和原因 |
| REJECTED | 待审批时驳回，终止该申请 |
| CANCELLED | 当前流程只允许未开始执行的草稿、待批或已批任务取消；权限及占用释放须确认 |
| EXPIRED | 未执行任务超期；执行中超时不可直接认定安全过期 |
| FAILED | 请求失败、超时或核验不匹配；error说明原因，未知结果保持UNKNOWN而非成功 |
| MANUAL_REQUIRED | 直播开关播无明确控制授权时只建人工交接，不控制推流，也不暂停广告代替关播 |
| SERVER_POLICY | 未来低风险白名单仅由后端授权，前端不得自己设APPROVED；工作模式不授予审批 |

每次变更携带expected_version，后端比较并原子递增version；409不能被前端猜测为成功。创建使用idempotency_key，同键同逻辑请求返回同一任务，同键不同对象/动作/值/意图应409。前端当前重试保留组件内的幂等键，但刷新、重新分析或用户重新发起可能生成新键，正式幂等范围与有效期仍须后端实现。

动作必须独立：CHANGE_DAILY_BUDGET/MONEY、CHANGE_BID/MONEY、SET_ROI_TARGET/RATIO、PAUSE_AD_PLAN/BOOLEAN false、RESUME_AD_PLAN/BOOLEAN true、REQUEST_LIVE_START/HANDOFF START、REQUEST_LIVE_END/HANDOFF END。前三种不得混用。多候选目标必须用户选择，candidate_ids中的对象必须在返回快照中可解析，且只包含会话有权操作的对象。

当前ApiClient不存在任务列表或详情独立GET；全部从snapshot.tasks读取。也不存在/api/...同源替代接口、/execute、/verify、模拟过期、resetDemo或WebSocket/SSE。subscribe在API模式是空实现，前端不会自动轮询；点击刷新或完成当前页操作后reload。真实手机跨设备进度推送需要双方确认订阅/轮询和相应前端改造，不能声称已经实现。

## 7 统计口径与快照关系

PC与手机共用ReportingScope。前端按shop_id/live_session_id/connector_id筛选plans，三个指标为广告消耗求和、支付归因GMV求和、GMV÷消耗；任何被选计划的消耗或GMV为null时，当前三个汇总都显示缺失；计划列表为空也显示缺失，不自动填0。分母为0时ROI=null。暂停只改变enabled，历史消耗和GMV必须保留。date尚未被前端拿来二次切分逐行数据，因此后端必须保证快照指标与scope.date一致。

scope切换后后端应返回同一授权全集和最新scope，不能只保留某一个店铺的选项导致无法再切换。为避免歧义需确认scope按用户、设备或会话保存，以及两个设备在不同统计范围同时操作时的行为。所有实体关联使用内部ID，nativePlanId只作平台追踪；任务account_id必须能在adAccounts中解析，广告计划的账户/店铺/场次与任务一致。

经营目标里的日预算上限、ROI、最大变化率、冷却期和工作模式仅作为配置。当前Mock展示企业关联计划预算合计（含暂停计划）与上限比较、单次变化率、成功动作冷却和策略版本检查；正式额度占用/释放、平台能力、累计资金、熔断、并发锁及执行前二次风控由陆文龙确定并实现，不能把浏览器预检当作授权。

## 8 八类平台连接器

| ConnectorId | 业务域 |
|---|---|
| adq | 腾讯广告ADQ |
| wxpromo | 微信小店推广 |
| beans | 微信豆 |
| wxshop | 微信小店经营 |
| qianchuan | 巨量千川 |
| douyinshop | 抖店经营 |
| juguang | 小红书聚光 |
| redshop | 小红书电商 |

八者独立授权、账户和能力核验。ADQ授权不等于微信豆或微信小店推广授权。Connector.status/scopes/checkedAt只描述能力；授权、令牌保存、回调、第三方写接口均由后端提供，本前端没有连接器实现，也没有暴露平台密钥。

## 9 全部页面对接映射与待定义模块

所有业务页面在进入工作区时先GET session和snapshot；下表表示在此基础上的实际实体/交互。

| PC路由 | 页面 | 当前对接与未定义部分 |
|---|---|---|
| /app/overview | AI总控台 | plans/scope/riskPolicies/tasks；仅3项KPI |
| /app/goals | 经营目标 | riskPolicies；PUT risk-policies/{id} |
| /app/monitor | AI直播作战室 | liveSessions/plans/products/replaySegments；指令解析与任务创建 |
| /app/tasks | AI任务与审批 | tasks；确认/审批/取消 |
| /app/planning | 内容策划 | products/materials；创意Brief业务接口未定义 |
| /app/scripts | 脚本工厂 | materials；脚本保存/生成/审核业务接口未定义 |
| /app/videos | 视频工厂 | materials；视频生产/查询/回收业务接口未定义 |
| /app/graphics | 图片图文 | products/materials；图文生成/上传业务接口未定义 |
| /app/editing | 智能剪辑 | materials；剪辑/渲染业务接口未定义 |
| /app/assets | 素材资产库 | materials；资产上传/版本/下载业务接口未定义 |
| /app/experiments | 素材测试 | 现有骨架；实验创建/观测业务接口未定义 |
| /app/plans | 投放计划 | plans；复用标准任务创建；无直接广告写接口 |
| /app/decisions | AI投手决策 | tasks；自主投放候选同一CommandJob，策略运行接口未定义 |
| /app/executions | 执行记录 | tasks[].execution_receipt；无前端直接执行接口 |
| /app/products | 商品与货盘 | products/replaySegments；完整货盘诊断服务未定义 |
| /app/budgets | 跨平台预算 | riskPolicies；复用标准任务申请，跨平台分配服务未定义 |
| /app/analytics | 经营分析 | plans/scope；完整分析服务未定义 |
| /app/reports | 完整复盘 | replayReports/replaySegments/products；完整经营八章生成服务未定义 |
| /app/agents | AI岗位管理 | 岗位骨架；岗位配置/策略启停接口未定义 |
| /app/connections | 平台连接 | connectors/adAccounts；OAuth/令牌/能力探测接口未定义 |
| /app/settings | 企业和权限设置 | tenants/Identity；正式成员/权限管理接口未定义 |

| 其他入口 | 当前对接 |
|---|---|
| / | 冻结官网，无后端业务API |
| /login | session/login/logout，next仅前端白名单；campaigns映射plans |
| /mobile AI指挥 | scope、interpretCommand、createJob、confirmJob、cancelJob；审批进度来自同一tasks |
| /mobile 直播数据 | liveSessions/plans/products和同一指标口径 |
| /mobile 执行流水 | tasks/approval/execution_receipt/history，无直接执行请求 |
| /mobile 直播复盘 | replayReports/replaySegments/products |

本轮没有为骨架页面虚构CRUD、生成、上传、授权或分析接口。若后端希望提前建设其余模块，应单独确认输入输出、分页、任务机制和前端阶段，再扩展契约。

## 10 联调前必须确认的兼容项

1. **直播真实状态缺口**：LiveSession.status当前只允许MOCK_LIVE/UNKNOWN，UI只有MOCK_LIVE显示演示直播，其他显示未知。真实后端不能用MOCK_LIVE冒充；暂用UNKNOWN，LIVE/ENDED等状态需批准下一轮前端修改后再接入。本次导出不偷偷改模型。
2. **正式认证未完成**：Cookie、CSRF、OAuth/验证码、权限粒度、CORS、续期与退出必须确定；目前仅email/password请求声明。OpenAPI未指定虚构Cookie名。
3. **实时更新未接入**：API subscribe为空且无自动轮询；确认列表分页、单任务读取、事件ID/版本/断线恢复，再批准改前端。不要要求后端实现一套前端尚未调用的固定路径。
4. **错误细节未读取**：当前客户端忽略错误body、429重试提示；需要显示具体服务端失败原因时，优先把任务业务失败放在完整CommandJob.error中，或后续改进HTTP错误解析。
5. **快照有效配置顺序**：经营目标读取riskPolicies[0]；后端需返回当前有效配置为第一项。复盘组件读取所选范围报告的首项，正式多版本/排序/选择协议待确认。
6. **真实指标口径**：日切、平台归因窗、支付去重、退款和延迟必须给出定义；当前支付归因GMV不是利润。缺成本/退款不可推算利润。
7. **任务授权和核验**：资金单位、平台出价语义、对象旧值、有效期、审批职责分离、SERVER_POLICY、幂等并发、受理/核验与超时对账均需后端确认。

## 11 建议联调顺序与验收

先完成测试环境地址、会话和只读快照，校验全部模型和授权范围；再接入scope/经营目标；再走解析→选择对象→创建DRAFT→confirm→PC批准→后端执行核验→刷新两端。不要在后端用前端示例数据冒充真实结果，也不要把浏览器的模拟推进接口变为正式执行授权。

验收至少包括：查询关联直播并显示更新时间；预算6000元→5000元显示-1000元且账户明确；同一job_id在两端一致；批准仍未成功；PLATFORM受理后等待核验，MATCHED才完成；超上限阻断；暂停保留历史消耗；ROI变更不改出价；多计划泛指必须选择；失败/超时/驳回/过期一致；版本及幂等冲突；401/403及跨租户拒绝。上述契约样例与Schema已本地校验，但**没有向真实后端发起联调请求**。

## 12 来源与交付边界

接口来源：src/services/api-client.ts及contracts.ts；类型：src/lib/domain.ts、command-job.ts；统计：reporting.ts；消费流程：provider.tsx及页面组件。源文件哈希见source-manifest.json。旧G1-A的g1a-draft-1、CommandTask、AWAITING_APPROVAL、AD_SET_*不应再作为当前对接协议；以此导出的command-job-v1.0为准。

此导出仅新增文档与接口定义文件，不编写后端业务代码、数据库、连接器、执行或AI策略引擎；不修改冻结官网、不推送、不部署。内容供陆文龙确认，未向任何人自动发送。
