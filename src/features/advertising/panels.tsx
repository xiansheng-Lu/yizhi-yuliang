"use client";
import { useState, useEffect, useRef } from "react";
import Link from "next/link";
import { Plus, Trash, UploadSimple } from "@phosphor-icons/react";
import {
  advertisingProducts,
  canEditField,
  configurationContext,
  fieldFor,
  type AdvertisingDraft,
  type ConfigurationCapability,
  type ConfigurationCatalog,
  type FieldId,
  type SelectedResource,
} from "@/lib/advertising";
import {
  AdvertisingService,
  configurationError,
} from "@/services/advertising-service";
import { Choice, Field, ResourcePicker } from "./controls";
export interface PanelProps {
  draft: AdvertisingDraft;
  cap: ConfigurationCapability | null;
  api: AdvertisingService;
  patch: (value: Partial<AdvertisingDraft>) => void;
}
export function AccountAndAssetSelector({
  draft: d,
  cap,
  api,
  patch,
  catalog,
  error,
}: PanelProps & { catalog: ConfigurationCatalog | null; error: string }) {
  const account = catalog?.accounts.find((a) => a.id === d.accountId),
    f = (id: FieldId) => fieldFor(d, cap, id);
  return (
    <div className="aw-panels">
      <Field
        label="经营主体"
        cap={{
          state: catalog ? "PUBLIC" : "UNCONFIRMED",
          reason:
            "主体、广告产品线、账户和经营店铺分别绑定，未授权关联不授予投放权限。",
        }}
      >
        <select
          aria-label="配置经营主体"
          value={d.businessEntityId}
          onChange={(e) => patch({ businessEntityId: e.target.value })}
        >
          <option value="">请选择真实经营主体</option>
          {catalog?.entities.map((entity) => (
            <option key={entity.id} value={entity.id}>
              {entity.name}
            </option>
          ))}
        </select>
      </Field>
      <div className="aw-grid">
        <Field
          label={d.product === "beans" ? "视频号账户" : "广告账户"}
          cap={{
            state: catalog ? "PUBLIC" : "UNCONFIRMED",
            reason:
              catalog?.reason ||
              error ||
              "正在读取账户目录。目录仅证明关联记录，真实权限需单独确认。",
          }}
        >
          <select
            aria-label="配置投放账户"
            value={d.accountId}
            onChange={(e) => patch({ accountId: e.target.value })}
          >
            <option value="">
              {catalog?.accounts.length
                ? "请选择关联账户"
                : "暂无已确认的账户目录"}
            </option>
            {catalog?.accounts
              .filter((a) => a.businessEntityId === d.businessEntityId)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} ·{" "}
                  {a.authorization === "AUTHORIZED"
                    ? "已授权"
                    : a.authorization === "NOT_INTEGRATED"
                      ? "接口待接入"
                      : a.authorization === "FORBIDDEN"
                        ? "权限不足"
                        : "未授权"}
                </option>
              ))}
          </select>
        </Field>
        <Field
          label="关联微信小店"
          cap={{
            state: account ? "PUBLIC" : "UNCONFIRMED",
            reason: "仅列出该账户关联且当前成员可访问的店铺。",
          }}
        >
          <select
            aria-label="配置关联店铺"
            value={d.shopId}
            disabled={!account}
            onChange={(e) => patch({ shopId: e.target.value })}
          >
            <option value="">请选择关联店铺</option>
            {catalog?.shops
              .filter((s) => account?.shopIds.includes(s.id))
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
          </select>
        </Field>
      </div>
      {(!catalog || !catalog.accounts.length) && (
        <div className="aw-note">
          允许查看全部步骤和填写本地配置意向。账户目录、绑定关系与授权尚未确认，真实对象和标签不会使用演示数据填充。
        </div>
      )}
      <Field label="计划名称" cap={f("name")}>
        <input
          aria-label="计划名称"
          maxLength={100}
          value={d.name}
          disabled={!canEditField(f("name"), true)}
          placeholder={`${advertisingProducts[d.product].short} · 输入便于识别的名称`}
          onChange={(e) => patch({ name: e.target.value })}
        />
      </Field>
      <Choice
        label="推广目标"
        cap={f("objective")}
        value={d.objectiveId}
        onChange={(objectiveId) => patch({ objectiveId })}
      />
      {d.product === "shop" && d.scenario === "PRODUCT_HOSTING" && (
        <Choice
          label="商品范围"
          cap={f("productRange")}
          value={d.productRangeId}
          onChange={(productRangeId) => patch({ productRangeId })}
        />
      )}
      <ResourcePicker
        label={
          d.scenario === "VIDEO" || d.scenario === "DOU_PLUS"
            ? "要加热的视频"
            : d.scenario === "LIVE" || d.scenario === "SHOP_LIVE"
              ? "推广直播间"
              : "推广商品或对象"
        }
        kind="targets"
        context={configurationContext(d)}
        api={api}
        cap={f("target")}
        single={d.scenario !== "PRODUCT_HOSTING"}
        selected={d.targets}
        onChange={(targets) => patch({ targets })}
      />
      {(d.scenario === "LIVE" || d.scenario === "SHOP_LIVE") && (
        <div className="aw-note">
          直播状态：{d.targets[0]?.status || "待选择并回读直播间状态"}
          。开播条件、预约开播与实际可执行时间由后端校验，不能用直播间名称替代真实
          ID。
        </div>
      )}
    </div>
  );
}
export function PlacementSelector({ draft: d, cap, patch }: PanelProps) {
  const f = (id: FieldId) => fieldFor(d, cap, id);
  return (
    <div className="aw-panels">
      {advertisingProducts[d.product].flow === "ADVERTISING" ? (
        <>
          <Field label="广告版位" cap={f("placement")}>
            <div className="aw-placement-options">
              {f("placement").options?.map((o) => (
                <label key={o.id} className="aw-check">
                  <input
                    type="checkbox"
                    checked={d.placementIds.includes(o.id)}
                    disabled={
                      f("placement").state !== "AVAILABLE" || !o.available
                    }
                    onChange={(e) =>
                      patch({
                        placementIds: e.target.checked
                          ? [...d.placementIds, o.id]
                          : d.placementIds.filter((id) => id !== o.id),
                      })
                    }
                  />
                  {o.name}
                  {!o.available && <small>{o.reason}</small>}
                </label>
              )) ?? (
                <p>
                  {d.product === "adq"
                    ? "自动版位、视频号、朋友圈、公众号 / 小程序等仅为公开配置要求参考。"
                    : "此产品线不继承腾讯版位与策略。"}{" "}
                  当前可用版位 ID 与组合规则待账户接口返回。
                </p>
              )}
            </div>
          </Field>
          <Choice
            label="优化目标"
            cap={f("optimization")}
            value={d.optimizationId}
            onChange={(optimizationId) => patch({ optimizationId })}
          />
          <Choice
            label="投放策略"
            cap={f("strategy")}
            value={d.strategyId}
            onChange={(strategyId) => patch({ strategyId })}
          />
        </>
      ) : d.product === "dou_plus" ? (
        <>
          <div className="aw-note">
            DOU+
            使用推广订单流程，先核验视频可投目的，再设置订单预算、素材与人群。不会创建巨量营销项目或单元。
          </div>
          <Choice
            label="订单出价策略"
            cap={f("bidMode")}
            value={d.budget.bidModeId}
            onChange={(bidModeId) =>
              patch({ budget: { ...d.budget, bidModeId } })
            }
          />
        </>
      ) : d.product === "beans" ? (
        <>
          {d.scenario === "LIVE" && (
            <>
              <Choice
                label="直播订单类型"
                cap={f("orderType")}
                value={d.heating.orderTypeId}
                onChange={(orderTypeId) =>
                  patch({ heating: { ...d.heating, orderTypeId } })
                }
              />
              <Choice
                label="成本与放量模式"
                cap={f("deliveryMode")}
                value={d.heating.deliveryModeId}
                onChange={(deliveryModeId) =>
                  patch({ heating: { ...d.heating, deliveryModeId } })
                }
              />
              <p className="aw-note">
                标准订单、全域订单等不预设为当前可用。控成本、放量与出价兼容性由当前直播订单能力响应决定。
              </p>
            </>
          )}
          {d.scenario === "VIDEO" && (
            <div className="aw-note">
              短视频按所选作品类型核验推广目标。智能 / 自定义出价与智能 /
              定向加热分开设置，不继承直播订单配置。
            </div>
          )}
          <Choice
            label="出价方式"
            cap={f("bidMode")}
            value={d.budget.bidModeId}
            onChange={(bidModeId) =>
              patch({ budget: { ...d.budget, bidModeId } })
            }
          />
        </>
      ) : (
        <>
          <Choice
            label="优化目标"
            cap={f("optimization")}
            value={d.optimizationId}
            onChange={(optimizationId) => patch({ optimizationId })}
          />
          <Choice
            label="托管策略"
            cap={f("strategy")}
            value={d.strategyId}
            onChange={(strategyId) => patch({ strategyId })}
          />
          <p className="aw-note">
            小店版为独立的待接入产品。商品托管与直播推广分别确认支持的预算、出价、素材及智能人群策略。
          </p>
        </>
      )}
    </div>
  );
}
export function BidAndBudgetPanel({ draft: d, cap, patch }: PanelProps) {
  const f = (id: FieldId) => fieldFor(d, cap, id),
    b = f("budget"),
    bid = f("bid");
  const unit =
    b.unit === "BEAN"
      ? "微信豆"
      : b.unit === "CNY_MINOR"
        ? "元"
        : d.product === "beans"
          ? "微信豆（待核验）"
          : "元（待核验）";
  return (
    <div className="aw-panels">
      {d.product === "beans" && d.scenario === "LIVE" && (
        <Choice
          label="资金类型"
          cap={f("fundType")}
          value={d.heating.fundTypeId}
          onChange={(fundTypeId) =>
            patch({ heating: { ...d.heating, fundTypeId } })
          }
        />
      )}
      <div className="aw-grid">
        <Choice
          label="预算类型"
          cap={f("budgetType")}
          value={d.budget.typeId}
          onChange={(typeId) => patch({ budget: { ...d.budget, typeId } })}
        />
        <Field label={`预算 · ${unit}`} cap={b}>
          <input
            aria-label="配置预算"
            type="number"
            min="0"
            step={b.unit === "BEAN" ? "1" : "0.01"}
            value={d.budget.amount}
            disabled={!canEditField(b, true)}
            onChange={(e) =>
              patch({ budget: { ...d.budget, amount: e.target.value } })
            }
          />
          <small>
            平台上下限：
            {b.min === undefined
              ? "待确认"
              : `${b.min}${b.unit === "CNY_MINOR" ? "分" : ""}`}{" "}
            ～{" "}
            {b.max === undefined
              ? "待确认"
              : `${b.max}${b.unit === "CNY_MINOR" ? "分" : ""}`}
            ，步长 {b.step ?? "待确认"}。{cap && `结算单位：${cap.currency}`}
          </small>
        </Field>
      </div>
      <Choice
        label="出价方式"
        cap={f("bidMode")}
        value={d.budget.bidModeId}
        onChange={(bidModeId) => patch({ budget: { ...d.budget, bidModeId } })}
      />
      <Field label={bid.unitLabel || "出价 / 目标成本 / ROI"} cap={bid}>
        <input
          aria-label="配置出价"
          type="number"
          min="0"
          step={
            bid.unit === "BEAN" ? "1" : bid.unit === "RATIO" ? "0.0001" : "0.01"
          }
          value={d.budget.bidValue}
          disabled={!canEditField(bid)}
          onChange={(e) =>
            patch({ budget: { ...d.budget, bidValue: e.target.value } })
          }
        />
        <small>
          {bid.unit === "RATIO"
            ? "按比例填写，不转换成金额。"
            : bid.unit === "BEAN"
              ? "单位：微信豆。"
              : bid.unit === "CNY_MINOR"
                ? "页面以元输入，后端契约按账户单位核验。"
                : "当前出价单位待能力接口确认。"}
          不适用出价的方式不填写固定价格。
        </small>
      </Field>
      {d.product === "beans" && (
        <Choice
          label="加热时长"
          cap={f("duration")}
          value={d.heating.durationId}
          onChange={(durationId) =>
            patch({ heating: { ...d.heating, durationId } })
          }
        />
      )}
      <ScheduleSelector draft={d} cap={cap} patch={patch} />
    </div>
  );
}
function ScheduleSelector({
  draft: d,
  cap,
  patch,
}: Pick<PanelProps, "draft" | "cap" | "patch">) {
  const f = fieldFor(d, cap, "schedule"),
    slots = fieldFor(d, cap, "timeSlots"),
    s = d.schedule,
    edit = canEditField(f, true),
    editSlots = canEditField(slots, true);
  return (
    <>
      <Field label="投放时间 · 北京时间 UTC+8" cap={f}>
        <div className="aw-grid">
          <label>
            开始时间
            <input
              aria-label="投放开始时间"
              type="datetime-local"
              value={s.start}
              disabled={!edit}
              onInput={(e) =>
                patch({ schedule: { ...s, start: e.currentTarget.value } })
              }
            />
          </label>
          <label>
            结束时间
            <input
              aria-label="投放结束时间"
              type="datetime-local"
              value={s.end}
              disabled={!edit}
              onInput={(e) =>
                patch({ schedule: { ...s, end: e.currentTarget.value } })
              }
            />
          </label>
        </div>
      </Field>
      <Field label="每日投放时段" cap={slots}>
        <label className="aw-check">
          <input
            type="checkbox"
            checked={s.allDay}
            disabled={!editSlots}
            onChange={(e) =>
              patch({
                schedule: {
                  ...s,
                  allDay: e.target.checked,
                  slots: e.target.checked ? [] : s.slots,
                },
              })
            }
          />
          全天投放
        </label>
        {!s.allDay && (
          <>
            {s.slots.map((slot, i) => {
              const update = (v: Partial<typeof slot>) =>
                patch({
                  schedule: {
                    ...s,
                    slots: s.slots.map((p, index) =>
                      index === i ? { ...p, ...v } : p,
                    ),
                  },
                });
              return (
                <div className="aw-slot" key={i}>
                  <div className="aw-weekdays">
                    {[1, 2, 3, 4, 5, 6, 7].map((day) => (
                      <label key={day}>
                        <input
                          type="checkbox"
                          disabled={!editSlots}
                          checked={slot.days.includes(day)}
                          onChange={(e) =>
                            update({
                              days: e.target.checked
                                ? [...slot.days, day]
                                : slot.days.filter((d) => d !== day),
                            })
                          }
                        />
                        {["一", "二", "三", "四", "五", "六", "日"][day - 1]}
                      </label>
                    ))}
                  </div>
                  <input
                    aria-label={`时段${i + 1}开始`}
                    type="time"
                    disabled={!editSlots}
                    value={slot.start}
                    onInput={(e) => update({ start: e.currentTarget.value })}
                  />
                  <span>至</span>
                  <input
                    aria-label={`时段${i + 1}结束`}
                    type="time"
                    disabled={!editSlots}
                    value={slot.end}
                    onInput={(e) => update({ end: e.currentTarget.value })}
                  />
                  <button
                    aria-label={`删除时段${i + 1}`}
                    className="dw-link"
                    onClick={() =>
                      patch({
                        schedule: {
                          ...s,
                          slots: s.slots.filter((_, index) => index !== i),
                        },
                      })
                    }
                  >
                    <Trash size={15} />
                  </button>
                </div>
              );
            })}
            <button
              className="button secondary"
              disabled={!editSlots || s.slots.length >= 14}
              onClick={() =>
                patch({
                  schedule: {
                    ...s,
                    slots: [
                      ...s.slots,
                      {
                        days: [1, 2, 3, 4, 5, 6, 7],
                        start: "09:00",
                        end: "18:00",
                      },
                    ],
                  },
                })
              }
            >
              <Plus size={15} />
              添加投放时段
            </button>
          </>
        )}
      </Field>
    </>
  );
}
export function CreativeSelector({ draft: d, cap, api, patch }: PanelProps) {
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const [uploading, setUploading] = useState(false),
    [message, setMessage] = useState("");
  const f = (id: FieldId) => fieldFor(d, cap, id);
  async function upload(file: File) {
    setUploading(true);
    setMessage("");
    try {
      const resource = await api.uploadCreative(configurationContext(d), file);
      if (!alive.current) return;
      patch({
        creative: { ...d.creative, assets: [...d.creative.assets, resource] },
      });
      setMessage("素材库已返回资源，平台审核状态以接口回读为准。");
    } catch (e) {
      if (alive.current) setMessage(configurationError(e));
    } finally {
      if (alive.current) setUploading(false);
    }
  }
  return (
    <div className="aw-panels">
      {d.product === "beans" && d.scenario === "LIVE" && (
        <Choice
          label="加热素材方式"
          cap={f("creativeMethod")}
          value={d.heating.creativeMethodId}
          onChange={(creativeMethodId) =>
            patch({ heating: { ...d.heating, creativeMethodId } })
          }
        />
      )}
      <div className="aw-note">
        复用内容工厂的素材资产与授权关系。
        <Link className="dw-link" href="/app/assets">
          打开素材资产库
        </Link>
        。作品型加热使用所选视频；直播按当前素材方式选择允许的内容。
      </div>
      <ResourcePicker
        label="可用视频与图片"
        kind="creatives"
        context={configurationContext(d)}
        api={api}
        cap={f("creative")}
        selected={d.creative.assets}
        onChange={(assets) => patch({ creative: { ...d.creative, assets } })}
      />
      <Field label="上传至内容工厂素材库" cap={f("creative")}>
        <label className="aw-upload">
          <UploadSimple size={18} />
          {uploading ? "正在上传…" : "选择当前版位允许的素材"}
          <input
            aria-label="上传创意素材"
            type="file"
            accept="image/*,video/*"
            disabled={
              !cap?.uploadSupported ||
              f("creative").state !== "AVAILABLE" ||
              uploading
            }
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void upload(file);
              e.target.value = "";
            }}
          />
        </label>
        <small>
          {f("creative").specs?.join(" · ") ||
            "格式、尺寸、大小、时长限制待账户与版位规格接口返回。未开放上传时请先在素材库准备素材。"}
        </small>
        {message && <p role="status">{message}</p>}
      </Field>
      <div className="aw-grid">
        {(
          [
            ["title", "创意标题"],
            ["copy", "广告文案"],
          ] as const
        ).map(([key, label]) => (
          <Field key={key} label={label} cap={f(key)}>
            <textarea
              aria-label={label}
              value={d.creative[key]}
              maxLength={f(key).max ?? 2000}
              disabled={!canEditField(f(key), true)}
              onChange={(e) =>
                patch({ creative: { ...d.creative, [key]: e.target.value } })
              }
            />
          </Field>
        ))}
      </div>
      {(d.product === "beans" || cap?.fields.portrait) && (
        <ResourcePicker
          label="肖像授权证明"
          kind="portraitCertificates"
          context={configurationContext(d)}
          api={api}
          cap={f("portrait")}
          single
          selected={d.creative.portrait ? [d.creative.portrait] : []}
          onChange={(items) =>
            patch({ creative: { ...d.creative, portrait: items[0] ?? null } })
          }
        />
      )}
      <div className="aw-subheading">
        <h3>创意配置预览</h3>
        <p>版式示意，最终展示与审核结果由平台返回。</p>
      </div>
      <div className="aw-creative-preview">
        <strong>{d.creative.title || "待填写标题"}</strong>
        <p>{d.creative.copy || "待填写广告文案"}</p>
        {d.creative.assets.length ? (
          d.creative.assets.map((asset) => (
            <CreativePreview key={asset.id} asset={asset} />
          ))
        ) : (
          <p>尚未选择真实素材</p>
        )}
      </div>
    </div>
  );
}
function CreativePreview({ asset }: { asset: SelectedResource }) {
  return (
    <div>
      <b>{asset.name}</b>
      <small>
        {asset.status || "审核状态待接口确认"} · {asset.specs?.join(" · ")}
      </small>
      {asset.previewUrl && /^https:\/\//.test(asset.previewUrl) && (
        <a
          className="dw-link"
          href={asset.previewUrl}
          target="_blank"
          rel="noreferrer"
        >
          打开真实素材预览
        </a>
      )}
    </div>
  );
}
