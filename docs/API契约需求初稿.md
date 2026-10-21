# 易致·驭量前端 API 契约需求初稿

版本：`g1a-draft-1`。日期：2026-10-21（Asia/Shanghai）。接收方：后端负责人陆文龙。

这是前后端对齐材料，**不是已经实现的后端接口**。G1-A 只实现前端类型、Mock Adapter 和请求客户端；没有 Next.js 业务 Route Handler、数据库、平台适配器、广告执行器或 AI 策略引擎。此文档尚未发送给任何人。

## 1. 实际代码入口

- `src/lib/domain.ts`：共享业务实体、任务动作和状态。
- `src/services/contracts.ts`：统一 Service、请求、响应及错误类型。
- `src/services/api-client.ts`：待接后端的 HTTP 客户端。
- `src/services/mock-adapter.ts`：浏览器内演示；不作为服务端实现参考或授权依据。

接口路径均为建议，由双方确认后调整客户端。前端只调用应用后端，不持有广告平台密钥，不直接调用平台写接口。NEXORA、易致投手和直播工程均未被修改或迁移后端。

## 2. 认证与租户边界

正式会话由后端提供。建议使用 HttpOnly Cookie，并由后端决定 Secure、SameSite、会话续期和撤销策略。所有请求均使用 `credentials: include`。跨域部署须配置精确 Origin 白名单；前端本地地址为 `http://127.0.0.1:3000`。

后端根据已验证身份决定 `tenantId`、可访问 `shopIds` 与权限；不能信任客户端提交的租户、角色或广告账户归属。前端路由守卫只是交互控制，不是安全边界。当前 Mock 身份可由浏览器用户自行修改，不能用于生产。

建议权限：`snapshot.read`、`task.create`、`task.approve`。后端可提供更细权限映射。登录目标参数只由前端白名单处理，不接受任意外部跳转。

CSRF Token 的获取、刷新、Header 名称及跨域 Cookie 方案尚待确认；当前 API Client 尚未实现该正式认证协议。认证接口就绪和完成安全联调前，不得视为可上线登录。

## 3. 通用响应

```ts
interface ApiSuccess<T> {
  data: T;
  requestId: string;
  schemaVersion: 'g1a-draft-1';
}
interface ApiFailure {
  error: { code: ErrorCode; message: string };
  requestId: string;
}
```

建议 HTTP 状态：400/422 输入无效；401 未登录；403 无权限；404 资源不存在；409 版本或幂等冲突；429 频率限制；5xx 后端或上游异常。客户端当前将 429、5xx 统一呈现为请求错误，细分重试策略待后续接入。

客户端检查 JSON、响应版本、requestId、快照来源及顶层集合；字段和权限的完整校验必须由后端完成。空集合使用 `[]`；未知数值使用 `null`；禁止用零填充未授权、未支持或延迟数据。正式结果必须是 `source: 'API'`，不能返回演示快照冒充真实数据。API 模式失败不会自动回退到 Mock。

## 4. G1-B 首批候选接口

| 方法与路径 | 请求 | 成功 data | 备注 |
|---|---|---|---|
| GET `/v1/auth/session` | 无 | `Identity` | 未登录返回 401，客户端进入登录页 |
| POST `/v1/auth/login` | `{email, password}` | `Identity` | 写入服务端会话；账号体系由后端决定 |
| POST `/v1/auth/logout` | 无 | `null` | 撤销服务端会话 |
| GET `/v1/frontend/snapshot` | 当前会话；后续可加明确的店铺/场次筛选 | `FrontendSnapshot` | G1-B 联调用聚合快照；后续可拆分分页资源 |
| POST `/v1/command-jobs` | `CreateTaskRequest` | `CommandTask` | 创建申请，不等同于执行广告 |
| POST `/v1/command-jobs/:id/approvals` | `{taskId, expectedVersion, decision}` | `Approval` | 后端核对任务、审批人、版本与权限 |

G1-A 未要求新增直接执行广告的前端调用。本客户端没有 `executeAdvertisement` 等方法。正式执行、队列、回执、回读由陆文龙负责。

## 5. 共享实体与标识关系

`FrontendSnapshot` 内含：企业、店铺、广告账户、直播场次、广告计划、商品/SKU、素材版本、指令任务、风险策略、风险检查、审批、执行回执、回放片段、复盘报告、独立连接器。

关联链：

```text
tenantId → shopId → liveSessionId
tenantId → adAccountId → nativePlanId → commandJobId → platformReceiptId
liveSessionId → productId/skuId → replaySegment → transcript → materialVersion
```

每个正式响应均限制在授权租户和店铺内。驭量 ID 与 NEXORA workspaceId、直播工程 organizationId 的映射由后端确认；不能由浏览器任意拼接转换。平台 nativePlanId 与内部 planId 分开。

金额使用 CNY 整数分，不能使用浮点元作为传输和执行依据。ROI 是无单位数值，例如 `5` 表示支付GMV/消耗为 5；不使用分或百分数单位。出价的正式含义与平台单位须经账户产品类型核验，当前演示字段 `bidMinor` 不是全平台通用写接口。

