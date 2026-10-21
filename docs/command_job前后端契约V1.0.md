# command_job 前后端契约 V1.0

2026-10-21 · G1-B1 前端契约草案 · 提交陆文龙确认。本文定义前端需要的数据和交互，不代表后端已实现或已同意。替代 G1-A 草案中的旧 CommandTask/AD_SET_* 协议；企业、店铺等既有实体仍沿用 G1-A 定义。

前端唯一类型源为 `src/lib/command-job.ts`；Service 接口为 `src/services/contracts.ts`，候选 HTTP 客户端为 `src/services/api-client.ts`。后端业务代码、鉴权、数据库、AI 解析、平台连接器、执行与核验全部由陆文龙提供。本项目没有对应 Route Handler。

## 1. 任务字段

| 字段 | 类型及含义 |
|---|---|
| schema_version | 固定 `"1.0"` |
| job_id | 后端生成的不可变字符串 ID；Mock 由适配器生成 UUID |
| source | `AUTOPILOT / PC_MANUAL / MOBILE_COMMAND`；真实自主投放任务仅由后端策略创建 |
| tenant_id / shop_id | 企业、店铺 ID，后端从会话检查租户和授权范围 |
| account_id | 广告账户 ID；直播人工交接为 null，不能用平台名称替代账户 |
| live_session_id | 关联直播场次 ID 或 null |
| target_type / target_id | `AD_PLAN / LIVE_SESSION` 与具体对象 ID；禁止猜测目标 |
| action_type | 见动作表 |
| old_value / new_value | 类型化变更值；old_value 由服务端回读，不接受前端伪造；交接 old_value 为 null |
| intent | 用户原指令或策略候选的文字意图 |
| status | 见状态表 |
| created_at / updated_at / expires_at | UTC ISO 8601 时间；UI 显式按 Asia/Shanghai 展示 |
| risk_summary | 来源、结果、风险级别、逐项检查、规则版本、检查时间 |
| approval | 审批状态、审批人、审批时间、审批方式、被批准版本 |
| execution_receipt | 受理与核验分开记录；执行前为 null |
| error | null 或 `{code,message,retryable,at}`；超时说明结果未知 |
| version | 任务乐观锁版本，状态变更递增 |
| idempotency_key | 创建请求幂等键；同键不同意图/对象/动作/值应返回 409 |
| mode | `MOCK / API`；正式任务必须 API |
| history | `{status,at,detail}[]`，只读状态流水 |
| mock_scenario | 仅 Mock 用于验收，绝不发给真实 API |

变更值联合类型：

```ts
type JobValue =
  | { kind: "MONEY"; currency: "CNY"; amount_minor: number }
  | { kind: "RATIO"; value: number }
  | { kind: "BOOLEAN"; value: boolean }
  | { kind: "HANDOFF"; value: "START" | "END" };
```

金额必须是正的安全整数分。例如用户输入 5000 元，发送 `amount_minor: 500000`；0.29 元为 29 分，不用浮点金额传输。当前仅 CNY。ROI 是无单位正比值，不是人民币出价；差额由同类型 new_value − old_value 计算。预算 UI 必须展示旧值、新值、差额、店铺、账户及风险检查。

| action_type | new_value | 对象与作用 |
|---|---|---|
| CHANGE_DAILY_BUDGET | MONEY | 仅变更广告计划日预算 |
| CHANGE_BID | MONEY | 仅变更广告出价 |
| SET_ROI_TARGET | RATIO | 仅变更投放ROI目标 |
| PAUSE_AD_PLAN | BOOLEAN false | 暂停广告计划；历史消耗和支付GMV保持 |
| RESUME_AD_PLAN | BOOLEAN true | 恢复广告计划 |
| REQUEST_LIVE_START | HANDOFF START | 直播真正开播的人工交接，无推流控制 |
| REQUEST_LIVE_END | HANDOFF END | 直播真正关播的人工交接，无推流控制 |

