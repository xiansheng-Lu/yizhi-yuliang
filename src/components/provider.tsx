"use client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import type { FrontendSnapshot, Identity } from "@/lib/domain";
import { createFrontendService } from "@/services";
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
  reload: () => Promise<void>;
  logout: () => Promise<void>;
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
  const generation = useRef(0);
  const reload = useCallback(async () => {
    const request = ++generation.current;
    try {
      service.current ??= createFrontendService();
      setClient(service.current);
      const actor = await service.current.getIdentity();
      if (request !== generation.current) return;
      setIdentity(actor);
      if (actor) {
        const snapshot = await service.current.getSnapshot();
        if (request === generation.current)
          setData({ status: "ready", data: snapshot });
      } else setData({ status: "loading" });
    } catch (error) {
      if (request === generation.current)
        setData({
          status: "error",
          error:
            error instanceof ServiceError
              ? error
              : new ServiceError("NETWORK_ERROR", "无法读取前端服务，请重试。"),
        });
    } finally {
      if (request === generation.current) setReady(true);
    }
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
    setIdentity(null);
    await reload();
  }
  return (
    <WorkspaceContext.Provider
      value={{ service: client, identity, ready, data, reload, logout }}
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
export function AuthGuard({ children }: { children: ReactNode }) {
  const { ready, identity, data, reload, service } = useWorkspace();
  const pathname = usePathname(),
    router = useRouter();
  useEffect(() => {
    if (ready && !identity && data.status !== "error")
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [ready, identity, data.status, pathname, router]);
  if (data.status === "error")
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
  if (!ready || !identity || data.status === "loading") return <LoadingState />;
  return children;
}
export function SnapshotView({
  children,
}: {
  children: (snapshot: FrontendSnapshot) => ReactNode;
}) {
  const { data, reload } = useWorkspace();
  return data.status === "loading" ? (
    <LoadingState />
  ) : data.status === "error" ? (
    <ErrorState error={data.error} retry={() => void reload()} />
  ) : (
    children(data.data)
  );
}
