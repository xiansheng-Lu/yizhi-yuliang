# 16 内容 SOP 工作流详设

本册细化内容工厂域：把内容创作过程梳理为运营/投手可直接驱动的 SOP 自动化工作流。设计前提（见 01 册 2.1）：系统不设独立内容制作角色，使用者是 OPERATOR；后端不建渲染引擎，生成对接 NEXORA 等外部能力，自己只做 SOP 编排、文档云端化、素材资产与成本追溯。所有结构以前端 `src/lib/content-factory.ts`、`content-delivery.ts` 的既有模型为准，不另造一套让前端返工。

## 1. 前端现状盘点（后端必须兼容的事实）

| 事实 | 来源 | 后端承接方式 |
|---|---|---|
| 六个页面 planning/scripts/graphics/videos/editing/assets；scripts 与 planning 同一工作台（contentDocumentMatchesPage） | contentPages | 列表接口 planning 页同时返回 kind=scripts |
| 文档五种 kind：planning/scripts/graphics/videos/editing | DocumentKind | content_documents.kind 枚举一致 |
| 文档结构 brief/body/scenes/boards/nodes/edges/clips/settings/prompt/messages，状态 draft/review，version 乐观锁 | ContentDocument | 云端文档字段一一对应，version 对应云端 revision |
| 派生链 parentId：策划→脚本/视频/图文/剪辑，自动复制 brief、继承 body/scenes/settings，视频文档生成默认三节点 | flowDocument/createDocument | POST flow 接口在服务端实现同一派生规则 |
| 视频节点四型 reference/direction/generate/result，边不可引用不存在节点（前端另有环检测） | VideoNode/nexora-canvas | 节点与边入参校验引用完整性+无环 |
| 图文画板 Artboard/GraphicLayer（1080×1350 默认、100-8192 尺寸、hex 色、图层类型） | createBoard/validateContent | 服务端同规则校验 |
| 剪辑三轨 video/audio/caption，片段不超素材边界，最小 0.05 秒，音量 0-1 | Clip/validateContent | 服务端同规则校验 |
| 规格 settings：ratio 四值、resolution 三值、duration 1-600 秒，skill（如"口播精剪"） | validateContent | 同规则校验，skill 走 SOP 模板选项 |
| 素材四类 image/video/audio/text，含 mime/size/duration | ContentAsset | content_assets 一致 |
| 本地草稿 IndexedDB + ContentState.revision，校验失败给明确中文错误 | content-drafts.ts/validateContent | 云端保存接口 + revision 冲突处理（见第 7 节） |
| 账户关联 ContentAccountLink 是"本地交接意图，不是平台上传或投放回执"，按(productLine,accountId)去重，必须属于当前店铺 | content-delivery.ts 注释与 validateAccountLinks | 关联只登记意图；真实平台上传走执行内核两段链路（见第 8 节） |
| 创作对话 CreativeMessage（id/text/context/assetIds/createdAt） | content-factory.ts | messages 随文档保存，是 AI 辅助创作的上下文记录 |

## 2. SOP 工作流模型

R00-02已冻结[系统模板与复合FK约束](../contracts/R00-02-数据库与认证规格.md)：本节为业务结构片段，实施时合并13册的owner_tenant_id生成列、系统行读写策略及template_tenant_id选择约束；tenant=0不是租户根记录。各运行/步骤/文档的租户复合FK须在M3创建目标表后补齐，不能只抄本节单列引用当作完整迁移。

在既有文档/素材/生成任务三张事实表之上增加一层轻量编排（新增三张表），回答"这条商品内容按 SOP 走到哪一步、每步在等机器还是等人"。文档派生链（parentId）仍然是内容事实，workflow_run 只是编排视图，二者不重复存正文。

### 2.1 新增表