没有明确直播控制接口及授权时，最后两类只进入 MANUAL_REQUIRED；不得拿暂停广告替代关播。ADQ、微信豆、微信小店推广以及其余五类连接器使用各自独立 ID 和授权。

## 2. 状态及审批权威

正常人工审批流程：

`DRAFT → PENDING_APPROVAL → APPROVED → EXECUTING → VERIFYING → SUCCEEDED`

| 状态 | 条件 / 前端含义 |
|---|---|
| DRAFT | 已创建标准任务，尚待用户确认具体对象、变更差异和风控 |
| PENDING_APPROVAL | 用户确认后等待授权审批人 |
| APPROVED | 审批通过，仍未执行，不产生成功提示 |
| EXECUTING | 后端执行中，不能凭按钮点击认定平台受理 |
| VERIFYING | 平台受理后等待回读核验 |
| SUCCEEDED | 回读匹配才完成，不表示投放效果已达目标 |
| BLOCKED | 对象、规则版本、额度或风险阻断，不能审批绕过 |
| MANUAL_REQUIRED | 直播负责人交接，不改变直播或广告状态 |
| REJECTED | 待审批时驳回，不能继续执行 |
| CANCELLED | DRAFT / PENDING_APPROVAL / APPROVED 阶段取消；执行中取消协议另行确认 |
| EXPIRED | 未开始执行的任务超出有效期；执行中超时不能被当作安全过期 |
| FAILED | 请求失败、执行超时、核验不匹配等；结果未知必须说明，禁止自动重试写操作 |

`approval`：state 为 NOT_REQUESTED / PENDING / APPROVED / REJECTED；actor_id、decided_at 可为 null；method 为 MANUAL / SERVER_POLICY / null；approved_version 是被批准的任务版本。正式低风险白名单可由后端授予 SERVER_POLICY 审批，前端绝不自行设置 APPROVED。更改工作模式也不授予审批权限。

`risk_summary`：source 为 MOCK / SERVER；result 为 REVIEW_REQUIRED / BLOCKED / MANUAL_ONLY；level 为 LOW / MEDIUM / HIGH；checks 为 `{code,passed,message}[]`；policy_version 与 checked_at 必须提供。额度、频率、账户授权、计划状态、旧值及规则版本须在服务端确认、批准和执行前重新验证。

Mock 示例规则：企业全部关联计划的配置日预算总和（含暂停计划）不得超过上限；单次预算/出价变化率不得超过配置；同计划成功操作后有冷却期。策略变化或旧值变化要求重新申请。**这些只是浏览器演示分支，正式风控公式、额度占用及并发锁待后端确定。**

`execution_receipt`：receipt_id、source(MOCK / PLATFORM)、platform_receipt_id(null 或真实 ID)、accepted(null/true/false)、received_at、verification(PENDING / MATCHED / MISMATCH / UNKNOWN)、observed_value、verified_at。API 模式拒绝 Mock 回执；SUCCEEDED 至少需要 PLATFORM、accepted=true、verification=MATCHED。模拟回执始终标识 MOCK，platform_receipt_id=null。

## 3. 候选 HTTP 接口

均为前端客户端声明，**没有在 Next.js 内实现任何业务接口**。URL、授权及错误细节待陆文龙确认。通用成功包为 `{schemaVersion:"command-job-v1.0",requestId,data}`。HTTP 401/403/404/409/400/422 对应未登录、无权限、无数据、版本/幂等冲突及无效参数。客户端网络异常不回退 Mock；15 秒超时显示 TIMEOUT 和结果未知。

