"use client";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { MagnifyingGlass, X, ArrowClockwise } from "@phosphor-icons/react";
import {
  canEditField,
  configurationContextKey,
  fieldStateNames,
  type ConfigurationContext,
  type FieldCapability,
  type LookupKind,
  type LookupPage,
  type SelectedResource,
} from "@/lib/advertising";
import {
  AdvertisingService,
  configurationError,
} from "@/services/advertising-service";

export function Field({
  label,
  cap,
  children,
}: {
  label: string;
  cap: FieldCapability;
  children: ReactNode;
}) {
  return (
    <div className="aw-field">
      <div className="aw-field-label">
        <strong>
          {label}
          {cap.required && " *"}
        </strong>
        <span
          className={`aw-evidence ${cap.state.toLowerCase()}`}
          title={cap.reason}
        >
          {fieldStateNames[cap.state]}
        </span>
      </div>
      {children}
      <small>{cap.reason}</small>
    </div>
  );
}
export function Choice({
  label,
  cap,
  value,
  onChange,
}: {
  label: string;
  cap: FieldCapability;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <Field label={label} cap={cap}>
      <select
        aria-label={label}
        value={value}
        disabled={!canEditField(cap)}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">
          {cap.state === "AVAILABLE" ? "请选择" : "等待账户能力与授权"}
        </option>
        {cap.options?.map((o) => (
          <option key={o.id} value={o.id} disabled={!o.available}>
            {o.name}
            {!o.available ? ` · ${o.reason ?? "当前不可用"}` : ""}
          </option>
        ))}
      </select>
    </Field>
  );
}
export function ResourcePicker({
  label,
  kind,
  context,
  api,
  cap,
  selected,
  onChange,
  excluded = [],
  single = false,
  hierarchical = false,
}: {
  label: string;
  kind: LookupKind;
  context: ConfigurationContext;
  api: AdvertisingService;
  cap: FieldCapability;
  selected: SelectedResource[];
  onChange: (items: SelectedResource[]) => void;
  excluded?: string[];
  single?: boolean;
  hierarchical?: boolean;
}) {
  const [query, setQuery] = useState(""),
    [category, setCategory] = useState(""),
    [path, setPath] = useState<{ id: string; name: string }[]>([]),
    [reload, setReload] = useState(0);
  const [read, setRead] = useState<{
    key: string;
    page: LookupPage | null;
    error: string;
    loading: boolean;
  } | null>(null);
  const [more, setMore] = useState(false);
  const contextKey = configurationContextKey(context);
  const parent = path.at(-1)?.id ?? "";
  const key = JSON.stringify([contextKey, kind, query, parent, reload]);
  const activeKey = useRef(key);
  useEffect(() => {
    activeKey.current = key;
  }, [key]);
  const enabled = cap.state === "AVAILABLE" && !!context.accountId;
  useEffect(() => {
    const abort = new AbortController();
    void Promise.resolve().then(async () => {
      if (abort.signal.aborted || !enabled) return;
      setRead({ key, page: null, error: "", loading: true });
      try {
        const page = await api.lookup(
          context,
          kind,
          { q: query, parentId: parent },
          abort.signal,
        );
        if (!abort.signal.aborted)
          setRead({ key, page, error: "", loading: false });
      } catch (error) {
        if (!abort.signal.aborted)
          setRead({
            key,
            page: null,
            error: configurationError(error),
            loading: false,
          });
      }
    });
    return () => abort.abort();
    // Context is represented by its stable, complete key.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, key, enabled]);
  const current = read?.key === key ? read : null;
  const categories = [
    ...new Set(
      current?.page?.items
        .map((r) => r.category)
        .filter((v): v is string => !!v),
    ),
  ];
  const items =
    current?.page?.items.filter((r) => !category || r.category === category) ??
    [];
  async function loadMore() {
    if (!current?.page?.nextCursor || more) return;
    setMore(true);
    try {
      const page = await api.lookup(context, kind, {
        q: query,
        parentId: parent,
        cursor: current.page.nextCursor,
      });
      if (activeKey.current === key)
        setRead({
          key,
          page: {
            ...page,
            items: [
              ...current.page.items,
              ...page.items.filter(
                (r) => !current.page!.items.some((p) => p.id === r.id),
              ),
            ],
          },
          error: "",
          loading: false,
        });
    } catch (error) {
      if (activeKey.current === key)
        setRead({ ...current, error: configurationError(error) });
    } finally {
      setMore(false);
    }
  }
  return (
    <Field label={label} cap={cap}>
      <div className="aw-resource-tools">
        <label className="aw-search">
          <MagnifyingGlass size={15} />
          <input
            aria-label={`搜索${label}`}
            placeholder="从真实接口搜索"
            value={query}
            disabled={!enabled}
            onChange={(e) => {
              setQuery(e.target.value);
              setCategory("");
            }}
          />
        </label>
        {categories.length > 0 && (
          <select
            aria-label={`${label}分类`}
            value={category}
            onChange={(e) => setCategory(e.target.value)}
          >
            <option value="">全部分类</option>
            {categories.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        )}
        <button
          className="button secondary compact"
          aria-label={`刷新${label}`}
          disabled={!enabled}
          onClick={() => setReload((n) => n + 1)}
        >
          <ArrowClockwise size={14} />
        </button>
      </div>
      {hierarchical && (
        <div className="aw-location-path">
          <button type="button" onClick={() => setPath([])} disabled={!enabled}>
            全部地区
          </button>
          {path.map((p, i) => (
            <button key={p.id} onClick={() => setPath(path.slice(0, i + 1))}>
              {p.name}
            </button>
          ))}
        </div>
      )}
      <div className="aw-resource-results" aria-live="polite">
        {!enabled ? (
          <p>尚无可用的账户数据。{cap.reason}</p>
        ) : !current || current.loading ? (
          <p>正在加载真实数据…</p>
        ) : current.error ? (
          <p role="alert">{current.error}</p>
        ) : !items.length ? (
          <p>
            {current.page?.reason || "接口未返回可选资源。调整搜索或检查授权。"}
          </p>
        ) : (
          items.map((item) => {
            const checked = selected.some((s) => s.id === item.id),
              conflict = excluded.includes(item.id),
              limited =
                !checked &&
                cap.maxSelected !== undefined &&
                selected.length >= cap.maxSelected;
            return (
              <div className="aw-resource-row" key={item.id}>
                <label>
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={!item.eligible || conflict || limited}
                    onChange={() =>
                      onChange(
                        checked
                          ? selected.filter((s) => s.id !== item.id)
                          : single
                            ? [item]
                            : [...selected, item],
                      )
                    }
                  />
                  <span>
                    <b>{item.name}</b>
                    <small>
                      {item.platformId} {item.status && `· ${item.status}`}{" "}
                      {item.category && `· ${item.category}`}
                    </small>
                    {(!item.eligible || conflict) && (
                      <small>
                        {conflict
                          ? "已在另一组选择，不能同时包含和排除"
                          : (item.reason ?? "此资源当前不可用")}
                      </small>
                    )}
                    {item.specs?.length && (
                      <small>{item.specs.join(" · ")}</small>
                    )}
                  </span>
                </label>
                {hierarchical && (
                  <button
                    className="dw-link"
                    onClick={() =>
                      setPath([...path, { id: item.id, name: item.name }])
                    }
                  >
                    下级地区
                  </button>
                )}
              </div>
            );
          })
        )}
        {current?.page?.nextCursor && (
          <button
            className="dw-link"
            disabled={more}
            onClick={() => void loadMore()}
          >
            {more ? "加载中…" : "加载更多"}
          </button>
        )}
      </div>
      <div className="aw-selected" aria-label={`已选${label}`}>
        {selected.map((item) => (
          <span key={item.id}>
            {item.name}
            <small>{item.platformId}</small>
            <button
              aria-label={`移除${item.name}`}
              onClick={() => onChange(selected.filter((s) => s.id !== item.id))}
            >
              <X size={12} />
            </button>
          </span>
        ))}
      </div>
    </Field>
  );
}
