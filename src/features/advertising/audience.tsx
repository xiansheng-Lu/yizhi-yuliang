"use client";
import { useState } from "react";
import { Copy, FloppyDisk, UsersThree } from "@phosphor-icons/react";
import {
  canEditField,
  advertisingProducts,
  configurationContext,
  emptyAudience,
  fieldFor,
  type AdvertisingDraft,
  type AudienceConfiguration,
  type ConfigurationCapability,
  type LocalTargetingTemplate,
} from "@/lib/advertising";
import type { AdvertisingService } from "@/services/advertising-service";
import { Choice, Field, ResourcePicker } from "./controls";

export function AudienceTargetingPanel({
  draft: d,
  cap,
  api,
  onChange,
  templates,
  onSaveTemplate,
  onCopyTemplate,
}: {
  draft: AdvertisingDraft;
  cap: ConfigurationCapability | null;
  api: AdvertisingService;
  onChange: (value: AudienceConfiguration) => void;
  templates: LocalTargetingTemplate[];
  onSaveTemplate: (name: string) => void;
  onCopyTemplate: (template: LocalTargetingTemplate) => void;
}) {
  const [templateName, setTemplateName] = useState("");
  const a = d.audience,
    context = configurationContext(d),
    f = (id: Parameters<typeof fieldFor>[2]) => fieldFor(d, cap, id);
  const patch = (v: Partial<AudienceConfiguration>) => onChange({ ...a, ...v });
  const smartManaged =
    d.product === "shop" || cap?.audienceManagement === "PLATFORM_SMART";
  if (smartManaged)
    return (
      <div className="aw-smart">
        <UsersThree size={32} />
        <h3>系统智能匹配目标人群</h3>
        <p>
          {cap?.reason ||
            "小店版按商品托管、直播推广分别适配。账户尚未接入，自动管理范围仍需平台确认。"}
        </p>
        <p>
          不提供未经账户能力确认的手动定向。方案可继续配置预算、对象与允许的素材。
        </p>
      </div>
    );
  const modeField = f("targetingMode"),
    age = f("age"),
    gender = f("gender"),
    region = f("region");
  return (
    <div className="aw-panels">
      <Field
        label={d.product === "beans" ? "加热人群方式" : "定向方式"}
        cap={modeField}
      >
        <div className="aw-segments">
          {(
            [
              ["CUSTOM", "自定义定向"],
              ["SMART", d.product === "beans" ? "智能加热" : "智能定向"],
              ["TEMPLATE", "平台定向包"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              aria-pressed={a.mode === value}
              disabled={
                cap
                  ? !modeField.options?.some(
                      (o) => o.id === value && o.available,
                    ) || modeField.state !== "AVAILABLE"
                  : value !== "CUSTOM"
              }
              onClick={() => onChange({ ...emptyAudience(), mode: value })}
            >
              {label}
              {!cap && value === "CUSTOM" ? " · 草稿" : ""}
            </button>
          ))}
        </div>
      </Field>
      {a.mode === "TEMPLATE" ? (
        <ResourcePicker
          label="平台真实定向包"
          kind="platformTemplates"
          context={context}
          api={api}
          cap={f("template")}
          single
          selected={a.platformTemplate ? [a.platformTemplate] : []}
          onChange={(items) => patch({ platformTemplate: items[0] ?? null })}
        />
      ) : a.mode === "SMART" ? (
        <div className="aw-note">
          由平台按当前账户策略匹配人群；只有能力接口明确开放的约束才可设置。
        </div>
      ) : (
        <>
          <Field label="地域定向" cap={region}>
            <div className="aw-segments">
              {(
                [
                  ["ANY", "不限"],
                  ["REGION", "省 / 市 / 区县"],
                  ["BUSINESS", "商圈"],
                  ["CUSTOM", "自定义位置"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  aria-pressed={a.locationMode === value}
                  disabled={
                    !canEditField(region, true) ||
                    (value !== "ANY" &&
                      (cap
                        ? !region.options?.some(
                            (o) => o.id === value && o.available,
                          )
                        : value !== "REGION"))
                  }
                  onClick={() =>
                    patch({
                      locationMode: value,
                      locations: [],
                      locationTypeId: "",
                    })
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          </Field>
          {a.locationMode !== "ANY" && (
            <>
              <ResourcePicker
                key={a.locationMode}
                label={
                  a.locationMode === "REGION"
                    ? "行政地区"
                    : a.locationMode === "BUSINESS"
                      ? "商圈标签"
                      : "账户自定义位置"
                }
                kind={
                  a.locationMode === "REGION"
                    ? "regions"
                    : a.locationMode === "BUSINESS"
                      ? "businessDistricts"
                      : "customLocations"
                }
                hierarchical={a.locationMode === "REGION"}
                api={api}
                cap={region}
                context={context}
                selected={a.locations}
                onChange={(locations) => patch({ locations })}
              />
              {cap?.fields.locationType && (
                <Choice
                  label="地点类型"
                  cap={f("locationType")}
                  value={a.locationTypeId}
                  onChange={(locationTypeId) => patch({ locationTypeId })}
                />
              )}
              <p className="aw-note">
                仅选择接口返回的合法平台
                ID。自定义位置由后端返回已校验的坐标和半径资源，不接受手写中文地址替代。
              </p>
            </>
          )}
          <div className="aw-grid">
            <Field label="年龄定向" cap={age}>
              <div className="aw-inline">
                <input
                  type="number"
                  aria-label="年龄下限"
                  placeholder="不限"
                  min={age.min}
                  max={age.max}
                  step="1"
                  disabled={!canEditField(age, true)}
                  value={a.age.min}
                  onChange={(e) =>
                    patch({ age: { ...a.age, min: e.target.value } })
                  }
                />
                <span>至</span>
                <input
                  type="number"
                  aria-label="年龄上限"
                  placeholder="不限"
                  min={age.min}
                  max={age.max}
                  step="1"
                  disabled={!canEditField(age, true)}
                  value={a.age.max}
                  onChange={(e) =>
                    patch({ age: { ...a.age, max: e.target.value } })
                  }
                />
                <button
                  className="dw-link"
                  disabled={!canEditField(age, true)}
                  onClick={() => patch({ age: { min: "", max: "" } })}
                >
                  不限
                </button>
              </div>
              <small>
                {age.allowedMin && `允许下限：${age.allowedMin.join("、")}。`}
                {age.allowedMax && `允许上限：${age.allowedMax.join("、")}。`}
                {!cap &&
                  "公开旧版文档存在特殊年龄边界；当前账户合法范围待接口确认。"}
              </small>
            </Field>
            <Field label="性别定向" cap={gender}>
              <select
                aria-label="性别定向"
                value={a.gender}
                disabled={!canEditField(gender, true)}
                onChange={(e) =>
                  patch({
                    gender: e.target.value as AudienceConfiguration["gender"],
                  })
                }
              >
                <option value="ALL">不限</option>
                <option value="MALE">男性</option>
                <option value="FEMALE">女性</option>
              </select>
            </Field>
          </div>
          <div className="aw-subheading">
            <h3>行为与兴趣</h3>
            <p>
              标签、意向与关键词均从真实接口查询。出价或版位变化后，能力接口会禁用不兼容类型。
            </p>
          </div>
          <ResourcePicker
            label={d.product === "beans" ? "兴趣领域" : "兴趣标签"}
            kind="interests"
            api={api}
            cap={f("interest")}
            context={context}
            selected={a.interests}
            onChange={(interests) => patch({ interests })}
          />
          {advertisingProducts[d.product].flow === "ADVERTISING" ? (
            <>
              <ResourcePicker
                label="行为标签"
                kind="behaviors"
                api={api}
                cap={f("behavior")}
                context={context}
                selected={a.behaviors}
                onChange={(behaviors) => patch({ behaviors })}
              />
              {a.behaviors.length > 0 && (
                <div className="aw-grid">
                  <Choice
                    label="行为场景"
                    cap={f("behaviorScene")}
                    value={a.behaviorSceneId}
                    onChange={(behaviorSceneId) => patch({ behaviorSceneId })}
                  />
                  <Choice
                    label="行为时间窗"
                    cap={f("behaviorWindow")}
                    value={a.behaviorWindowId}
                    onChange={(behaviorWindowId) => patch({ behaviorWindowId })}
                  />
                  <Choice
                    label="行为强度"
                    cap={f("behaviorIntensity")}
                    value={a.behaviorIntensityId}
                    onChange={(behaviorIntensityId) =>
                      patch({ behaviorIntensityId })
                    }
                  />
                </div>
              )}
              <ResourcePicker
                label="意向标签"
                kind="intentions"
                api={api}
                cap={f("intention")}
                context={context}
                selected={a.intentions}
                onChange={(intentions) => patch({ intentions })}
              />
              <ResourcePicker
                label="平台关键词"
                kind="keywords"
                api={api}
                cap={f("keyword")}
                context={context}
                selected={a.keywords}
                onChange={(keywords) => patch({ keywords })}
              />
              <div className="aw-subheading">
                <h3>自定义人群</h3>
                <p>人群包须处于可用状态，并满足当前出价方式的人群来源规则。</p>
              </div>
              <div className="aw-grid">
                <ResourcePicker
                  label="定向人群包"
                  kind="audiences"
                  api={api}
                  cap={f("customAudience")}
                  context={context}
                  selected={a.include}
                  excluded={a.exclude.map((r) => r.id)}
                  onChange={(include) => patch({ include })}
                />
                <ResourcePicker
                  label="排除人群包"
                  kind="audiences"
                  api={api}
                  cap={f("customAudience")}
                  context={context}
                  selected={a.exclude}
                  excluded={a.include.map((r) => r.id)}
                  onChange={(exclude) => patch({ exclude })}
                />
              </div>
            </>
          ) : (
            <>
              <ResourcePicker
                label="作者粉丝"
                kind="creators"
                api={api}
                cap={f("creators")}
                context={context}
                selected={a.creators}
                onChange={(creators) => patch({ creators })}
              />
              <ResourcePicker
                label="群聊"
                kind="groups"
                api={api}
                cap={f("groups")}
                context={context}
                selected={a.groups}
                onChange={(groups) => patch({ groups })}
              />
            </>
          )}
        </>
      )}
      {cap?.fields.expansion?.state === "AVAILABLE" && (
        <Field label="智能扩量" cap={f("expansion")}>
          <label className="aw-check">
            <input
              type="checkbox"
              checked={a.expansion}
              onChange={(e) =>
                patch({
                  expansion: e.target.checked,
                  hardConstraints: e.target.checked ? a.hardConstraints : [],
                })
              }
            />
            允许平台按账户规则智能扩量
          </label>
        </Field>
      )}
      {a.expansion && cap?.fields.hardConstraints?.state === "AVAILABLE" && (
        <Field label="不可突破定向" cap={f("hardConstraints")}>
          <div className="aw-inline">
            {f("hardConstraints").options?.map((o) => (
              <label className="aw-check" key={o.id}>
                <input
                  type="checkbox"
                  disabled={!o.available}
                  checked={a.hardConstraints.includes(o.id)}
                  onChange={(e) =>
                    patch({
                      hardConstraints: e.target.checked
                        ? [...a.hardConstraints, o.id]
                        : a.hardConstraints.filter((id) => id !== o.id),
                    })
                  }
                />
                {o.name}
              </label>
            ))}
          </div>
        </Field>
      )}
      <div className="aw-subheading">
        <h3>驭量本地定向模板</h3>
        <p>
          仅保存在此浏览器与当前成员名下。模板不是平台定向包，复用后仍需重新核验。
        </p>
      </div>
      <div className="aw-inline">
        <input
          aria-label="本地定向模板名称"
          maxLength={100}
          placeholder="例如：本次人群配置"
          value={templateName}
          onChange={(e) => setTemplateName(e.target.value)}
        />
        <button
          className="button secondary"
          disabled={!templateName.trim()}
          onClick={() => {
            onSaveTemplate(templateName.trim());
            setTemplateName("");
          }}
        >
          <FloppyDisk size={15} />
          保存本地模板
        </button>
      </div>
      {templates
        .filter((t) => t.product === d.product && t.scenario === d.scenario)
        .map((t) => (
          <div className="aw-template" key={t.id}>
            <span>
              {t.name}
              <small>驭量本地 · {t.accountId || "未绑定账户"}</small>
            </span>
            <div className="aw-inline">
              <button
                className="dw-link"
                disabled={t.accountId !== d.accountId}
                title={
                  t.accountId !== d.accountId
                    ? "模板资源属于其他账户，需重新选择资源"
                    : "复用到当前草稿"
                }
                onClick={() => onChange(structuredClone(t.audience))}
              >
                复用
              </button>
              <button className="dw-link" onClick={() => onCopyTemplate(t)}>
                <Copy size={14} />
                复制
              </button>
            </div>
          </div>
        ))}
    </div>
  );
}
