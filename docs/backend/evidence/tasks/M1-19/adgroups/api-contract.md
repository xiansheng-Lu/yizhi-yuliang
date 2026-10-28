# 已有 V9 广告组查询合同

当前稳定分页已取代本页初版1000行/不支持cursor限制，最新见[pagination/api-contract.md](pagination/api-contract.md)；首版证据作为历史保留。

入口：`POST /v1/delivery/integrations/queries`，Cookie 与 `X-CSRF-Token`；禁止 query 参数覆盖身份。body 沿现有 `QueryRequest`：

```json
{
  "scope": {
    "tenantId": "当前会话企业", "businessEntityId": "实际主体UUID",
    "shopId": "实际店铺UUID", "provider": "tencent", "productLine": "adq",
    "accountId": "实际授权账户UUID", "start": "2026-10-01", "end": "2026-10-28"
  },
  "resource": "adgroups",
  "filters": {"search": "实际名称或原生ID片段", "nativeParentId": "原生campaign int64 ID"}
}
```

accountId 依既有合同允许空，仅当实际范围唯一账户时服务端解析；行 ID 用解析后的 tenant/account/native-adgroup 生成稳定 UUID，scopeKey 仍精确复现请求范围，不擅自改合同。非法 UUID 父级/非正 int64、cursor 直接 422。

成功输出 `platform-workspace-v9.0` ResourcePage：`source=API`，完整 rows、`nextCursor=null`，`syncedAt` 为当前完整批次抓取时间，`timezone=Asia/Shanghai` 为展示合同，`granularity=LATEST_ADGROUP_CONFIG_OBSERVATION`。每行 nativeId/name 来自真实字段；version=null。budgetMinor 仅取独立有效 proof，证明缺失/过期为 null；证明后的 0 不丢失。moneyType 仅有效预算为 AD_BUDGET。源更新时间仅证明 epoch 单位后转换 UTC ISO；无依据为“源更新时间未提供”，不能拿 fetchedAt 替代。目标 ROI 不映射效果 roi。

完整空批次可 rows=[]；尚未同步、最新失败/不完整、授权变更或缺唯一来源为 CAPABILITY_UNAVAILABLE/422。筛选完整结果超过1000为 DATA_COVERAGE_INSUFFICIENT/422，不静默截断、不签不可用游标。

消费者：`frontend/src/features/platform-workspace.tsx` 真实发起 query，父级下钻保存 `row.nativeId`；`frontend/src/services/platform-service.ts` 严格验证响应；`frontend/src/lib/platform-catalog.ts` queryBlock 核当前账户授权/关联、READ operation、permission 与前六阶段。此切片没有改前端生产代码。

固定路径依据复用 [M1-18 SDK 来源证据](../../M1-18/plan-projections/sdk-source.json)：官方 SDK revision `81d6cc04b58d7d5a5ec9ad0137e43b3b6c52fde9`，`pkg/api/api_adgroups.go` SHA256 `14c4726671b9d3ecb85c75553d8ad0723325cd1b7e6089bc1ff0500698061765`。路径/source 不代表本租户已真实授予接口/scope，独立量纲和当前授权仍逐次核对。
