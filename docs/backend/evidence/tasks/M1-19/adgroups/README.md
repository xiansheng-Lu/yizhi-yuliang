# M1-19 ADQ 广告组只读资源查询

当前稳定分页已取代本页初版1000行/不支持cursor限制，最新见[pagination/api-contract.md](pagination/api-contract.md)；首版证据作为历史保留。

状态：后端本切片真实 PG/HTTP 已技术验证；前端消费者独立核验另见 frontend 子目录。M1-19 整卡仍 IN_PROGRESS。

## 真实合同与接线

- 前端 `platform-workspace.tsx` 已调用 `PlatformService.query` → `POST /v1/delivery/integrations/queries`，本切片增加 `productLine=adq,resource=adgroups`，不新建旁路端点。
- 沿现有 QueryScope、Cookie/CSRF、query_context 当前 tenant/actor/shop/entity/account 与 IntegrationService 七阶段声明。广告组 backend 阶段额外固定 GET `/v1.3/adgroups/get` 和有效 `ADQ_ADGROUPS_V1_3_READ` 来源。冻结目录 ID `90b086afd649f5dc0600`、current head、独立 SDK source 共同校准目录原 `/v3.0/adgroups/get`；不改目录和 SDK frontendExposed。
- `nativeParentId` 是下钻行的 `nativeId`（真实 campaign ID），不是本地 UUID；search 只筛已有名称/原生 ID，不生成资源。当前完整结果最多 1000 行，超过明确 DATA_COVERAGE_INSUFFICIENT/422，cursor 不支持且明确 422；`nextCursor=null`。不宣称服务端分页已实现。
- 输出严格 `platform-workspace-v9.0` ResourcePage。稳定 UUID 标签仅为本地资源标识，`nativeId` 保留精确字符串；`version=null`，不提供写许可。

## 来源与量纲边界

复用 `read_plan_observations`：sync_sources / sync_runs / adq_plan_pages / adq_plan_observations 仅最新完整 SUCCESS 批次，逐页当前 authorization/binding 版本、总数/页数完整性。无同步、失败、旧授权均不能回退旧成功或伪造空集合；确认完整零行才返回真实空。

固定字段来自 `connectors/plan_protocol.py`，包括真实 adgroup_id/campaign_id/adgroup_name/configured_status/is_deleted，预算、出价、目标 ROI 与时间只是精确类型观察。只有完整有效、当前、匹配 source/binding 的独立 PlanMeasureProof 才提供 budgetMinor/规范状态/源 updatedAt。缺证明时预算为 null、moneyType=null、状态注明原生枚举、updatedAt 使用明确缺失文本；syncedAt 只取持久抓取时间。已证明的真实 0 原样保留。未知不能从账户预算或广告效果事实补值。

granularity 固定 LATEST_ADGROUP_CONFIG_OBSERVATION：所选日期不代表历史配置；广告组目标 ROI 不是效果 ROI，不输出为 metrics.roi。删除/未知删除状态明确显示，不授予选择或上传能力。CAMPAIGN 尚无冻结前端读取目录且必填 bid 来源不足，本切片不开放。

## 验证与限制

最终 unit 15 PASS（0.69s）；Ruff/check-format PASS，mypy 六文件 PASS。真实 PG/Redis/会话 Cookie HTTP `resource-query-plans` 14 PASS（14.61s），旧 `integration-context` 全组 15 PASS（10.05s），现有迁移链升级至 0073，无新 DDL。覆盖未同步/真实空、有效独立证明的真实 0、过期口径、当前授权/版本/scope/head/阶段、失败新 run 不回退、可选账户稳定ID、真实父计划筛选、CSRF、1001 行明确拒绝与收窄后1000行完整返回。CAMPAIGN 即使维护者声明当前目录操作，其 backend 仍显式 false。

首轮 12 PASS/2 FAIL（16.53s），过期故障 fixture 的 checked_at/expires_at 窗口不合法；二轮 13 PASS/1 FAIL（15.16s），proof fixture 使用 PG `::text` 输出 `+00` 时区导致 Pydantic 拒绝。均只修合成 fixture：保留合法但已过期窗口，时间使用 `to_jsonb(timestamptz)`。没有放宽运行守卫/SQL。最终完整14项复跑通过。

实际 HTTP 捕获仅包含合成测试数据，私有 `.runtime/adgroup-query-http-capture.json` 提供真实 GET context/session、primary 无proof未知时间、parentInput/parentPage 与独立合成 proof 的 known-zero case；独立 agent 用现有前端 queryBlock/PlatformService 读取。未宣称真实浏览器 F12 或真实平台授权绑定验收。

本切片真实平台请求、付费调用、原 token 配置变更均为 0。测试阶段全部身份、授权、量纲来源与平台响应均为合成输入，不代表真实平台账户已配置该映射、G1 或 F11 人工验收通过。M3-11b 命令/派发/回执不在本切片；M1-19 其他资源覆盖继续单列。
