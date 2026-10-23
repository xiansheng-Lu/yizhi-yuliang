"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type Dispatch,
  type SetStateAction,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import type { FrontendSnapshot, Identity } from "@/lib/domain";
import { createFrontendService } from "@/services";
import { readSnapshot } from "@/services/snapshot-reader";
import {
  ServiceError,
  type FrontendService,
  type Resource,
} from "@/services/contracts";
import { ErrorState, LoadingState } from "./ui";

interface WorkspaceContextValue {
  service: FrontendService | null;
  identity: Identity | null;
  ready: boolean;
  data: Resource<FrontendSnapshot>;
  lastValidSnapshot: FrontendSnapshot | null;
  reload: () => Promise<Identity | null>;
  logout: () => Promise<void>;
  sidebarCollapsed: boolean;
  setSidebarCollapsed: Dispatch<SetStateAction<boolean>>;
}
const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);
export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const service = useRef<FrontendService | null>(null);
  const [client, setClient] = useState<FrontendService | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null),
    [ready, setReady] = useState(false);
  const [data, setData] = useState<Resource<FrontendSnapshot>>({
    status: "loading",
  });
  const [lastValidSnapshot, setLastValidSnapshot] = useState<FrontendSnapshot | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const generation = useRef(0);
  const owner = useRef<string | null>(null);
  const reload = useCallback(async () => {
    const request = ++generation.current;
    let actor: Identity | null = null;
    try {
      service.current ??= createFrontendService();
      setClient(service.current);
      actor = await service.current.getIdentity();
      if (request !== generation.current) return null;
      setIdentity(actor);
      const nextOwner = actor ? `${actor.mode}:${actor.tenantId}:${actor.userId}` : null;
      if (nextOwner !== owner.current) { setData({ status: "loading" }); setLastValidSnapshot(null); }
      owner.current = nextOwner;
      if (actor) {
        const snapshot = await readSnapshot(service.current);
        if (request === generation.current) {
          setData({ status: "ready", data: snapshot });
          setLastValidSnapshot(snapshot);
        }
      } else setData({ status: "loading" });
    } catch (error) {
      if (request === generation.current) {
        if (error instanceof ServiceError && error.code === "UNAUTHENTICATED") {
          actor = null;
          setIdentity(null);
          setLastValidSnapshot(null);
          setData({ status: "loading" });
        } else {
          setData({
            status: "error",
            error: error instanceof ServiceError ? error : new ServiceError("NETWORK_ERROR", "无法读取前端服务，请重试。"),
          });
        }
      }
    } finally {
      if (request === generation.current) setReady(true);
    }
    return request === generation.current ? actor : null;
  }, []);
  useEffect(() => {
    void reload();
    const unsubscribe = service.current?.subscribe(() => {
      void reload();
    });
    return () => {
      unsubscribe?.();
    };
  }, [reload]);
  async function logout() {
    await service.current?.logout();
    ++generation.current;
    setIdentity(null);
    setLastValidSnapshot(null);
    setData({ status: "loading" });
  }
  return (
    <WorkspaceContext.Provider
      value={{ service: client, identity, ready, data, lastValidSnapshot, reload, logout, sidebarCollapsed, setSidebarCollapsed }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}
export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("WorkspaceProvider missing");
  return context;
}
export function AuthGuard({ children, requireSnapshot = true, loading }: { children: ReactNode; requireSnapshot?: boolean; loading?: ReactNode }) {
  const { ready, identity, data, reload, service } = useWorkspace();
  const pathname = usePathname(),
    router = useRouter();
  useEffect(() => {
    if (ready && !identity && (data.status !== "error" || data.error.code === "API_NOT_CONFIGURED"))
      router.replace(`/login?next=${encodeURIComponent(pathname + (pathname === "/app/reports" || pathname === "/app/analytics" ? window.location.search : ""))}`);
  }, [ready, identity, data, pathname, router]);
  if (data.status === "error" && (!identity || requireSnapshot))
    return (
      <ErrorState
        error={data.error}
        retry={() => void reload()}
        recover={
          identity && service?.mode === "mock" && service.resetDemo
            ? () => {
                void service.resetDemo!().then(reload);
              }
            : undefined
        }
      />
    );
  if (!ready || !identity || (requireSnapshot && data.status === "loading")) return loading ?? <LoadingState />;
  return children;
}
export function SnapshotView({
  children,
  loading,
  retainOnError = false,
}: {
  children: (snapshot: FrontendSnapshot) => ReactNode;
  loading?: ReactNode;
  retainOnError?: boolean;
}) {
  const { data, reload, lastValidSnapshot } = useWorkspace();
  if (retainOnError && (data.status === "ready" || lastValidSnapshot)) {
    const snapshot = data.status === "ready" ? data.data : lastValidSnapshot!;
    return <>{data.status === "error" && <p className="feedback" role="alert">数据获取失败，仍显示上次成功同步的原筛选范围：{data.error.message}</p>}{children(snapshot)}</>;
  }
  return data.status === "loading" ? (
    loading ?? <LoadingState />
  ) : data.status === "error" ? (
    <ErrorState error={data.error} retry={() => void reload()} />
  ) : (
    children(data.data)
  );
}
