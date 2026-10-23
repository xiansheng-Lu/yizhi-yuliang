"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowClockwise,
  ArrowSquareOut,
  CheckCircle,
  MagnifyingGlass,
  Plus,
  ShieldCheck,
  WarningCircle,
} from "@phosphor-icons/react";
import { useWorkspace } from "@/components/provider";
import { advertisingProducts } from "@/lib/advertising";
import { configurationError } from "@/services/advertising-service";
import {
  integrationScopeKey,
  productCatalog,
  productLineName,
  providerOf,
  queryBlock,
  resourceNames,
  resourcesForPage,
  verificationStageNames,
  verificationPendingNames,
  catalogEvidence,
  type IntegrationContext,
  type IntegrationScope,
  type OperationEvidence,
  type PlatformApiRecord,
  type PlatformResource,
  type PlatformResourcePage,
  type PlatformResourceRow,
  type ProductLine,
} from "@/lib/platform-catalog";
import {
  PlatformService,
  type PlatformChange,
  type PlatformChangePreview,
  type PlatformCommandResult,
} from "@/services/platform-service";
import { ProductScopeSelector } from "./advertising/product-selector";
import { Modal, Status } from "./delivery/ui";
import { money } from "@/lib/domain";
import { todayShanghai } from "@/lib/delivery";
import "@/styles/platform-workspace.css";

