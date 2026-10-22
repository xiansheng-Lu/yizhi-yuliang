"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useWorkspace } from "@/components/provider";
import {
  adaptDeliverySnapshot,
  DeliveryService,
  retainDeliveryFailure,
} from "@/services/delivery-service";
import { ServiceError } from "@/services/contracts";
import {
  actionAvailability,
  deliveryHref,
  LatestDeliveryRead,
  parseScope,
  scopeKey,
  type Action,
  type DeliveryData,
  type DeliveryScope,
  type PlanFilter,
} from "@/lib/delivery";
import type { Identity } from "@/lib/domain";
import { Modal } from "./ui";
import { useDeliverySession } from "./session";

interface DeliveryContextValue {
  data: DeliveryData;
  scope: DeliveryScope;
  identity: Identity;
  api: DeliveryService;
  loading: boolean;
  error: string;
  message: string;
  busy: boolean;
  uncertain: boolean;
  query: URLSearchParams;
  refresh: () => void;
  changeScope: (patch: Partial<DeliveryScope>) => void;
  href: (
    page: string,
    ids?: Record<string, string | null | undefined>,
  ) => string;
  navigate: (url: string) => void;
  guard: (action: () => void) => void;
  dirty: boolean;
  markDirty: (value: boolean) => void;
  draft: unknown;
  saveDraft: (value: unknown) => void;
  mutate: <T>(
    accountId: string,
    action: Action,
    work: () => Promise<T>,
    onDone?: (value: T) => void,
  ) => Promise<void>;
  feedback: (message: string) => void;
  filters: Record<string, PlanFilter>;
  setFilter: (platform: string, filter: PlanFilter) => void;
}
const Context = createContext<DeliveryContextValue | null>(null);
export function useDelivery() {
  const value = useContext(Context);
  if (!value) throw new Error("Delivery context missing");
  return value;
}
export function DeliveryProvider({ children }: { children: ReactNode }) {
  const workspace = useWorkspace();
  const identity = workspace.identity!;
  const router = useRouter(),
    pathname = usePathname(),
    search = useSearchParams();
  const [switching, startTransition] = useTransition();
  const snapshot =
    workspace.data.status === "ready"
      ? workspace.data.data
      : workspace.lastValidSnapshot;
  const scope = useMemo(
    () =>
      parseScope(new URLSearchParams(search.toString()), {
        shopId: snapshot?.scope.shop_id,
        start: snapshot?.scope.date,
      }),
    [search, snapshot?.scope.shop_id, snapshot?.scope.date],
  );
  const key = `${identity.mode}:${identity.tenantId}:${identity.userId}:${scopeKey(scope)}`;
  const api = useMemo(
    () => new DeliveryService(workspace.service!),
    [workspace.service],
  );
  const fallback = useMemo(
    () => adaptDeliverySnapshot(snapshot, identity, scope),
    [snapshot, identity, scope],
  );
  const [read, setRead] = useState<{
    key: string;
    data: DeliveryData;
    error: string;
    loading: boolean;
  } | null>(null);
  const reader = useRef(new LatestDeliveryRead());
  const latest = useRef({ scope, snapshot, identity, key, api });
  useEffect(() => {
    latest.current = { scope, snapshot, identity, key, api };
  }, [scope, snapshot, identity, key, api]);
  const [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const lock = useRef(false);
  const memory = useDeliverySession(
    `${identity.mode}:${identity.tenantId}:${identity.userId}`,
  );
  const { uncertainKeys, drafts, filters } = memory;
  const setUncertainKeys = (change: (previous: string[]) => string[]) =>
    memory.update((previous) => ({
      ...previous,
      uncertainKeys: change(previous.uncertainKeys),
    }));
  const [dirty, markDirty] = useState(false),
    [pending, setPending] = useState<(() => void) | null>(null);
  const draft = drafts[scopeKey(scope)] ?? null;
  const setDraft = (value: unknown) =>
    memory.update((previous) => ({
      ...previous,
      drafts: { ...previous.drafts, [scopeKey(scope)]: value },
    }));
  const guard = useCallback(
    (action: () => void) => {
      if (lock.current) {
        setMessage("操作正在处理中，请等待平台响应后再切换。");
        return;
      }
      if (dirty) setPending(() => action);
      else action();
    },
    [dirty],
  );
  const refresh = useCallback(() => {
    const current = latest.current;
    setRead((previous) =>
      previous?.key === current.key
        ? { ...previous, loading: true, error: "" }
        : null,
    );
    void reader.current.run(
      (signal) =>
        current.api.load(
          current.scope,
          current.snapshot,
          current.identity,
          signal,
        ),
      (data) => setRead({ key: current.key, data, error: "", loading: false }),
      (error) =>
        setRead((previous) => ({
          key: current.key,
          data: retainDeliveryFailure(
            previous?.key === current.key
              ? previous.data
              : adaptDeliverySnapshot(
                  current.snapshot,
                  current.identity,
                  current.scope,
                ),
            error,
          ),
          error: error instanceof Error ? error.message : "投放数据读取失败",
          loading: false,
        })),
    );
  }, []);
  useEffect(() => {
    const currentReader = reader.current;
    // Schedule through the transport boundary; avoid publishing prior scope data.
    void Promise.resolve().then(refresh);
    return () => currentReader.cancel();
  }, [key, api, snapshot, refresh]);
  const data = read?.key === key ? read.data : fallback;
  const error = read?.key === key ? read.error : "";
  const loading = switching || !read || read.key !== key || read.loading;
  useEffect(() => {
    const refreshEvent = () => refresh();
    window.addEventListener("yuliang:refresh-delivery", refreshEvent);
    return () =>
      window.removeEventListener("yuliang:refresh-delivery", refreshEvent);
  }, [refresh]);
  useEffect(() => {
    const seconds = Math.min(
      ...data.capabilities.map((c) =>
        c.refreshSeconds && c.refreshSeconds > 0
          ? Math.max(30, c.refreshSeconds)
          : Infinity,
      ),
    );
    if (!Number.isFinite(seconds)) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible" && !lock.current) refresh();
    }, seconds * 1000);
    return () => clearInterval(timer);
  }, [data.capabilities, refresh]);
  useEffect(() => {
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty || lock.current) event.preventDefault();
    };
    const intercept = (event: MouseEvent) => {
      const anchor = (event.target as Element)?.closest<HTMLAnchorElement>(
        "a[href]",
      );
      if (
        !anchor ||
        anchor.target === "_blank" ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        anchor.hasAttribute("download") ||
        anchor.href === location.href ||
        (!dirty && !lock.current)
      )
        return;
      event.preventDefault();
      event.stopPropagation();
      guard(() => router.push(anchor.href));
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", intercept, true);
    return () => {
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", intercept, true);
    };
  }, [dirty, guard, router]);
  const navigate = (url: string) =>
    guard(() => startTransition(() => router.push(url)));
  const changeScope = (patch: Partial<DeliveryScope>) => {
    const next = { ...scope, ...patch };
    if (
      (patch.platform !== undefined || patch.shopId !== undefined) &&
      patch.accountId === undefined
    )
      next.accountId = "all";
    if (patch.shopId !== undefined) delete next.liveSessionId;
    navigate(deliveryHref(pathname.split("/").pop()!, next));
  };
  async function mutate<T>(
    accountId: string,
    action: Action,
    work: () => Promise<T>,
    onDone?: (value: T) => void,
  ) {
    const accountKey = `${identity.tenantId}:${identity.userId}:${accountId}`;
    if (lock.current) return;
    if (
      uncertainKeys.includes(accountKey) &&
      !["verify", "analyze"].includes(action)
    ) {
      setMessage(
        "此账户存在结果未知的申请，请先在执行记录核查，不可重复提交。",
      );
      return;
    }
    const rule = actionAvailability(data, identity, accountId, action);
    if (loading || rule.status !== "AUTHORIZED") {
      setMessage(loading ? "正在更新账户能力，请稍后再试" : rule.reason);
      return;
    }
    lock.current = true;
    setBusy(true);
    setMessage("");
    try {
      const value = await work();
      markDirty(false);
      setMessage("后端已返回申请结果，请以审批和回读核验状态为准。");
      onDone?.(value);
      // Invalidate all shared views; no local optimistic platform success.
      refresh();
      void workspace.reload();
      window.dispatchEvent(new Event("yuliang:delivery-changed"));
    } catch (e) {
      const unknown =
        e instanceof ServiceError &&
        ["TIMEOUT", "NETWORK_ERROR", "INVALID_RESPONSE"].includes(e.code);
      const writeUnknown = unknown && !["verify", "analyze"].includes(action);
      if (writeUnknown) setUncertainKeys((values) => [...values, accountKey]);
      setMessage(
        writeUnknown
          ? "提交结果未知。已锁定此账户在本次会话中的重复写入；请在执行记录核查原任务与幂等键。结果明确后可重新载入页面并同步，系统不会自动重放申请。"
          : unknown ? "分析或核验请求结果未知，请同步任务状态后核查；未重复提交任何平台修改。"
          : e instanceof ServiceError && e.code === "CONFLICT"
            ? "配置已被其他人修改，请刷新并重新预览，原申请未被自动重放。"
            : e instanceof Error
              ? e.message
              : "操作未完成",
      );
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  return (
    <Context.Provider
      value={{
        data,
        scope,
        identity,
        api,
        loading,
        error,
        message,
        busy,
        uncertain: uncertainKeys.includes(
          `${identity.tenantId}:${identity.userId}:${scope.accountId}`,
        ),
        query: new URLSearchParams(search.toString()),
        refresh,
        changeScope,
        href: (page, ids) => deliveryHref(page, scope, ids),
        navigate,
        guard,
        dirty,
        markDirty,
        draft,
        saveDraft: setDraft,
        mutate,
        feedback: setMessage,
        filters,
        setFilter: (platform, filter) =>
          memory.update((previous) => ({
            ...previous,
            filters: { ...previous.filters, [platform]: filter },
          })),
      }}
    >
      {children}
      {pending && (
        <Modal title="有尚未保存的编辑" close={() => setPending(null)}>
          <p>
            切换会离开当前编辑。可将配置保存在本次会话的草稿中；草稿不会提交到平台，关闭页面后不保留。
          </p>
          <div className="dw-actions">
            <button
              className="button secondary"
              onClick={() => setPending(null)}
            >
              取消切换
            </button>
            <button
              className="button secondary"
              onClick={() => {
                markDirty(false);
                setDraft(null);
                pending();
                setPending(null);
              }}
            >
              放弃并切换
            </button>
            <button
              className="button primary"
              onClick={() => {
                markDirty(false);
                pending();
                setPending(null);
                setMessage("已保留本次会话配置草稿，尚未提交后端。");
              }}
            >
              保留草稿并切换
            </button>
          </div>
        </Modal>
      )}
    </Context.Provider>
  );
}
