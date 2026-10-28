"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useWorkspace } from "./provider";
import { canAccessPage } from "@/lib/access";
import { emptyHistory, type HistorySnapshot } from "@/lib/workspace-history";
import { ServiceError, type Resource } from "@/services/contracts";

const HistoryContext = createContext<{ history: Resource<HistorySnapshot>; refreshHistory: () => Promise<void> } | null>(null);
export function HistoryProvider({ children }: { children: ReactNode }) {
  const { service, identity } = useWorkspace();
  const actorKey = identity ? `${identity.mode}:${identity.tenantId}:${identity.userId}` : "";
  const allowed = canAccessPage(identity, "command");
  const [state, setState] = useState<{ owner: string; resource: Resource<HistorySnapshot> }>({ owner: "", resource: { status: "loading" } });
  const generation = useRef(0);
  const refreshHistory = useCallback(async () => {
    const request = ++generation.current;
    if (!service || !actorKey || !allowed) return;
    try {
      const data = await service.getHistory();
      if (request === generation.current) setState({ owner: actorKey, resource: { status: "ready", data } });
    } catch (error) {
      if (request === generation.current) setState({ owner: actorKey, resource: { status: "error", error: error instanceof ServiceError ? error : new ServiceError("NETWORK_ERROR", "无法读取对话记录，请稍后重试。") } });
    }
  }, [service, actorKey, allowed]);
  useEffect(() => {
    void Promise.resolve().then(refreshHistory);
    const unsubscribe = service?.subscribe(() => void refreshHistory());
    return () => { unsubscribe?.(); };
  }, [service, refreshHistory]);
  const history: Resource<HistorySnapshot> = !allowed ? { status: "ready", data: emptyHistory() } : state.owner === actorKey ? state.resource : { status: "loading" };
  return <HistoryContext.Provider value={{ history, refreshHistory }}>{children}</HistoryContext.Provider>;
}
export function useHistory() {
  const value = useContext(HistoryContext);
  if (!value) throw new Error("HistoryProvider missing");
  return value;
}
