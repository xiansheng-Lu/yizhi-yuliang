"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowSquareOut,
  CheckCircle,
  DownloadSimple,
  FloppyDisk,
  Plus,
  WarningCircle,
} from "@phosphor-icons/react";
import { useWorkspace } from "@/components/provider";
import { useDelivery } from "@/features/delivery/context";
import { deliveryHref } from "@/lib/delivery";
import { Status } from "@/features/delivery/ui";
import {
  advertisingProducts,
  advertisingProviderNames,
  changeConfiguration,
  configurationContext,
  configurationContextKey,
  configurationSteps,
  emptyAdvertisingStorage,
  isAdvertisingProduct,
  newAdvertisingDraft,
  readAdvertisingStorage,
  reconcileConfiguration,
  scenarioNames,
  storageKey,
  submissionBlock,
  validateConfiguration,
  type AdvertisingDraft,
  type AdvertisingProduct,
  type AdvertisingScenario,
  type AdvertisingStorage,
  type ConfigurationCapability,
  type ConfigurationCatalog,
  type LocalTargetingTemplate,
  type SubmissionTicket,
} from "@/lib/advertising";
import {
  AdvertisingService,
  configurationError,
  type AudienceEstimate,
  type ConfigurationTask,
  type ConfigurationValidation,
} from "@/services/advertising-service";
import { ServiceError } from "@/services/contracts";
import { AudienceTargetingPanel } from "./audience";
import {
  AccountAndAssetSelector,
  BidAndBudgetPanel,
  CreativeSelector,
  PlacementSelector,
} from "./panels";
import { ConfigurationSummary } from "./summary";
import { productCatalog, verificationStageNames, verificationPendingNames, type VerificationStage } from "@/lib/platform-catalog";
import "@/styles/advertising.css";