```sql
-- SOP 模板：系统预置（is_system=true）+ 租户可复制改造
create table workflow_templates (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,                       -- 系统模板 tenant_id=0
  code          varchar(40) not null,                  -- product_short_video / graphic_post / live_clip ...
  name          varchar(80) not null,
  product_lines jsonb not null,                        -- 适用产品线（广告投放关联用）
  steps         jsonb not null,                        -- 有序步骤定义，结构见 2.2
  version       int not null default 1,
  is_system     boolean not null default false,
  status        varchar(10) not null default 'active',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz null,
  constraint ux_workflow_tpl unique (tenant_id, code, version)
);

-- 一次创作线（运营对一个商品发起一次 SOP）
create table workflow_runs (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  template_id   uuid not null references workflow_templates(id),
  product_id    uuid not null,
  shop_id       uuid not null,
  root_document_id uuid references content_documents(id),  -- 策划文档
  status        varchar(12) not null default 'RUNNING'
                  check (status in ('RUNNING','COMPLETED','ABANDONED')),
  created_by    uuid not null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  completed_at  timestamptz null
);
create index ix_workflow_runs_user on workflow_runs(tenant_id, created_by, status);

-- SOP 每个步骤的运行态
create table workflow_run_steps (
  id            uuid primary key default gen_random_uuid(),
  tenant_id     bigint not null,
  run_id        uuid not null references workflow_runs(id),
  step_code     varchar(40) not null,                  -- brief/planning/script/storyboard/image/video/edit/review
  seq           int not null,
  status        varchar(16) not null check (status in
                  ('PENDING','READY','RUNNING','WAITING_REVIEW','DONE','SKIPPED','FAILED')),
  requires_human_confirm boolean not null default true,-- 是否必须运营确认才能推进下游
  depends_on    jsonb not null default '[]'::jsonb,    -- 上游 step_code 数组（显式依赖）
  document_id   uuid null references content_documents(id),
  generation_job_id uuid null references generation_jobs(id),
  output_asset_ids  jsonb null,                        -- 步骤产出素材 id 列表
  input_ref     jsonb null,                            -- 输入快照（brief/上游产物引用）
  error         jsonb null,
  started_at    timestamptz null,
  finished_at   timestamptz null,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint ux_run_step unique (tenant_id, run_id, step_code)
);
```

### 2.2 步骤定义结构（templates.steps 元素）

```json
{
  "code": "storyboard",
  "name": "分镜",
  "kind": "AI_ASSISTED",
  "produces": { "documentKind": "scripts", "field": "scenes" },
  "requiresHumanConfirm": true,
  "inputs": ["brief", "script.body"],
  "aiScenario": "content_storyboard",
  "validation": "scenes"
}
```

kind 四类：`MANUAL`（纯人工编辑，如货盘选择）、`AI_ASSISTED`（LLM 出候选、人确认，如选题/脚本/分镜/字幕文案）、`GENERATION`（NEXORA 等出图出片，绑定 generation_jobs）、`REVIEW`（审核与发布到素材库/投放关联）。每步声明输入引用、产物落点（文档字段或素材）、校验规则名，编排器只按定义驱动，不把流程写死在代码里。

## 3. 五条标准 SOP

### 3.1 策划与脚本（planning 统一工作台）

步骤：`brief → topic → planning → script → storyboard`。

1. brief：运营围绕商品填写受众/目标/卖点/渠道/要求五字段（对应前端 brief，channel 默认"视频号"）；可由商品资料与历史素材用 AI 预填候选，人确认。
2. topic/planning：AI 按 brief 出选题与策划 body 候选，写入 kind=planning 文档，运营编辑确认。
3. script：在同一工作台派生 kind=scripts 文档（parentId 指向策划），正文 body 是口播稿。
4. storyboard：AI 把脚本拆为 scenes[]（title/narration/visual/duration），单镜头 0.1-600 秒；运营逐镜改文案与画面描述。
5. 文档对话消息（CreativeMessage）全程随文档保存，作为后续步骤上下文与审计；AI 输出必须经人采纳才落正文，不直接覆盖。

### 3.2 图文（graphics）

`brief → copy → board → image_generation → review`。派生 graphics 文档时按前端规则把 sellingPoints+body 拼成初始 prompt，并默认建一块画板。图层仅 text/image，image 图层必须引用已入库 image 素材；出图走 GENERATION 步骤（type=IMAGE/GRAPHIC），候选进 WAITING_REVIEW，运营选定后结果图层引用 result 素材。

### 3.3 视频（videos，节点画布）

`reference → direction → generate → result` 与前端四类节点对应：

- reference：商品与参考素材节点（运营从素材库挂接，不引用未授权商品）；
- direction：人工导演说明节点；
- generate：生成节点，文本由脚本分镜自动拼接（与前端 createDocument 默认行为一致：scenes 的 visual+narration 汇总），绑定 type=VIDEO 的 generation_job；
- result：成片输出节点，SUCCEEDED 后挂 result_asset。
- 编排器校验边引用完整且无环（前端 nexora-canvas 已有环检测，服务端独立再验，不信任客户端）；生成节点在途时禁止重复提交（同一 run_step 只允许一个在途 job）。

### 3.4 智能剪辑（editing）