| 方法 / 路径 | 请求 / 返回 |
|---|---|
| GET /v1/auth/session | 返回 Identity(mode=API) 或 401 |
| POST /v1/auth/login | `{email,password}` → Identity；正式认证方式另行确认 |
| POST /v1/auth/logout | → null |
| GET /v1/frontend/snapshot | 共享实体、scope、riskPolicies、tasks 等 FrontendSnapshot(source=API) |
| POST /v1/command-interpretations | `{text,scope}` → QUERY / ACTION / UNRECOGNIZED |
| POST /v1/command-jobs | CreateJobRequest → 完整 CommandJob，通常 DRAFT 或 BLOCKED |
| POST /v1/command-jobs/{job_id}/confirm | `{job_id,expected_version}` → 完整任务 |
| POST /v1/command-jobs/{job_id}/approvals | `{job_id,expected_version,decision:"APPROVE"\|"REJECT"}` → 完整任务 |
| POST /v1/command-jobs/{job_id}/cancel | `{job_id,expected_version}` → 完整任务 |
| PUT /v1/risk-policies/{id} | 带 version 的 RiskPolicy → 新版本规则 |
| PUT /v1/frontend/reporting-scope | ReportingScope → null；存储位置/会话范围待确认 |

创建请求示例（不含由后端回读和生成的旧值、状态、审批、回执）：

```json
{
  "source": "MOBILE_COMMAND",
  "shop_id": "shop-tea",
  "target_type": "AD_PLAN",
  "target_id": "plan-qc",
  "action_type": "CHANGE_DAILY_BUDGET",
  "new_value": { "kind": "MONEY", "currency": "CNY", "amount_minor": 500000 },
  "intent": "把千川A计划日预算调到5000",
  "idempotency_key": "unique-request-key"
}
```

ACTION 解析返回 `{kind:"ACTION",text,action_type,new_value,target_type,candidate_ids}`；多个候选时 UI 必须显式选择且创建按钮禁用至选择完成。QUERY 返回 `{kind:"QUERY",text,live_session_id}`，场次不唯一时先选择。前端已拒绝单条混合预算/出价/ROI动作的 Mock 指令，正式解析由后端提供。

后端只接受用户有权访问的对象，从对象推导租户和账户并防止越权。客户端不提交执行成功、风险通过或审批人。执行状态推进来自后端；客户端 API 模式不提供 Mock 推进或过期操作。任务查询分页、单任务订阅、SSE/WebSocket、核验重试和对账接口需要后续确认及前端接入，当前 API UI 用刷新重新读取快照。

## 4. 指标与共享范围

ReportingScope 为 `{shop_id,live_session_id,connector_id,date,timezone:"Asia/Shanghai"}`，三个 ID 可为 `"all"`。PC/手机共用 scope、scopedPlans、businessTotals 和 money 工具。统计为选定自然日、同店铺/场次/平台的广告消耗与支付归因 GMV；投放ROI=支付GMV÷广告消耗。缺失数据不填零，分母为零时 ROI 无口径。暂停仅改变计划 enabled，历史消耗与GMV不变。

当前 Mock 为 2026-10-21 固定演示采样，展示源更新时间；不代表实时平台数据。正式归因窗、去重、跨平台重复支付、退款、数据延迟、日切边界和货币转换需后端明确。经营ROI目标与各计划ROI参数分别保存，保存经营目标不直接修改广告计划。

## 5. Mock 边界及待确认事项

Mock 数据源为 `yuliang:command-jobs:v1`，PC与手机在**同源同浏览器配置**中通过 storage/custom event 重新读 Service。读 G1-A 旧键迁移任务但不覆盖旧键，不自动继承旧审批。Mock 使用版本和存储 revision 减少同浏览器过期修改，**不提供分布式锁、持久化服务或真实跨设备实时同步**。有效期在读取时检查，演示可显式触发过期。localStorage 不能承担正式权限、跨设备同步或任务持久化。

请陆文龙确认：字段/动作/状态及 BLOCKED、MANUAL_REQUIRED 扩展；规则和额度占用口径；任务有效期；审批职责分离与 SERVER_POLICY 授权；正式认证/CSRF/CORS；幂等范围及并发语义；平台受理与回读核验及未知结果对账；直播控制权限；指标归因与时区；查询分页及实时订阅。确认后再安排联调；本次未向任何人发送消息，也未实现这些后端能力。