export function selectedAdvertisingProduct(
  query: URLSearchParams,
  platform: string,
): AdvertisingProduct {
  const p = query.get("product");
  return isAdvertisingProduct(p) ? p : platform === "beans" ? "beans" : "adq";
}
export function PlatformProductSelector({
  value,
  onChange,
}: {
  value: AdvertisingProduct;
  onChange: (value: AdvertisingProduct) => void;
}) {
  return (
    <div>
      <div className="aw-product-pickers">
        <label>
          投放平台
          <select
            aria-label="配置投放平台"
            value={advertisingProducts[value].provider}
            onChange={(e) => {
              const first = Object.entries(advertisingProducts).find(
                ([, p]) => p.provider === e.target.value,
              );
              if (first) onChange(first[0] as AdvertisingProduct);
            }}
          >
            {Object.entries(advertisingProviderNames).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label>
          产品线
          <select
            aria-label="配置投放产品线"
            value={value}
            onChange={(e) => onChange(e.target.value as AdvertisingProduct)}
          >
            {Object.entries(advertisingProducts)
              .filter(
                ([, p]) => p.provider === advertisingProducts[value].provider,
              )
              .map(([id, p]) => (
                <option key={id} value={id}>
                  {p.name}
                </option>
              ))}
          </select>
        </label>
      </div>
      <div className="aw-products" aria-label="选择投放产品">
        {Object.entries(advertisingProducts)
          .filter(([, p]) => p.provider === advertisingProducts[value].provider)
          .map(([id, product]) => (
            <button
              key={id}
              aria-pressed={value === id}
              onClick={() => onChange(id as AdvertisingProduct)}
            >
              <strong>{product.name}</strong>
              <span>{product.description}</span>
              <small>
                {id === "shop"
                  ? "待接入 · 独立产品"
                  : "查看配置 · 提交需真实授权"}
              </small>
            </button>
          ))}
      </div>
    </div>
  );
}
export function AdvertisingWizard({
  product,
}: {
  product: AdvertisingProduct;
}) {
  const d = useDelivery(),
    workspace = useWorkspace();
  const api = useMemo(
    () => new AdvertisingService(workspace.service!, d.identity),
    [workspace.service, d.identity],
  );
  const ownerKey = storageKey(d.identity);
  const [storage, setStorage] = useState<AdvertisingStorage>(
      emptyAdvertisingStorage,
    ),
    [draft, setDraft] = useState(() => newAdvertisingDraft(product));
  const [ready, setReady] = useState(false),
    [step, setStep] = useState(0),
    [message, setMessage] = useState(""),
    [storageError, setStorageError] = useState(""),
    [refresh, setRefresh] = useState(0);
  const [catalogRead, setCatalogRead] = useState<{
    catalog: ConfigurationCatalog | null;
    error: string;
  }>({ catalog: null, error: "正在读取产品账户目录…" });
  const [capRead, setCapRead] = useState<{
    key: string;
    cap: ConfigurationCapability | null;
    error: string;
  } | null>(null);
  const [validation, setValidation] = useState<{
      fingerprint: string;
      result: ConfigurationValidation;
    } | null>(null),
    [task, setTask] = useState<ConfigurationTask | null>(null),
    [estimate, setEstimate] = useState<{
      fingerprint: string;
      value: AudienceEstimate;
    } | null>(null);
  const [busy, setBusy] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const lock = useRef(false),
    mounted = useRef(true),
    draftRef = useRef(draft),
    storageRef = useRef(storage);
  const context = configurationContext(draft),
    key = configurationContextKey(context),
    keyRef = useRef(key);
  useEffect(() => {
    draftRef.current = draft;
    storageRef.current = storage;
    keyRef.current = key;
  }, [draft, storage, key]);
  const [now, setNow] = useState(0);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    tick();
    const timer = setInterval(tick, 10_000);
    return () => clearInterval(timer);
  }, []);
  const cap = capRead?.key === key ? capRead.cap : null;
  const fingerprint = JSON.stringify(draft);
  const steps = configurationSteps(product, draft.scenario);
  function writeStore(next: AdvertisingStorage) {
    storageRef.current = next;
    setStorage(next);
    try {
      localStorage.setItem(ownerKey, JSON.stringify(next));
      setStorageError("");
      return true;
    } catch {
      setStorageError(
        "浏览器本地存储不可用。请导出方案保存，当前更改尚未持久保存。",
      );
      return false;
    }
  }
  function persist(
    next: AdvertisingDraft,
    notice = "已自动保存本地草稿；尚未提交平台。",
  ) {
    const value = {
      ...next,
      id: next.id === "new" ? crypto.randomUUID() : next.id,
      updatedAt: new Date().toISOString(),
    };
    draftRef.current = value;
    setDraft(value);
    setValidation(null);
    setAcknowledged(false);
    setEstimate(null);
    setTask(null);
    const saved = writeStore({
      ...storageRef.current,
      drafts: [
        value,
        ...storageRef.current.drafts.filter((p) => p.id !== value.id),
      ].slice(0, 20),
    });
    d.markDirty(!saved);
    setMessage(saved ? notice : "本地保存失败，请导出方案。");
  }
  const persistRef = useRef(persist);
  useEffect(() => {
    persistRef.current = persist;
  });
  function patch(value: Partial<AdvertisingDraft>) {
    if (lock.current) return;
    const changed = changeConfiguration(draftRef.current, value);
    persist(
      cap &&
        cap.contextKey ===
          configurationContextKey(configurationContext(changed.draft))
        ? reconcileConfiguration(changed.draft, cap)
        : changed.draft,
      changed.cleared
        ? "配置上下文已变化，已清除依赖的对象、人群、素材或出价参数，请重新确认。"
        : "已自动保存本地草稿；尚未提交平台。",
    );
  }
  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    void Promise.resolve().then(() => {
      if (cancelled) return;
      let saved = emptyAdvertisingStorage();
      try {
        saved = readAdvertisingStorage(localStorage.getItem(ownerKey));
      } catch {
        setStorageError("浏览器禁止读取本地草稿，可继续配置并导出方案。");
      }
      storageRef.current = saved;
      setStorage(saved);
      const restored = saved.drafts.find((p) => p.product === product);
      if (restored) {
        draftRef.current = restored;
        setDraft(restored);
        setMessage("已恢复本地草稿；账户权限、平台 ID 与规则将重新核验。");
      }
      setReady(true);
    });
    return () => {
      cancelled = true;
      mounted.current = false;
    };
  }, [ownerKey, product]);
  useEffect(() => {
    const abort = new AbortController();
    void api
      .catalog(product, abort.signal)
      .then((catalog) => {
        if (!abort.signal.aborted) setCatalogRead({ catalog, error: "" });
      })
      .catch((error) => {
        if (!abort.signal.aborted)
          setCatalogRead({ catalog: null, error: configurationError(error) });
      });
    return () => abort.abort();
  }, [api, product, refresh]);
  useEffect(() => {
    if (!ready || !context.accountId) return;
    const abort = new AbortController();
    void api
      .capabilities(context, abort.signal)
      .then((value) => {
        if (abort.signal.aborted || keyRef.current !== key) return;
        const latest = draftRef.current,
          pruned = reconcileConfiguration(latest, value);
        setCapRead({ key, cap: value, error: "" });
        if (JSON.stringify(pruned) !== JSON.stringify(latest))
          persistRef.current(
            pruned,
            "账户能力已更新，不兼容字段已清除；请检查配置摘要。",
          );
      })
      .catch((error) => {
        if (!abort.signal.aborted)
          setCapRead({ key, cap: null, error: configurationError(error) });
      });
    return () => abort.abort();
    // All context fields are represented by key; draft-only edits preserve the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, key, ready, refresh]);
  const catalog = catalogRead.catalog,
    account = catalog?.accounts.find((a) => a.id === draft.accountId);
  const issues = validateConfiguration(draft, cap);
  const block = submissionBlock(
    draft,
    cap,
    d.identity.mode,
    d.identity.permissions,
    !!account?.writable && account.authorization === "AUTHORIZED",
  );
  const ticket = storage.tickets.find(
    (t) =>
      t.product === product &&
      (t.draftId === draft.id || t.accountId === draft.accountId),
  );
  const validated =
    validation?.fingerprint === fingerprint &&
    validation.result.validationToken &&
    Date.parse(validation.result.expiresAt) > now &&
    validation.result.checks.every((c) => c.passed || c.severity === "WARNING");
  const canSubmit =
    !block &&
    !issues.length &&
    validated &&
    acknowledged &&
    !ticket &&
    !busy &&
    !storageError;
  async function validate() {
    if (!cap || lock.current) return;
    const current = fingerprint;
    lock.current = true;
    setBusy(true);
    setMessage("");
    try {
      const result = await api.validate(draft, cap);
      if (mounted.current && JSON.stringify(draftRef.current) === current)
        setValidation({ fingerprint: current, result });
    } catch (error) {
      if (mounted.current) setMessage(configurationError(error));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function submit() {
    if (!canSubmit || !validation || !cap || lock.current) return;
    lock.current = true;
    setBusy(true);
    const nextTicket: SubmissionTicket = {
      draftId: draft.id,
      accountId: draft.accountId,
      product,
      idempotencyKey: crypto.randomUUID(),
      contextKey: key,
      createdAt: new Date().toISOString(),
    };
    const previous = storageRef.current;
    if (
      !writeStore({ ...previous, tickets: [...previous.tickets, nextTicket] })
    ) {
      lock.current = false;
      setBusy(false);
      return;
    }
    try {
      const result = await api.submit(
        draft,
        cap,
        validation.result,
        nextTicket,
      );
      if (mounted.current) {
        setTask(result);
        setMessage("后端已返回任务状态。请以平台状态与业务 ID 为准。");
        if (["REJECTED", "FAILED", "CANCELLED"].includes(result.state))
          writeStore({
            ...storageRef.current,
            tickets: storageRef.current.tickets.filter(
              (t) => t.idempotencyKey !== nextTicket.idempotencyKey,
            ),
          });
      }
    } catch (error) {
      if (mounted.current) {
        if (
          error instanceof ServiceError &&
          ["FORBIDDEN", "INVALID_INPUT", "CONFLICT", "NO_DATA"].includes(
            error.code,
          )
        )
          writeStore({
            ...storageRef.current,
            tickets: storageRef.current.tickets.filter(
              (t) => t.idempotencyKey !== nextTicket.idempotencyKey,
            ),
          });
        setMessage(
          error instanceof ServiceError && error.code === "NO_DATA"
            ? "正式创建接口未接入，未创建平台计划。"
            : `提交未能确认：${configurationError(error)}。请按原幂等键查询结果，不会自动重发。`,
        );
      }
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function queryResult() {
    if (!ticket || lock.current) return;
    lock.current = true;
    setBusy(true);
    try {
      const result = await api.result(ticket);
      if (mounted.current) {
        setTask(result);
        setMessage("已按原幂等键回读结果，未重复提交。");
        if (["FAILED", "REJECTED", "CANCELLED"].includes(result.state))
          writeStore({
            ...storageRef.current,
            tickets: storageRef.current.tickets.filter(
              (t) => t.idempotencyKey !== ticket.idempotencyKey,
            ),
          });
      }
    } catch (error) {
      if (mounted.current)
        setMessage(
          `结果仍待核查：${configurationError(error)}。保留原幂等键。`,
        );
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function cancelTask() {
    if (!ticket || !task?.cancellable || lock.current) return;
    lock.current = true;
    setBusy(true);
    setTask({ ...task, cancellable: false });
    try {
      const result = await api.cancel(ticket, task);
      if (mounted.current) {
        setTask(result);
        setMessage("已回读原任务的撤回状态，未重复创建投放。");
        if (["FAILED", "REJECTED", "CANCELLED"].includes(result.state))
          writeStore({ ...storageRef.current, tickets: storageRef.current.tickets.filter(t => t.idempotencyKey !== ticket.idempotencyKey) });
      }
    } catch (error) {
      if (mounted.current) setMessage(`撤回结果待核查：${configurationError(error)}。请查询原任务。`);
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function getEstimate() {
    if (!cap?.estimateSupported || lock.current) return;
    const current = fingerprint;
    setBusy(true);
    lock.current = true;
    try {
      const value = await api.estimate(draft);
      if (mounted.current && JSON.stringify(draftRef.current) === current)
        setEstimate({ fingerprint: current, value });
    } catch (error) {
      if (mounted.current) setMessage(configurationError(error));
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  function exportProposal() {
    const blob = new Blob(
      [
        JSON.stringify(
          {
            documentType: "YULIANG_LOCAL_PROPOSAL",
            notice:
              "本地配置意向，未提交平台；需要重新读取授权并校验所有平台资源。",
            configuration: draft,
            frontendChecks: issues,
          },
          null,
          2,
        ),
      ],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob),
      link = document.createElement("a");
    link.href = url;
    link.download = `驭量-${advertisingProducts[product].short}-待执行方案.json`;
    link.click();
    URL.revokeObjectURL(url);
  }
  function saveTemplate(name: string) {
    const t: LocalTargetingTemplate = {
      id: crypto.randomUUID(),
      name,
      product,
      scenario: draft.scenario,
      accountId: draft.accountId,
      audience: structuredClone(draft.audience),
      updatedAt: new Date().toISOString(),
    };
    writeStore({
      ...storageRef.current,
      templates: [t, ...storageRef.current.templates].slice(0, 30),
    });
    setMessage("已保存驭量本地模板；未在平台创建定向包。");
  }
  function newDraft(scenario: AdvertisingScenario = draft.scenario) {
    persist(
      newAdvertisingDraft(product, scenario, crypto.randomUUID()),
      "已建立新的本地草稿；原草稿保留在草稿列表。",
    );
    setStep(0);
  }
  const props = { draft, cap, api, patch };
  return (
    <div className="advertising-workbench" aria-label="新建投放配置工作台">
      <header className="aw-header">
        <div>
          <button
            className="dw-link"
            disabled={busy}
            onClick={() =>
              d.navigate(
                d.href("plans", product === "shop" ? { product: "shop" } : {}),
              )
            }
          >
            <ArrowLeft size={15} />
            返回计划中心
          </button>
          <h2>新建投放配置</h2>
          <p>
            配置工作台 / {advertisingProducts[product].name} /{" "}
            {scenarioNames[draft.scenario]}
          </p>
        </div>
        <Status
          tone={cap?.authorization === "AUTHORIZED" ? "green" : "neutral"}
        >
          {cap?.authorization === "AUTHORIZED"
            ? "账户能力已核验"
            : d.identity.mode === "MOCK"
              ? "演示模式 · 正式写入隔离"
              : "配置草稿 · 授权待核验"}
        </Status>
      </header>
      <PlatformProductSelector
        value={product}
        onChange={(p) => {
          if (!busy)
            d.navigate(deliveryHref("plans",{...d.scope,platform:p==="adq"||p==="beans"?p:"all",accountId:"all"},{ view: "create", product: p }));
        }}
      />
      <div className="aw-draft-bar">
        <label>
          产品场景
          <select
            aria-label="投放产品场景"
            disabled={busy}
            value={draft.scenario}
            onChange={(e) => newDraft(e.target.value as AdvertisingScenario)}
          >
            {advertisingProducts[product].scenarios.map((s) => (
              <option key={s} value={s}>
                {scenarioNames[s]}
              </option>
            ))}
          </select>
        </label>
        <label>
          本地草稿
          <select
            aria-label="恢复本地草稿"
            value={draft.id}
            disabled={!ready || busy}
            onChange={(e) => {
              const saved = storage.drafts.find((p) => p.id === e.target.value);
              if (saved) {
                setDraft(structuredClone(saved));
                draftRef.current = saved;
                setValidation(null);
                setTask(null);
                setStep(0);
                setMessage("已恢复本地草稿，等待重新校验。");
              }
            }}
          >
            <option value="new">尚未保存的配置</option>
            {storage.drafts
              .filter((p) => p.product === product)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name || "未命名草稿"} · {scenarioNames[p.scenario]} ·{" "}
                  {p.updatedAt.slice(0, 16).replace("T", " ")} UTC
                </option>
              ))}
          </select>
        </label>
        <button
          className="button secondary"
          disabled={!ready || busy}
          onClick={() => newDraft()}
        >
          <Plus size={15} />
          新草稿
        </button>
        <button
          className="button secondary"
          disabled={!ready || busy}
          onClick={() =>
            persist(draft, "本地草稿已保存，可在此浏览器恢复；未创建平台计划。")
          }
        >
          <FloppyDisk size={15} />
          保存本地草稿
        </button>
        <button
          className="button secondary"
          disabled={busy}
          onClick={exportProposal}
        >
          <DownloadSimple size={15} />
          导出待执行方案
        </button>
      </div>
      {(message || storageError) && (
        <div
          className={`aw-feedback ${storageError ? "warning" : ""}`}
          role="status"
        >
          {storageError ? (
            <WarningCircle size={17} />
          ) : (
            <CheckCircle size={17} />
          )}
          <span>{storageError || message}</span>
        </div>
      )}
      <div className="aw-capability-status">
        <span>
          {cap
            ? `平台 API ${cap.apiVersion} · 能力版本 ${cap.version} · 有效至 ${cap.expiresAt}`
            : capRead?.key === key && capRead.error
              ? capRead.error
              : !draft.accountId || !draft.shopId
                ? catalogRead.error || "选择账户与关联店铺后查询当前场景能力。"
                : "正在读取当前配置的账户能力…"}
        </span>
        <button
          className="dw-link"
          disabled={busy}
          onClick={() => {
            setCapRead(null);
            setValidation(null);
            setRefresh((n) => n + 1);
          }}
        >
          重新读取能力
        </button>
      </div>
      <div className="pi-stages" aria-label="配置能力核验阶段">
        {Object.entries(verificationStageNames).map(([stage, label]) => {
          const proof = cap?.verification;
          const passed =
            stage === "directory"
              ? productCatalog(product).length > 0
              : stage === "path"
                ? proof?.pathVerified
                : stage === "applicationScope"
                  ? proof?.applicationScopeGranted
                  : stage === "account"
                    ? proof?.accountAuthorized
                    : stage === "backend"
                      ? proof?.backendIntegrated
                      : stage === "requestSchema"
                        ? proof?.requestSchemaVerified
                        : proof?.executionVerified;
          return (
            <span key={stage} className={passed ? "verified" : "pending"}>
              {passed ? <CheckCircle size={12} /> : <WarningCircle size={12} />}{" "}
              {passed?label:verificationPendingNames[stage as VerificationStage]}
            </span>
          );
        })}
      </div>
      <div className={`aw-layout ${step === 5 ? "aw-preview" : ""}`}>
        <nav className="aw-steps" aria-label="投放配置步骤">
          {steps.map((name, i) => (
            <button
              key={name}
              disabled={!ready || busy}
              aria-current={step === i ? "step" : undefined}
              onClick={() => setStep(i)}
            >
              <span>{i + 1}</span>
              <div>
                {name}
                <small>
                  {i === step
                    ? "正在配置"
                    : issues.some((p) => p.step === i)
                      ? "有待完善项"
                      : "查看与编辑"}
                </small>
              </div>
            </button>
          ))}
        </nav>
        <main className="aw-main" aria-busy={busy}>
          <div className="aw-step-heading">
            <span>STEP {String(step + 1).padStart(2, "0")} / 06</span>
            <h2>{steps[step]}</h2>
            <p>可以返回任一步骤编辑。本地意向与账户可用能力分别标注。</p>
          </div>
          <fieldset disabled={!ready || busy} className="aw-body">
            {step === 0 ? (
              <AccountAndAssetSelector
                {...props}
                catalog={catalog}
                error={catalogRead.error}
              />
            ) : step === 1 ? (
              <PlacementSelector {...props} />
            ) : step === 2 ? (
              <AudienceTargetingPanel
                {...props}
                onChange={(audience) => patch({ audience })}
                templates={storage.templates}
                onSaveTemplate={saveTemplate}
                onCopyTemplate={(t) => {
                  writeStore({
                    ...storageRef.current,
                    templates: [
                      {
                        ...structuredClone(t),
                        id: crypto.randomUUID(),
                        name: `${t.name} 副本`,
                      },
                      ...storageRef.current.templates,
                    ].slice(0, 30),
                  });
                  setMessage("已复制本地模板。");
                }}
              />
            ) : step === 3 ? (
              <BidAndBudgetPanel {...props} />
            ) : step === 4 ? (
              <CreativeSelector key={key} {...props} />
            ) : (
              <>
                <ConfigurationSummary
                  draft={draft}
                  cap={cap}
                  catalog={catalog}
                />
                <div className="aw-validation">
                  <h3>配置校验与执行边界</h3>
                  {issues.length ? (
                    <ul>
                      {issues.map((issue, i) => (
                        <li key={`${issue.field}-${i}`}>
                          <button
                            className="dw-link"
                            onClick={() => setStep(issue.step)}
                          >
                            步骤 {issue.step + 1} · {issue.message}
                          </button>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p>
                      前端字段检查通过。真实资源、权限、资金、组合规则仍需后端校验。
                    </p>
                  )}
                  <div className="aw-note">
                    {block ||
                      "已具备后端写入条件，提交前还需完成服务端配置校验。"}
                  </div>
                  <button
                    className="button secondary"
                    disabled={!cap || issues.length > 0 || !!block || busy}
                    onClick={() => void validate()}
                  >
                    校验真实账户与配置
                  </button>
                  {validation?.fingerprint === fingerprint && (
                    <div className="aw-server-checks">
                      <p>
                        后端校验
                        {validation.result.requiresApproval
                          ? "要求审批"
                          : "已返回"}{" "}
                        · 有效至 {validation.result.expiresAt}
                      </p>
                      {validation.result.checks.map((check, i) => (
                        <p key={i}>
                          {check.passed ? "✓" : "!"} {check.severity} ·{" "}
                          {check.message}
                        </p>
                      ))}
                    </div>
                  )}
                  <label className="aw-check">
                    <input
                      type="checkbox"
                      disabled={!validated}
                      checked={acknowledged}
                      onChange={(e) => setAcknowledged(e.target.checked)}
                    />
                    我已核对账户、预算、投放时间与审核风险。后端决定审批、支付及执行状态。
                  </label>
                  <button
                    className="button primary"
                    disabled={!canSubmit}
                    onClick={() => void submit()}
                  >
                    {busy ? "处理中…" : "提交真实投放任务"}
                  </button>
                  {product === "beans" && (
                    <a
                      className="button secondary"
                      href="https://channels.weixin.qq.com/"
                      target="_blank"
                      rel="noreferrer"
                    >
                      <ArrowSquareOut size={15} />
                      前往视频号官方入口
                    </a>
                  )}
                  {ticket && (
                    <div className="aw-note">
                      <strong>已有提交记录，禁止重复创建</strong>
                      <p>幂等键：{ticket.idempotencyKey}</p>
                      <button
                        className="button secondary"
                        disabled={busy || d.identity.mode !== "API"}
                        onClick={() => void queryResult()}
                      >
                        查询原任务结果
                      </button>
                    </div>
                  )}
                  {task && (
                    <div className="aw-task-result" role="status">
                      <strong>后端任务状态：{task.state}</strong>
                      <p>{task.message}</p>
                      <dl>
                        <dt>任务 ID</dt>
                        <dd>{task.taskId}</dd>
                        <dt>平台业务 ID</dt>
                        <dd>{task.businessId || "尚未返回"}</dd>
                        <dt>平台状态</dt>
                        <dd>{task.platformStatus || "待回读"}</dd>
                        <dt>回读核验</dt>
                        <dd>{task.verification}</dd>
                        <dt>审批 ID</dt><dd>{task.approvalId||"不适用 / 未提供"}</dd>
                      </dl>
                      <p>
                        {task.state === "SUCCEEDED"
                          ? "平台业务 ID 已由后端返回。"
                          : "申请、待审批、待支付与处理中均不代表平台计划已创建成功。"}
                      </p>
                      {task.cancellable&&<button className="button secondary" disabled={busy} onClick={()=>void cancelTask()}>撤回未执行投放申请</button>}
                    </div>
                  )}
                </div>
              </>
            )}
          </fieldset>
          <footer className="aw-step-footer">
            <button
              className="button secondary"
              disabled={step === 0 || busy}
              onClick={() => setStep((n) => n - 1)}
            >
              <ArrowLeft size={15} />
              上一步
            </button>
            <span>
              {draft.updatedAt ? "草稿仅保存在本机浏览器" : "尚未保存"}
            </span>
            <button
              className="button primary"
              disabled={step === 5 || busy || !ready}
              onClick={() => setStep((n) => n + 1)}
            >
              {step === 4 ? "确认预览" : "下一步"}
              <ArrowRight size={15} />
            </button>
          </footer>
        </main>
        {step !== 5 && (
          <aside className="aw-sidebar">
            <ConfigurationSummary
              draft={draft}
              cap={cap}
              catalog={catalog}
              compact
            />
            {cap?.estimateSupported && (
              <div className="aw-estimate">
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => void getEstimate()}
                >
                  查询平台真实覆盖预估
                </button>
                {estimate?.fingerprint === fingerprint && (
                  <p>
                    {estimate.value.lower.toLocaleString()}–
                    {estimate.value.upper.toLocaleString()} 人<br />
                    {estimate.value.description}
                  </p>
                )}
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