从视频/脚本派生 editing 文档并继承 settings；三轨剪辑：video/audio/caption。服务端执行与前端完全相同的边界校验：片段起点非负、duration>=0.05、sourceStart+duration 不超素材时长（0.01 容差）、音量 0-1、caption 轨不要求素材但 text 必填。字幕可由 AI 按成片生成候选（AI_ASSISTED），技能 skill 取模板配置（如口播精剪）。

### 3.5 素材库与投放关联（assets）

成片/图片/文稿经 REVIEW 步骤入素材库（content_assets），运营在素材上登记投放账户关联（ContentAccountLink）。这是 SOP 的终点、平台投放的起点，边界见第 8 节。

## 4. 运行步骤状态机与编排规则

状态：`PENDING → READY → RUNNING → WAITING_REVIEW → DONE`，旁路 `SKIPPED / FAILED`。

| 转移 | 触发 | 规则 |
|---|---|---|
| PENDING→READY | 所有 depends_on 步骤 DONE | 编排器在每步完成时计算；SKIPPED 的上游若被依赖则下游不 READY（除非模板声明可选依赖） |
| READY→RUNNING | 运营点击执行/AI 自动候选 | AI_ASSISTED 调 LLM 网关；GENERATION 创建 generation_jobs（QUEUED） |
| RUNNING→WAITING_REVIEW | AI 出候选 / 供应商成片回来 | 产物先挂在步骤上不覆盖正式文档字段 |
| WAITING_REVIEW→DONE | 运营采纳 | 候选写入目标文档字段/素材挂接；推进下游 |
| 任意→FAILED | 供应商失败或校验失败 | 透传原因；允许从该步重做（新 generation_job，新 attempt） |
| READY→SKIPPED | 运营跳过（模板允许的可选步） | 记录跳过原因 |

重做与失效（对应前端 generation_steps 设计）：某步重做时，按 depends_on 闭包把下游步骤回置 PENDING 并清空其"机器产物"关联；已被运营人工确认且不依赖被改产物的独立结果保留（依赖关系显式存表，是否失效按图计算，不凭感觉）。run 完成不自动做任何平台动作。

## 5. 服务端校验规则（前端 validateContent 的服务端化）

前端校验是体验，服务端必须独立执行同一套规则（保存文档、生成提交、SOP 推进三个入口都要过）：

| 对象 | 规则 |
|---|---|
| 草稿容器 | schema=1、revision 非负整数、documents/assets 为数组；记录 id 不重复 |
| 关联商品 | productId 必须在当前租户授权商品集合内 |
| 文档头 | kind 合法（非 assets）、title 非空且 ≤120、version≥1、updatedAt 可解析 |
| brief | audience/objective/sellingPoints/channel/requirements 均为字符串 |
| 规格 | ratio ∈ 9:16/16:9/1:1/4:5；resolution ∈ 720P/1080P/4K；duration 1-600 秒 |
| 镜头 | title/narration/visual 为字符串，duration 0.1-600 秒 |
| 画板 | 宽高为 100-8192 整数；background 为 #rrggbb；图层 type∈text/image，颜色合法，坐标/尺寸/字号为正数；image 图层必须引用 kind=image 的已存在素材 |
| 视频节点 | 节点 id 唯一、type 合法、坐标有限、assetId 引用存在；边的端点存在；图无环 |
| 剪辑片段 | track∈video/audio/caption；起点非负、时长≥0.05；音量 0-1；非字幕轨引用素材 kind 与轨道一致且不超素材时长（0.01 容差） |
| 创作消息 | id/text/context 为字符串、createdAt 可解析、assetIds 引用存在 |
| 素材 | name 字符串、kind 合法、size≥0、duration≥0 有限 |
| 账户关联 | 字段完整、productLine 合法、mode∈MOCK/API（API 化后只接受 API）、linkedAt 可解析、按(productLine,accountId)去重、归属当前 shopId/tenantId |

校验失败统一 INVALID_INPUT，中文 message 与前端现有提示口径一致（如"剪辑超出素材边界或引用了不可用素材。"），details 指到具体文档/节点/片段 id。

## 6. 云端文档保存与本地草稿协调

一期采用"云端为准、整页保存、不做 CRDT"的简单模型：

1. 前端 IndexedDB 仍是编辑期高频自动保存的本地层（已有 content-drafts.ts）；云端保存由显式动作或定时防抖触发，PUT 全量文档带当前云端 revision。
   字段约定：HTTP文档字段沿用ContentDocument.version，映射数据库revision；ContentState.revision仅是本地整个容器的并发版本。二者不得复用。云端API适配器、冲突UI、本地文件上传和迁移预览均为F09待开发，不是现有persist调用已具备的功能。
