"use client";
import {
  advertisingProducts,
  configurationContextKey,
  configurationContext,
  fieldFor,
  fieldNames,
  fieldStateNames,
  scenarioNames,
  type AdvertisingDraft,
  type ConfigurationCapability,
  type ConfigurationCatalog,
  type FieldId,
} from "@/lib/advertising";
export function ConfigurationSummary({
  draft: d,
  cap,
  catalog,
  compact = false,
}: {
  draft: AdvertisingDraft;
  cap: ConfigurationCapability | null;
  catalog: ConfigurationCatalog | null;
  compact?: boolean;
}) {
  const names = (id: FieldId, values: string[]) =>
    values
      .map(
        (value) =>
          cap?.fields[id]?.options?.find((o) => o.id === value)?.name ||
          `${value}（待核验）`,
      )
      .join("、") || "未设置";
  const resources = (v: { name: string; platformId: string }[]) =>
    v.map((r) => `${r.name} [${r.platformId}]`).join("、") || "未选择";
  const a = d.audience,
    smart =
      d.product === "shop" || cap?.audienceManagement === "PLATFORM_SMART";
  const entries: [string, string][] = [
    [
      "投放产品",
      `${advertisingProducts[d.product].name} · ${scenarioNames[d.scenario]}`,
    ],
    ["计划名称", d.name || "未填写"],
    [
      "经营主体",
      catalog?.entities.find((e) => e.id === d.businessEntityId)?.name ||
        d.businessEntityId ||
        "未选择",
    ],
    [
      "账户",
      catalog?.accounts.find((r) => r.id === d.accountId)?.name ||
        d.accountId ||
        "未选择",
    ],
    [
      "店铺",
      catalog?.shops.find((r) => r.id === d.shopId)?.name ||
        d.shopId ||
        "未选择",
    ],
    ["推广目标", names("objective", [d.objectiveId].filter(Boolean))],
    ["推广对象", resources(d.targets)],
    ...(advertisingProducts[d.product].flow === "ADVERTISING"
      ? ([
          ["广告版位", names("placement", d.placementIds)],
          [
            "优化目标",
            names("optimization", [d.optimizationId].filter(Boolean)),
          ],
          ["投放策略", names("strategy", [d.strategyId].filter(Boolean))],
        ] as [string, string][])
      : []),
    [
      "定向方式",
      smart
        ? "系统智能匹配"
        : a.mode === "SMART"
          ? "智能定向"
          : a.mode === "TEMPLATE"
            ? `平台定向包：${a.platformTemplate?.name || "未选择"}`
            : "自定义定向",
    ],
    ...(!smart
      ? ([
          ["地域", a.locationMode === "ANY" ? "不限" : resources(a.locations)],
          [
            "地点类型",
            names("locationType", [a.locationTypeId].filter(Boolean)),
          ],
          [
            "年龄",
            a.age.min || a.age.max
              ? `${a.age.min || "?"}–${a.age.max || "?"}`
              : "不限",
          ],
          [
            "性别",
            a.gender === "ALL" ? "不限" : a.gender === "MALE" ? "男性" : "女性",
          ],
          ["兴趣", resources(a.interests)],
          [
            "行为 / 意向 / 关键词",
            resources([...a.behaviors, ...a.intentions, ...a.keywords]),
          ],
          [
            "行为参数",
            [
              names("behaviorScene", [a.behaviorSceneId].filter(Boolean)),
              names("behaviorWindow", [a.behaviorWindowId].filter(Boolean)),
              names(
                "behaviorIntensity",
                [a.behaviorIntensityId].filter(Boolean),
              ),
            ].join(" / "),
          ],
          ["包含人群", resources(a.include)],
          ["排除人群", resources(a.exclude)],
          ["作者粉丝 / 群聊", resources([...a.creators, ...a.groups])],
          [
            "智能扩量",
            a.expansion
              ? `已启用 · 不可突破：${names("hardConstraints", a.hardConstraints)}`
              : "未启用",
          ],
        ] as [string, string][])
      : []),
    [
      "预算",
      `${names("budgetType", [d.budget.typeId].filter(Boolean))} · ${d.budget.amount || "未填写"} ${cap?.currency === "BEAN" || (!cap && d.product === "beans") ? "微信豆" : "元"}`,
    ],
    [
      "出价",
      `${names("bidMode", [d.budget.bidModeId].filter(Boolean))} · ${d.budget.bidValue || "不适用 / 未设置"} ${cap?.fields.bid?.unitLabel || ""}`,
    ],
    [
      "投放时间",
      `${d.schedule.start || "未填写"} 至 ${d.schedule.end || "未填写"} · UTC+8`,
    ],
    [
      "每日时段",
      d.schedule.allDay
        ? "全天"
        : d.schedule.slots
            .map((s) => `星期 ${s.days.join("、")} ${s.start}–${s.end}`)
            .join("；") || "未设置",
    ],
    ...(d.product === "beans"
      ? ([
          [
            "加热时长",
            names("duration", [d.heating.durationId].filter(Boolean)),
          ],
          ...(d.scenario === "LIVE"
            ? [
                ["直播状态", d.targets[0]?.status || "待回读"],
                [
                  "订单类型",
                  names("orderType", [d.heating.orderTypeId].filter(Boolean)),
                ],
                [
                  "资金类型",
                  names("fundType", [d.heating.fundTypeId].filter(Boolean)),
                ],
                [
                  "成本 / 放量模式",
                  names(
                    "deliveryMode",
                    [d.heating.deliveryModeId].filter(Boolean),
                  ),
                ],
                [
                  "素材方式",
                  names(
                    "creativeMethod",
                    [d.heating.creativeMethodId].filter(Boolean),
                  ),
                ],
              ]
            : []),
        ] as [string, string][])
      : []),
    ...(d.product === "shop"
      ? ([
          [
            "商品范围",
            names("productRange", [d.productRangeId].filter(Boolean)),
          ],
          [
            "优化目标",
            names("optimization", [d.optimizationId].filter(Boolean)),
          ],
        ] as [string, string][])
      : []),
    ["素材", resources(d.creative.assets)],
    ["创意标题", d.creative.title || "未设置"],
    ["广告文案", d.creative.copy || "未设置"],
    ["肖像授权", d.creative.portrait?.name || "未提供 / 待确认是否适用"],
  ];
  const compactNames = new Set([
    "账户",
    "推广目标",
    "推广对象",
    "地域",
    "年龄",
    "性别",
    "兴趣",
    "包含人群",
    "排除人群",
    "预算",
    "投放时间",
    "素材",
  ]);
  return (
    <div className="aw-summary">
      <h3>{compact ? "当前配置摘要" : "完整配置预览"}</h3>
      <dl>
        {entries
          .filter(([key]) => !compact || compactNames.has(key))
          .map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
      </dl>
      {!compact && (
        <details>
          <summary>字段能力与依据</summary>
          <p>
            当前有效上下文：
            {cap?.contextKey ===
            configurationContextKey(configurationContext(d))
              ? "已匹配"
              : "尚未确认"}
            ，能力版本 {cap?.version || "未返回"}。
          </p>
          <dl>
            {Object.entries(fieldNames).map(([id, label]) => {
              const field = fieldFor(d, cap, id as FieldId);
              return (
                <div key={id}>
                  <dt>{label}</dt>
                  <dd>
                    {fieldStateNames[field.state]} · {field.reason}
                  </dd>
                </div>
              );
            })}
          </dl>
        </details>
      )}
      <p className="aw-note">
        本地草稿不是平台计划。平台
        ID、金额单位、权限和素材审核需要后端再次验证。
      </p>
    </div>
  );
}
