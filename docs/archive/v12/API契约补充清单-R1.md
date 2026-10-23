# 给后端的 V12 契约补充清单

## 2026-10-22 账号前端补充（待后端实现与联调）

用户已确认同时支持邮箱验证码和邮箱密码登录，当前没有后端地址或腾讯云配置。前端默认 API 模式；本地预览必须显式开启 Mock。以下路径是前端声明的对接契约，不代表已有可用服务。

| HTTP | 路径 | 请求与返回 |
| --- | --- | --- |
| POST | `/v1/auth/email-codes` | `{email, purpose:"LOGIN"}` → `{challengeId, expiresAt, retryAfterSeconds}` |
| POST | `/v1/auth/login` | 验证码：`{method:"email_code", email, challengeId, code}`；密码：`{method:"password", email, password}` → Identity，并通过 Set-Cookie 建立会话 |
| GET | `/v1/auth/session` | Identity；无会话返回 401；前端恢复已授权的登录目标 |
| POST | `/v1/auth/logout` | 服务端撤销会话、清除 Cookie，返回 `data:null` |
| PATCH | `/v1/users/me` | 仅允许 `{displayName}`，1–40 字符；返回更新后的 Identity |

成功信封继续使用 `{data, requestId, schemaVersion:"command-job-v1.0"}`。Identity 现需包含 `userId, displayName, email, tenantId, shopIds:string[], permissions:string[], workspacePages:string[], mode:"API"`。`workspacePages` 为服务端下发的客户页面 ID 白名单；空数组仍可进入用户中心。前端据此过滤导航、搜索、工作台入口及深链；权限的最终校验必须在后端完成。客户页面不接受 `agents/connections/settings` 管理权限。`tasks` 通过中枢或用户中心进入。

验证码应为 6 位数字，绑定邮箱、用途与 challengeId，服务端控制有效期、尝试次数、IP/邮箱频率限制和使用后失效，不得在发送响应里返回验证码。429 可返回整数秒 `Retry-After`。密码校验、密码哈希、账号开通、找回密码和密码设置均由后端负责，本轮未伪造这些能力。

请求携带 `credentials:include`，写操作带 `Content-Type:application/json` 和 `X-Yuliang-Client:web`；后端必须核验 Origin/CSRF、自定义请求头及严格的跨域白名单，配置 Secure/HttpOnly/SameSite Cookie，禁止凭证跨域通配授权。401 会让前端重新检查会话；工作区数据失败时，用户中心仍可管理已验证账号。浏览器不持久化密码或访问令牌。

腾讯云 SES 发信由后端使用已配置的发信域名、地址和模板完成，SecretId/SecretKey 不进入 NEXT_PUBLIC 环境变量。官方资料：[SendEmail](https://cloud.tencent.com/document/api/1288/51034)、[发信模板](https://cloud.tencent.com/document/product/1288/55193)。腾讯云提供邮件送达能力，应用后端负责登录认证和用户授权。现有 OpenAPI 已同步这两个新增操作、双登录请求及 Identity 字段。

## 项目与对话记录

2026-10-22 补充，待后端提供与联调。

| HTTP | 路径 | 前端调用与返回 |
| --- | --- | --- |
| GET | `/v1/workspace/history` | `{projects, conversations, taskProjects}`，按登录账号和企业范围返回 |
| POST | `/v1/projects` | `{name}` → `{id,name,createdAt}`；名称 1 至 40 字符 |
| PATCH | `/v1/conversations/{id}` | `{projectId:string|null}` → Conversation，移动对话及其关联任务 |
| POST | `/v1/conversation-messages` | `{conversationId?,projectId?,text,scope,source,idempotencyKey}` → `{conversation,interpretation,messageId}` |
| POST | `/v1/conversations/{id}/jobs` | `{messageId,jobId}` → Conversation，将已创建任务关联到具体助手消息 |
| PUT | `/v1/command-jobs/{id}/project` | `{projectId:string|null}` → `data:null`，仅更改归类，不修改执行、审批或任务版本 |

Conversation 包含 `id,title,projectId,createdAt,updatedAt,messages[]`；Message 包含 `id,role:user|assistant,text,createdAt`，助手消息可含 `interpretation,scope,jobId`。发送新对话时生成 ID，以第一条用户消息建立标题；后续发送延续原对话。`messageId` 必须明确指向本次请求保存的助手消息，不能依赖会话最后一条消息，以免并发或重试关联到错误任务。

后端需要原子保存用户消息、助手结果和幂等索引：同一 idempotencyKey、同一请求返回原结果，不同内容返回 409；发送响应丢失后可重试。历史查询必须按企业/用户鉴权；项目、对话、消息、任务间所有引用均须校验归属。已有结构化任务直接作为最近记录展示；有对话关联的任务在对话中提供入口，避免重复条目。项目归类不触发广告变更。

新项目不能伪造 AI 能力；真实模型回复由后端生成并存储。Mock 只保存本地规则结果，按 `tenantId/userId` 隔离，不能实现跨设备同步。API 模式不把失败的保存请求伪装成已记录，也不回退本地存储。当前历史读取是完整列表，正式大数据量使用前需后端补充分页；本轮未实现流式大模型、文件上传或语音输入。

## R1 历史记录

本轮没有修改现有 `FrontendService`、API Client、Mock Adapter 或 `command_job` 契约。新增路由和视觉外壳不要求后端变更，原 G1-B1 执行、回读和审批语义继续有效。

| 范围 | 本轮状态 / 后续确认项 |
| --- | --- |
| 默认入口与登录深链 | 前端 `/app/command`、七组导航已完成；正式认证仍由后端提供 |
| 全局范围 | 继续使用已有 shop/live/connector/date/timezone；账户级范围待 R3 契约确定 |
| 任务来源 | 继续使用已有 AUTOPILOT / PC_MANUAL / MOBILE_COMMAND；总指令提出的 AUTO_POLICY 映射待 R2 讨论，不擅自破坏既有协议 |
| workflow / content_job | R2 独立草案，不将内容生成伪装为广告 command_job |
| 素材及下载 | R4 需确认媒体元数据、真实 URL/Blob、下载授权、版本和素材回链 |
| 素材→投放 | R3/R4 需区分草稿、上传、平台审核、投放、失败与效果回流 |
| ADQ | R3 确认项目/营销单元/创意、AIM+/竞价、场景能力、字段单位和写权限 |
| 微信小店推广 | 独立商品/直播推广流程与能力矩阵，不能套 ADQ 表单 |
| 微信豆 | 独立加热订单、额度与时长；微信豆与人民币字段分离 |
| 直播/复盘/跨设备 | 数据更新、录像/转写、回执核验、服务端持久化和订阅由后端负责 |

以上为后续待确认项，没有宣称后端已经实现或联调完成。本轮未向陆文龙发送消息。