2. 服务端以 revision 做乐观锁：匹配则写入 revision+1；不匹配返回 409 VERSION_CONFLICT + 服务端文档（updatedAt/revision）。前端提供"保留我的/采用云端/另存副本"三选一，不静默覆盖；另存副本产生新文档（新 parentId 指向同上游），两边内容都不丢。
3. flow 派生（脚本/视频/图文/剪辑）由服务端执行：保证 brief 继承、分镜 id 重生成、视频默认三节点、图文默认画板、剪辑继承 settings 与前端 createDocument/flowDocument 完全同结果；派生是一次云端事务（父子文档同库写入）。
4. 文档删除为软删；存在未结束 run 或在途 generation_job 的文档拒绝删除（409）。

## 7. 素材投放关联：两段链路不得合并

第一段（内容域，交接意图）：运营把已入库成品素材关联到可投放账户（content_account_links），只记录"这个素材计划投到这个账户"，字段沿用 ContentAccountLink；选择账户时后端校验授权新鲜度（对应前端 expiresAt 过期拒绝）。

第二段（连接器，真实上传）：真正上传平台素材走统一执行内核——能力六阶段全 PASS、风控、幂等键、platform_receipts、独立回读、素材审核状态回写。上传成功后在素材上回写平台素材 id 与审核态；content_account_links 不被当作回执，前端两个状态（已关联 / 已上传+审核中/通过/拒绝）分开展示。未取得写能力的账户，第二段只能展示"去平台手动上传"指引。

## 8. 接口清单（在 05/14 册路径表基础上补齐）

| 方法路径 | 用途 |
|---|---|
| GET `/v1/content/workflow-templates` | 可用 SOP 模板（系统+本租户） |
| POST `/v1/content/workflow-runs` | `{templateCode, productId, shopId}` 发起创作线，建策划文档与首批步骤 |
| GET `/v1/content/workflow-runs/{id}` | run 与步骤状态、每步文档/素材/生成任务引用 |
| POST `/v1/content/workflow-runs/{id}/steps/{code}/execute` | 执行步骤（AI 候选或创建生成任务；带幂等键） |
| POST `/v1/content/workflow-runs/{id}/steps/{code}/accept` | 采纳候选（body 指定产物落点） |
| POST `/v1/content/workflow-runs/{id}/steps/{code}/skip` | 跳过可选步骤 |
| POST `/v1/content/documents/{id}/flow` | 派生文档（见第 6 节） |
| GET/POST `/v1/content/assets/{id}/account-links` | 查询/登记投放关联（交接意图） |
| POST `/v1/content/assets/{id}/upload` | 发起第二段平台上传（进入执行内核，返回 command/配置任务引用） |

run 与 step 的状态变化同样写 event_projections，前端通过 SSE generation.changed/job.changed 刷新，不做秒级轮询；生成任务在 WAITING_PROVIDER 时限速轮询或等回调。

## 9. 权限、成本与数据边界

- 权限：内容相关接口统一要求 `content.write`（默认授予 OPERATOR，AGENCY_OPERATOR 在授权范围内可配，OWNER 可配，AUDITOR 只读）；没有独立内容角色。平台上传另需 `plan.write`/对应创编权限并经过完整审批链。
- 成本：GENERATION 步骤执行前必须返回预估费用（estimated_cost_minor）并由运营确认（estimatedCostConfirm=true，见 14 册）；完成后回写 actual_cost_minor，按 run 汇总成本；模型调用走 ai_invocations 记账，供应商调用按 generation_jobs 记账，两本账都能按商品/run 汇总。
- 数据边界：AI 上下文只含 brief、本商品卖点、已授权素材与历史脚本，遵守 08 册个人信息白名单；素材下载一律预签名短链，私有桶。
- NEXORA 边界（与 08/15 册一致）：不渲染、不假进度、不重复付费；WAITING_PROVIDER 凭 external_job_id 查单，重复回调幂等，扣费未知挂起人工核对。

## 10. 与其他分册的关系

- 文档/素材/生成任务三张事实表的字段定义见 04 册第 10 节与 13 册第 7 节 DDL；本册新增 workflow 三表是编排层。
- 生成任务七态与步骤状态机的完整转移见 12 册第 7 节。
- NEXORA 提交-回调-扣费未知的崩溃恢复见 15 册第 8 节。
- 内容接口的 schema 示例见 14 册第 8 节。