export function VerificationStages({
  evidence,
}: {
  evidence: OperationEvidence;
}) {
  return (
    <div className="pi-stages">
      {Object.entries(verificationStageNames).map(([stage, name]) => {
        const e = evidence[stage as keyof OperationEvidence];
        return (
          <span
            key={stage}
            className={e.verified ? "verified" : "pending"}
            title={`${e.reason}${e.proofId ? ` · 证明 ${e.proofId}` : ""}${e.checkedAt ? ` · ${e.checkedAt}` : ""}`}
          >
            {e.verified ? (
              <CheckCircle size={12} />
            ) : (
              <WarningCircle size={12} />
            )}{" "}
            {e.verified?name:verificationPendingNames[stage as keyof OperationEvidence]}
          </span>
        );
      })}
    </div>
  );
}
export function PlatformDataPanel({ pageId }: { pageId: string }) {
  const [open, setOpen] = useState(false);
  const commerce = [
    "overview",
    "monitor",
    "products",
    "analytics",
    "reports",
  ].includes(pageId);
  return (
    <section className="platform-data-panel">
      <button
        className="pi-panel-toggle"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <ShieldCheck size={17} />
        {commerce ? "经营数据、平台报表与接口能力" : "平台素材授权、规格与审核"}
        <span>{open ? "收起" : "展开数据工作区"}</span>
      </button>
      {open && (
        <PlatformWorkspace
          key={`${pageId}:embedded`}
          pageId={pageId}
          initialProduct={commerce ? "wxshop" : "adq"}
          embedded
        />
      )}
    </section>
  );
}
function CatalogBrowser({
  product,
  context,
  accountId,
}: {
  product: ProductLine;
  context: IntegrationContext | null;
  accountId: string;
}) {
  const [search, setSearch] = useState(""),
    [domain, setDomain] = useState("all"),
    [onlyVerified, setOnlyVerified] = useState(false),
    [page, setPage] = useState(1),
    [detail, setDetail] = useState<PlatformApiRecord | null>(null);
  const records = productCatalog(product),
    domains = [...new Set(records.map((r) => r.domain))];
  const filtered = records.filter(
    (r) =>
      (domain === "all" || r.domain === domain) &&
      (!onlyVerified || r.pathEvidence === "SOURCE_VERIFIED") &&
      [r.name, r.module, r.nativePath, r.normalizedPath, r.scopes.join(" ")]
        .join(" ")
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 12)),
    currentPage = Math.min(page, pages);
  const evidence = (record: PlatformApiRecord) =>
    context?.operations.find(
      (o) =>
        o.catalogId === record.id &&
        (!accountId || o.accountIds.includes(accountId)),
    )?.evidence ?? catalogEvidence(record);
  return (
    <div className="pi-catalog">
      <div className="pi-title">
        <h3>接口依据与能力矩阵</h3>
        <span>{records.length} 条产品线相关目录 · 目录不授予调用权限</span>
      </div>
      <div className="pi-filter">
        <label>
          <MagnifyingGlass size={14} />
          <input
            aria-label="搜索平台接口"
            placeholder="名称、路径、模块或 scope"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <select
          aria-label="接口领域"
          value={domain}
          onChange={(e) => {
            setDomain(e.target.value);
            setPage(1);
          }}
        >
          <option value="all">全部领域</option>
          {domains.map((d) => (
            <option key={d}>{d}</option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={onlyVerified}
            onChange={(e) => {
              setOnlyVerified(e.target.checked);
              setPage(1);
            }}
          />
          仅图谱已核验路径
        </label>
      </div>
      {!records.length ? (
        <div className="pi-empty">
          {product === "beans"
            ? "四份资料未提供微信豆专属加热订单写接口。"
            : product === "qianchuan"
              ? "当前图谱未明确给出千川专属接口归属，不能借用巨量营销或本地推接口。"
              : "此产品线专属接口归属仍需核验。"}
        </div>
      ) : (
        <div className="pi-catalog-rows">
          {filtered.slice((currentPage - 1) * 12, currentPage * 12).map((r) => (
            <article key={r.id}>
              <div className="pi-title">
                <button className="dw-link" onClick={() => setDetail(r)}>
                  {r.name}
                </button>
                <small>
                  {r.domain} / {r.module}
                </small>
              </div>
              <code>
                {r.method ?? "HTTP 方法待核验"}{" "}
                {r.normalizedPath ?? "请求路径待核验"}
              </code>
              <VerificationStages evidence={evidence(r)} />
            </article>
          ))}
          {!filtered.length && (
            <div className="pi-empty">没有匹配的目录条目</div>
          )}
        </div>
      )}
      <div className="pi-pagination">
        <span>
          {filtered.length} 条 · {currentPage} / {pages}
        </span>
        <button
          className="button secondary"
          disabled={currentPage === 1}
          onClick={() => setPage(currentPage - 1)}
        >
          上一页
        </button>
        <button
          className="button secondary"
          disabled={currentPage === pages}
          onClick={() => setPage(currentPage + 1)}
        >
          下一页
        </button>
      </div>
      {detail && (
        <Modal title="接口来源与核验依据" close={() => setDetail(null)} wide>
          <dl className="pi-facts">
            {[
              ["接口", detail.name],
              ["原始标识", detail.nativePath || "未提供"],
              ["规范化路径", detail.normalizedPath || "未核验"],
              [
                "HTTP 方法",
                detail.method || "原始资料未给出，不按路径后缀推断",
              ],
              ["scope", detail.scopes.join("、") || "未给出"],
              ["来源文件", detail.sourceFile],
              ["目录 ID", detail.id],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <VerificationStages evidence={evidence(detail)} />
          {detail.docUrl && (
            <a
              className="dw-link"
              href={detail.docUrl}
              target="_blank"
              rel="noreferrer"
            >
              查看接口文档 <ArrowSquareOut size={13} />
            </a>
          )}
          <p className="pi-notice">
            图谱对路径的标记仅是资料依据。真实调用仍需应用
            scope、账户授权、后端适配、参数验证和审计回读。浏览器不直接调用此平台地址。
          </p>
        </Modal>
      )}
    </div>
  );
}
const metricNames: Record<string, string> = {
  budgetMinor: "配置预算",
  balanceMinor: "实际余额",
  spendMinor: "广告消耗",
  attributedGmvMinor: "广告归因 GMV",
  paidGmvMinor: "店铺支付 GMV",
  refundMinor: "退款金额",
  settlementMinor: "店铺结算",
  stock: "库存",
  paidOrders: "支付订单",
  refundOrders: "退款订单",
  impressions: "曝光",
  clicks: "点击",
  roi: "广告 ROI",
  beans: "微信豆",
  liveViewers: "观看人数",
  itemCount: "商品数量",
};
export function PlatformWorkspace({
  pageId,
  initialProduct,
  embedded = false,
}: {
  pageId: string;
  initialProduct: ProductLine;
  embedded?: boolean;
}) {
  const workspace = useWorkspace(),
    identity = workspace.identity!,
    router = useRouter(),
    pathname = usePathname(),
    searchParams = useSearchParams();
  const [localProduct, setLocalProduct] = useState(initialProduct);
  const product = embedded ? localProduct : initialProduct;
  const api = useMemo(
    () => new PlatformService(workspace.service!, identity),
    [workspace.service, identity],
  );
  const [contextRead, setContextRead] = useState<{
      product: ProductLine;
      value: IntegrationContext | null;
      error: string;
    } | null>(null),
    [refresh, setRefresh] = useState(0);
  const context = contextRead?.product === product ? contextRead.value : null;
  const error = contextRead?.product === product ? contextRead.error : "";
  const [selection, setSelection] = useState<{
    product: ProductLine;
    entity: string;
    shop: string;
    account: string;
  }>({
    product,
    entity: searchParams.get("entityId") ?? "",
    shop: searchParams.get("productShopId") ?? "",
    account: searchParams.get("productAccountId") ?? "",
  });
  const current =
    selection.product === product
      ? selection
      : { product, entity: "", shop: "", account: "" };
  const [dates, setDates] = useState({
    start: searchParams.get("start") || todayShanghai(),
    end: searchParams.get("end") || todayShanghai(),
  });
  const [resourceSelection, setResource] = useState<{
    product: ProductLine;
    value: PlatformResource;
  }>({ product, value: resourcesForPage(pageId, product)[0] });
  const resources =
    embedded &&
    product !== "wxshop" &&
    [
      "assets",
      "videos",
      "graphics",
      "editing",
      "planning",
      "scripts",
      "experiments",
    ].includes(pageId)
      ? (["creatives", "audit", "reports"] as PlatformResource[])
      : resourcesForPage(pageId, product);
  const resource =
    resourceSelection.product === product &&
    resources.includes(resourceSelection.value)
      ? resourceSelection.value
      : resources[0];
  const [read, setRead] = useState<{
      key: string;
      data: PlatformResourcePage | null;
      error: string;
      loading: boolean;
    } | null>(null),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("");
  const [editing, setEditing] = useState<PlatformResourceRow | null>(null),
    [now, setNow] = useState(0);
  const [viewing, setViewing] = useState<PlatformResourceRow | null>(null),
    [parent, setParent] = useState<{ id: string; name: string } | null>(null);
  const scope: IntegrationScope = {
    tenantId: identity.tenantId,
    businessEntityId: current.entity,
    shopId: current.shop,
    provider: providerOf(product),
    productLine: product,
    accountId: current.account,
    ...dates,
  };
  const scopeKey = integrationScopeKey(scope),
    readKey = JSON.stringify([
      scopeKey,
      resource,
      filter,
      parent?.id ?? "",
      refresh,
    ]);
  const keyRef = useRef(readKey);
  useEffect(() => {
    keyRef.current = readKey;
  }, [readKey]);
  useEffect(() => {
    let active = true;
    void Promise.resolve().then(() => {
      if (active) setNow(Date.now());
    });
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    void api
      .context(product, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted)
          setContextRead({ product, value, error: "" });
      })
      .catch((e) => {
        if (!abort.signal.aborted)
          setContextRead({
            product,
            value: null,
            error: configurationError(e),
          });
      });
    return () => abort.abort();
  }, [api, product, refresh]);
  const block = queryBlock(
    context,
    scope,
    resource,
    identity.permissions,
    now || undefined,
  );
  useEffect(() => {
    if (!context || block) return;
    const abort = new AbortController();
    void Promise.resolve().then(async () => {
      if (abort.signal.aborted) return;
      setRead({ key: readKey, data: null, error: "", loading: true });
      try {
        const data = await api.query(
          context,
          scope,
          resource,
          { search: filter, nativeParentId: parent?.id },
          abort.signal,
        );
        if (!abort.signal.aborted)
          setRead({ key: readKey, data, error: "", loading: false });
      } catch (e) {
        if (!abort.signal.aborted)
          setRead({
            key: readKey,
            data: null,
            error: configurationError(e),
            loading: false,
          });
      }
    });
    return () => abort.abort();
    // Full scope and filter are included in readKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, context, readKey, block]);
  const currentRead = read?.key === readKey && !block ? read : null;
  function switchProduct(p: ProductLine) {
    setParent(null);
    setViewing(null);
    setEditing(null);
    setQuery("");
    setFilter("");
    if (embedded) {
      setLocalProduct(p);
      setSelection({ product: p, entity: "", shop: "", account: "" });
      return;
    }
    const q = new URLSearchParams(searchParams.toString());
    q.delete("view");
    q.delete("planId");
    q.delete("productAccountId");
    q.delete("productShopId");
    q.delete("entityId");
    q.set("accountId", "all");
    q.set("product", p);
    q.set("platform", p === "adq" || p === "beans" ? p : "all");
    router.push(`${pathname}?${q}`);
  }
  function select(patch: Partial<typeof current>) {
    setParent(null);
    setViewing(null);
    setEditing(null);
    const next = { ...current, ...patch };
    setSelection(next);
    if (!embedded) {
      const q = new URLSearchParams(searchParams.toString());
      for (const [key, value] of [
        ["entityId", next.entity],
        ["productShopId", next.shop],
        ["productAccountId", next.account],
      ]) {
        if (value) q.set(key, value);
        else q.delete(key);
      }
      router.replace(`${pathname}?${q}`);
    }
  }
  const accounts =
    context?.accounts.filter(
      (a) => !current.entity || a.businessEntityId === current.entity,
    ) ?? [];
  const shops =
    context?.shops.filter(
      (s) =>
        (!current.entity || s.businessEntityId === current.entity) &&
        (product === "wxshop" ||
          !current.account ||
          context.links.some(
            (l) => l.accountId === current.account && l.shopId === s.id,
          )),
    ) ?? [];
  const data = currentRead?.data;
  const writeOps =
    context?.operations.filter(
      (o) =>
        o.resource === resource &&
        o.kind === "WRITE" &&
        (product === "wxshop" || o.accountIds.includes(current.account)),
    ) ?? [];
  const canEdit =
    writeOps.some(
      (o) =>
        Object.values(o.evidence).every((e) => e.verified) &&
        identity.permissions.includes(o.permission),
    ) &&
    !!context &&
    Date.parse(context.expiresAt) > now &&
    !block;
  function create() {
    if (product === "wxshop") return;
    const q = new URLSearchParams(searchParams.toString());
    q.set("view", "create");
    q.set("product", product);
    q.delete("planId");
    router.push(`/app/plans?${q}`);
  }
  return (
    <div
      className="platform-workspace"
      aria-label={`${productLineName(product)}工作区`}
    >
      <div className="pi-heading">
        <div>
          <span>{embedded ? "平台数据补充" : "独立产品线工作区"}</span>
          <h2>{productLineName(product)}</h2>
          <p>
            {product === "wxshop"
              ? "商品、订单、罗盘与结算是经营数据，不创建广告计划。"
              : "每条产品线独立核验账户、接口版本与资源，不借用其他产品的数据。"}
          </p>
        </div>
        <Status>
          {context
            ? "账户目录已返回 · 权限逐项核验"
            : "后端待接入 / 授权待核验"}
        </Status>
      </div>
      <div className="pi-controls">
        {product === "wxshop" ? (
          <label>
            经营数据源
            <select aria-label="经营数据源" value="wxshop" onChange={() => {}}>
              <option value="wxshop">微信小店</option>
            </select>
          </label>
        ) : (
          <ProductScopeSelector
            value={product}
            allowAll={!embedded}
            onChange={(p) => {
              if (p === "all") {
                const q = new URLSearchParams(searchParams.toString());
                q.delete("product");
                q.set("platform", "all");
                router.push(`${pathname}?${q}`);
              } else switchProduct(p);
            }}
          />
        )}
        <label>
          经营主体
          <select
            aria-label="经营主体"
            value={current.entity}
            onChange={(e) =>
              select({ entity: e.target.value, shop: "", account: "" })
            }
          >
            <option value="">请选择真实主体</option>
            {context?.entities.map((e) => (
              <option key={e.id} value={e.id}>
                {e.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          经营店铺
          <select
            aria-label="平台经营店铺"
            value={current.shop}
            onChange={(e) => select({ shop: e.target.value })}
          >
            <option value="">请选择授权店铺</option>
            {shops.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </label>
        {product !== "wxshop" && (
          <label>
            投放账户
            <select
              aria-label="产品线投放账户"
              value={current.account}
              onChange={(e) => select({ account: e.target.value, shop: "" })}
            >
              <option value="">请选择产品线账户</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} · {a.authorization}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>
          开始日期
          <input
            aria-label="平台数据开始日期"
            type="date"
            value={dates.start}
            max={dates.end}
            onInput={(e) =>
              setDates({ ...dates, start: e.currentTarget.value })
            }
          />
        </label>
        <label>
          结束日期
          <input
            aria-label="平台数据结束日期"
            type="date"
            value={dates.end}
            min={dates.start}
            onInput={(e) => setDates({ ...dates, end: e.currentTarget.value })}
          />
        </label>
        <button
          className="button secondary"
          onClick={() => {
            setContextRead(null);
            setRefresh((n) => n + 1);
          }}
        >
          <ArrowClockwise size={15} />
          重新读取
        </button>
      </div>
      <div className="pi-actions">
        {product !== "wxshop" && (
          <button className="button primary" onClick={create}>
            <Plus size={15} />
            {advertisingProducts[product].writeEnabled
              ? "新建配置与预览"
              : "配置本地草稿 · 创建待核验"}
          </button>
        )}
        {embedded && (
          <select
            aria-label="切换平台数据工作区"
            value={product}
            onChange={(e) => switchProduct(e.target.value as ProductLine)}
          >
            <optgroup label="经营数据源">
              <option value="wxshop">微信小店经营数据中心</option>
            </optgroup>
            <optgroup label="广告产品线">
              {Object.entries(advertisingProducts).map(([id, p]) => (
                <option key={id} value={id}>
                  {p.name}
                </option>
              ))}
            </optgroup>
          </select>
        )}
        {!embedded && (
          <button
            className="button secondary"
            onClick={() =>
              switchProduct(product === "wxshop" ? "adq" : "wxshop")
            }
          >
            {product === "wxshop" ? "返回腾讯营销投放" : "微信小店经营数据中心"}
          </button>
        )}
        <span>
          {identity.mode === "MOCK"
            ? "演示隔离 · 此处没有模拟余额或原生标签"
            : "仅使用已授权后端响应"}
        </span>
      </div>
      {(error || block) && (
        <div className="pi-notice" role="status">
          {error || block}
          {error && block && ` ${block}`}
        </div>
      )}
      <div className="pi-tabs" role="tablist" aria-label="产品线业务资源">
        {resources.map((r) => (
          <button
            key={r}
            role="tab"
            aria-selected={resource === r}
            onClick={() => {
              setResource({ product, value: r });
              setParent(null);
              setViewing(null);
              setFilter("");
              setQuery("");
              setEditing(null);
            }}
          >
            {resourceNames[r]}
          </button>
        ))}
      </div>
      <div className="pi-resource">
        {parent && (
          <div className="pi-notice">
            关联原生对象：{parent.name} [{parent.id}]{" "}
            <button className="dw-link" onClick={() => setParent(null)}>
              清除关联筛选
            </button>
          </div>
        )}
        <div className="pi-title">
          <h3>{resourceNames[resource]}</h3>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setFilter(query);
              setRefresh((n) => n + 1);
            }}
          >
            <input
              aria-label="搜索平台业务对象"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="名称或原始 ID"
            />
            <button className="button secondary" disabled={!!block}>
              查询
            </button>
          </form>
        </div>
        {currentRead?.error ? (
          <p role="alert" className="pi-notice">
            {currentRead.error}
          </p>
        ) : currentRead?.loading ? (
          <div className="pi-empty">正在读取此主体、店铺和账户的真实数据…</div>
        ) : !data?.rows.length ? (
          <div className="pi-empty">
            {block || "接口未返回当前范围的资源。"}
            <small>不会由其他产品线的样例、额度或经营数据填充。</small>
          </div>
        ) : (
          <>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>对象 / 原始 ID</th>
                    <th>平台状态</th>
                    <th>指标与口径</th>
                    <th>更新时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <button
                          className="dw-link"
                          onClick={() => setViewing(row)}
                        >
                          {row.name}
                        </button>
                        <small>{row.nativeId}</small>
                      </td>
                      <td>
                        {row.status}
                        <small>版本 {row.version ?? "未提供"}</small>
                      </td>
                      <td>
                        {Object.entries(row.metrics).map(([key, value]) => (
                          <div key={key}>
                            {metricNames[key] || key}：
                            {value === null
                              ? "未提供"
                              : key.endsWith("Minor")
                                ? money(value)
                                : value?.toLocaleString()}
                          </div>
                        ))}
                        <small>
                          {row.moneyType === "MERCHANT_SETTLEMENT"
                            ? "店铺结算资金，不能用于广告余额计算"
                            : row.moneyType === "AD_BUDGET"
                              ? "配置额度，不等同实际余额"
                              : (row.attributionWindow ?? "归因口径未提供")}
                        </small>
                      </td>
                      <td>{row.updatedAt}</td>
                      <td>
                        <button
                          className="dw-link"
                          disabled={!canEdit || row.version === null}
                          onClick={() => setEditing(row)}
                        >
                          调整配置
                        </button>
                        {(resource === "campaigns" ||
                          resource === "adgroups") && (
                          <button
                            className="dw-link"
                            onClick={() => {
                              setParent({ id: row.nativeId, name: row.name });
                              setResource({
                                product,
                                value:
                                  resource === "campaigns"
                                    ? "adgroups"
                                    : "creatives",
                              });
                            }}
                          >
                            查看{resource === "campaigns" ? "单元" : "创意"}
                          </button>
                        )}
                        {row.related.map((link) => (
                          <Link
                            key={`${link.type}:${link.id}`}
                            className="dw-link"
                            href={`/app/${link.type === "PRODUCT" ? "products" : link.type === "LIVE" ? "monitor" : "plans"}?${new URLSearchParams({ shopId: current.shop, [link.type === "LIVE" ? "liveSessionId" : link.type === "PRODUCT" ? "productId" : "planId"]: link.id, ...(link.type === "PLAN" ? { product, platform: product === "adq" || product === "beans" ? product : "all" } : {}) })}`}
                          >
                            关联
                            {link.type === "PRODUCT"
                              ? "商品"
                              : link.type === "LIVE"
                                ? "场次"
                                : "投放"}
                          </Link>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {data.nextCursor && (
              <button
                className="button secondary"
                onClick={async () => {
                  if (!context) return;
                  try {
                    const next = await api.query(context, scope, resource, {
                      search: filter,
                      cursor: data.nextCursor!,
                      nativeParentId: parent?.id,
                    });
                    if (keyRef.current === readKey)
                      setRead({
                        key: readKey,
                        data: {
                          ...next,
                          rows: [
                            ...data.rows,
                            ...next.rows.filter(
                              (r) => !data.rows.some((p) => p.id === r.id),
                            ),
                          ],
                        },
                        error: "",
                        loading: false,
                      });
                  } catch (e) {
                    if (keyRef.current === readKey)
                      setRead({
                        key: readKey,
                        data,
                        error: configurationError(e),
                        loading: false,
                      });
                  }
                }}
              >
                加载更多
              </button>
            )}
          </>
        )}
        {data && (
          <div className="pi-notice">
            同步 {data.syncedAt} · {data.timezone} · 粒度 {data.granularity} ·
            归因 {data.attributionWindow || "未提供"}
            <br />
            退款：{data.refundHandling}；跨渠道去重：{data.deduplication}
            {data.warnings.map((warning, i) => (
              <p key={i}>{warning}</p>
            ))}
          </div>
        )}
        {product === "wxshop" && (
          <p className="pi-notice">
            经营支付 GMV、退款、结算与广告归因
            GMV分别展示。订单面板不接收收货人、电话、地址或银行卡数据，也不自动送入
            AI 上下文。优选联盟推广计划不作为广告计划。
          </p>
        )}
      </div>
      <CatalogBrowser
        key={product}
        product={product}
        context={context}
        accountId={current.account}
      />
      {viewing && (
        <Modal title="平台对象详情" close={() => setViewing(null)}>
          <dl className="pi-facts">
            {[
              ["名称", viewing.name],
              ["平台原生 ID", viewing.nativeId],
              ["平台状态", viewing.status],
              ["资源版本", viewing.version ?? "未提供"],
              ["更新时间", viewing.updatedAt],
              ["归因窗口", viewing.attributionWindow ?? "未提供"],
              ["资金类型", viewing.moneyType ?? "不适用"],
            ].map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          {Object.entries(viewing.metrics).map(([key, value]) => (
            <p key={key}>
              {metricNames[key]}：
              {value === null
                ? "未提供"
                : key.endsWith("Minor")
                  ? money(value)
                  : value}
            </p>
          ))}
          <p className="pi-notice">
            当前企业、主体、店铺、产品线与账户范围内的后端回读数据。缺失值不补零；订单个人信息不在此面板展示。
          </p>
        </Modal>
      )}
      {editing && context && (
        <PlatformEdit
          key={`${scopeKey}:${editing.id}`}
          api={api}
          context={context}
          scope={scope}
          row={editing}
          operations={writeOps}
          close={() => setEditing(null)}
          onChanged={() => setRefresh((n) => n + 1)}
          owner={`${identity.mode}:${identity.tenantId}:${identity.userId}`}
        />
      )}
    </div>
  );
}

function PlatformEdit({
  api,
  context,
  scope,
  row,
  operations,
  close,
  onChanged,
  owner,
}: {
  api: PlatformService;
  context: IntegrationContext;
  scope: IntegrationScope;
  row: PlatformResourceRow;
  operations: IntegrationContext["operations"];
  close: () => void;
  onChanged: () => void;
  owner: string;
}) {
  const allowed = operations.filter((o) =>
      Object.values(o.evidence).every((e) => e.verified),
    ),
    [opId, setOpId] = useState(allowed[0]?.catalogId ?? ""),
    [values, setValues] = useState<PlatformChange["changes"]>({}),
    [reason, setReason] = useState(""),
    [preview, setPreview] = useState<PlatformChangePreview | null>(null),
    [result, setResult] = useState<PlatformCommandResult | null>(null),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false),
    [ack, setAck] = useState(false),
    [pending, setPending] = useState("");
  const op = allowed.find((o) => o.catalogId === opId),
    lock = useRef(false),
    mounted = useRef(true);
  const localKey = `yuliang:platform-change:v1:${encodeURIComponent(owner)}:${encodeURIComponent(integrationScopeKey(scope))}:${row.id}`;
  useEffect(() => {
    let active = true;
    mounted.current = true;
    void Promise.resolve().then(() => {
      if (!active) return;
      try {
        const saved = JSON.parse(localStorage.getItem(localKey) || "null");
        if (saved && typeof saved.pending === "string")
          setPending(saved.pending);
        if (saved && typeof saved.reason === "string") setReason(saved.reason);
        if (saved?.values && typeof saved.values === "object")
          setValues(
            Object.fromEntries(
              Object.entries(saved.values).filter(
                ([key, value]) =>
                  op?.editableFields.includes(key as keyof typeof values) &&
                  typeof value === "string",
              ),
            ),
          );
      } catch {
        setMessage("无法恢复本地操作草稿，请重新核对。");
      }
    });
    return () => {
      active = false;
      mounted.current = false;
    };
    // Only the scoped local key determines restoration; grants are never restored.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localKey]);
  function save(v = values, r = reason, key = pending) {
    try {
      localStorage.setItem(
        localKey,
        JSON.stringify({ values: v, reason: r, pending: key }),
      );
      return true;
    } catch {
      setMessage("本地草稿无法保存，禁止提交以免丢失幂等记录。");
      return false;
    }
  }
  const change: PlatformChange = {
    catalogId: opId,
    targetId: row.id,
    nativeId: row.nativeId,
    expectedVersion: row.version!,
    changes: values,
    reason,
  };
  async function work(kind: "preview" | "submit" | "query" | "cancel") {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setMessage("");
    try {
      if (kind === "preview") {
        const value = await api.preview(context, scope, change);
        if (mounted.current) setPreview(value);
      } else if (kind === "submit" && preview && ack && !pending) {
        const key = crypto.randomUUID();
        if (!save(values, reason, key)) return;
        setPending(key);
        const value = await api.command(context, scope, change, preview, key);
        if (mounted.current) {
          setResult(value);
          onChanged();
        }
      } else if (kind === "query" && pending) {
        const value = await api.result(scope, pending);
        if (mounted.current) setResult(value);
      } else if (kind === "cancel" && result?.cancellable) {
        const key = crypto.randomUUID();
        if (!save(values, reason, key)) return;
        setPending(key);
        const value = await api.cancel(scope, result, key);
        if (mounted.current) {
          setResult(value);
          onChanged();
        }
      }
    } catch (e) {
      if (mounted.current)
        setMessage(
          `${configurationError(e)}${kind !== "preview" ? " 保留原幂等键，请查询结果，系统不会自动重发。" : ""}`,
        );
    } finally {
      lock.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <Modal
      wide
      title="平台变更预览与核验"
      close={() => {
        if (!busy) {
          save();
          close();
        }
      }}
    >
      <p>
        {row.name} · {row.nativeId} · 原版本 {row.version}
      </p>
      <label>
        已核验操作
        <select
          aria-label="平台调整操作"
          value={opId}
          disabled={busy || !!pending}
          onChange={(e) => {
            setOpId(e.target.value);
            setValues({});
            setPreview(null);
            setAck(false);
          }}
        >
          {allowed.map((o) => (
            <option key={o.catalogId} value={o.catalogId}>
              {productCatalog(scope.productLine).find(
                (r) => r.id === o.catalogId,
              )?.name || o.catalogId}
            </option>
          ))}
        </select>
      </label>
      <div className="pi-edit-fields">
        {op?.editableFields.map((field) => (
          <label key={field}>
            {
              {
                name: "名称",
                budget: "预算（元）",
                bid: "出价（单位由预检确认）",
                status: "目标状态",
              }[field]
            }
            {field === "status" ? (
              <select
                aria-label="目标平台状态"
                disabled={busy || !!pending}
                value={values[field] ?? ""}
                onChange={(e) => {
                  const next = { ...values, [field]: e.target.value };
                  setValues(next);
                  setPreview(null);
                  setAck(false);
                  save(next);
                }}
              >
                <option value="">请选择后端允许的状态</option>
                {op.options
                  ?.filter((o) => o.field === "status")
                  .map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
              </select>
            ) : (
              <input
                aria-label={`平台调整${field}`}
                disabled={busy || !!pending}
                value={values[field] ?? ""}
                onChange={(e) => {
                  const next = { ...values, [field]: e.target.value };
                  setValues(next);
                  setPreview(null);
                  setAck(false);
                  save(next);
                }}
              />
            )}
          </label>
        ))}
      </div>
      <label>
        变更原因
        <textarea
          aria-label="平台变更原因"
          disabled={busy || !!pending}
          value={reason}
          onChange={(e) => {
            setReason(e.target.value);
            setPreview(null);
            setAck(false);
            save(values, e.target.value);
          }}
        />
      </label>
      <button
        className="button secondary"
        disabled={
          busy || !!pending || !Object.keys(values).length || !reason.trim()
        }
        onClick={() => void work("preview")}
      >
        服务端预检并展示差异
      </button>
      {preview && (
        <>
          <div className="pi-change-diff">
            {op?.editableFields.map((field) => (
              <div key={field}>
                <b>{field}</b>
                <span>{String(preview.before[field] ?? "未提供")}</span>
                <span>→</span>
                <strong>{String(preview.after[field] ?? "未提供")}</strong>
              </div>
            ))}
          </div>
          {preview.checks.map((c, i) => (
            <p key={i}>
              {c.passed ? "✓" : "!"} {c.message}
            </p>
          ))}
          <p>
            {preview.requiresApproval
              ? "提交后需要审批，尚未执行。"
              : "以平台受理和回读为准。"}
          </p>
          <label className="pi-ack">
            <input
              type="checkbox"
              checked={ack}
              onChange={(e) => setAck(e.target.checked)}
            />
            确认原值、目标值、账户、预算和风险
          </label>
          <button
            className="button primary"
            disabled={
              busy || !!pending || !ack || preview.checks.some((c) => !c.passed)
            }
            onClick={() => void work("submit")}
          >
            提交平台变更申请
          </button>
        </>
      )}
      {pending && (
        <div className="pi-notice">
          幂等键 {pending} · 不自动重发
          <button
            className="button secondary"
            disabled={busy}
            onClick={() => void work("query")}
          >
            查询原申请结果
          </button>
        </div>
      )}
      {result && (
        <div className="pi-notice">
          <p>
            任务 {result.taskId} · {result.state} · 回读 {result.verification}
          </p>
          <p>
            平台业务 ID：{result.businessId || "未提供"} · {result.message}
          </p>
          {result.cancellable && (
            <button
              className="button secondary"
              disabled={busy}
              onClick={() => void work("cancel")}
            >
              撤回未执行申请
            </button>
          )}
        </div>
      )}
      {message && <p role="alert">{message}</p>}
    </Modal>
  );
}