广告与经营指标需保留 `sourceUpdatedAt`、`fetchedAt`、来源、场次/商品/计划标识和 `attributionWindow`。同一历史统计范围中，暂停计划不应导致已经产生的消耗从汇总中消失。广告支付归因与订单经营口径须分别说明，不能简单叠加或认定为利润。

## 6. 三种独立广告动作与直播交接

```ts
type TaskAction =
  | { type: 'AD_SET_BUDGET'; budgetMinor: number }
  | { type: 'AD_SET_BID'; bidMinor: number }
  | { type: 'AD_SET_ROI_TARGET'; targetRoi: number }
  | { type: 'AD_PAUSE' | 'AD_RESUME' }
  | { type: 'REQUEST_LIVE_START' | 'REQUEST_LIVE_END' };
```

例：目标日预算 5000 元为 `budgetMinor: 500000`；出价 3.2 元为 `bidMinor: 320`；ROI 目标 6.2 为 `targetRoi: 6.2`。三者不得相互替代。

`CreateTaskRequest` 包含 `origin: PC | MOBILE`、明确的 planId 或 liveSessionId、intent、action 和 idempotencyKey。后端自主投放任务使用同一 `CommandTask`，`origin: AUTOPILOT`，进入同一审批和执行机制。

```json
{
  "origin": "MOBILE",
  "planId": "<内部计划ID>",
  "intent": "复核该直播计划的ROI目标调整申请",
  "action": { "type": "AD_SET_ROI_TARGET", "targetRoi": 6.2 },
  "idempotencyKey": "<客户端请求UUID>"
}
```

直播开关播与广告启停独立。直播推流能力未获授权时，`REQUEST_LIVE_START/END` 必须进入 `MANUAL_REQUIRED`，创建交接事项，不调用广告暂停或直播控制接口。

## 7. 任务、审批与回执

任务状态定义：

```text
DRAFT / NEEDS_CLARIFICATION / RISK_PENDING / BLOCKED
AWAITING_APPROVAL / APPROVED / REJECTED / EXPIRED
QUEUED / EXECUTING / RECEIPT_PENDING / VERIFYING / VERIFIED
FAILED / UNKNOWN / MANUAL_REQUIRED
```

`Approval` 单独记录 taskId、actorId、decision、createdAt、taskVersion 和来源模式。后端核对 `expectedVersion`，过期或对象变更后的审批应返回 409 并要求重新确认。审批通过不能直接标记任务已核验。

`ExecutionReceipt` 单独记录 source（MOCK/PLATFORM）、平台受理状态、verification、platformReceiptId、createdAt。平台结果未知应保持 UNKNOWN，不可自动重试资金写入。正式的效果观察模型、重试和通知契约待后续阶段确认。

后端应独立落实预算硬上限、累计额度、权限、动作幅度、审批、冷却、熔断、幂等、执行审计和回读。前端 Mock 中的输入检查和单浏览器去重不能替代这些约束。Mock 风险检查始终为 `MOCK_NOT_EVALUATED`；Mock 批准不会更新计划预算、出价、ROI或制造平台回执。

## 8. 八类连接器

| ID | 业务域 |
|---|---|
| adq | 腾讯广告 ADQ |
| wxpromo | 微信小店推广 |
| beans | 微信豆 |
| wxshop | 微信小店经营 |
| qianchuan | 巨量千川 |
| douyinshop | 抖店经营 |
| juguang | 小红书聚光 |
| redshop | 小红书电商 |

当前全部 UNKNOWN，Scope 为空，没有已授权声明。后端需要按账户与动作提供能力证据；不能因 ADQ 授权而启用微信豆或小店推广。只读与写入、预算与出价/ROI、直播/商品/订单/回放能力分别核验。

## 9. 同步与回放需求

G1-A 仅通过同源 localStorage 的 storage 事件与当前页事件同步演示。不同设备、不同浏览器配置、不同 Origin 不共享。该实现没有服务端持久化、分布式锁或跨设备一致性保证。

G1-B 建议先确认快照刷新机制；后续 SSE/WebSocket 需包含事件ID、实体版本、租户范围、断线续传和重新拉取规则。当前 API Client 的 subscribe 为预留接口，不宣称已接消息推送。

回放仅接受获权视频或商家上传。当前 videoUrl 均为空、片段为 Mock 文本。正式需补视频文件授权访问、ASR/转写、片段与广告/订单事件对齐、证据引用和报告版本。完整经营复盘保留八章，直播复盘保留六类标签。缺少成本或退款不得生成利润结论。

## 10. 待陆文龙确认

1. 正式认证主系统、会话/CSRF协议、角色权限及租户映射。
2. 本地后端地址、CORS与响应信封版本；上述路径是否沿用。
3. 指标归因、时区、金额和出价单位；缺失与延迟的字段约定。
4. 创建任务是否需要 beforeValue、策略版本、审批有效期等新增字段。
5. 状态更新、分页、请求幂等和并发冲突协议。
6. 首批只读连接器与获权试点账户；每项能力的正式证据。

本草稿无需后端立即开发全部接口。优先完成正式身份和只读快照，再根据 G1-B 批准范围逐项联调。
