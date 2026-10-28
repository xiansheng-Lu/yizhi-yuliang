"use client";
import { createContext, useContext, useState, type ReactNode } from "react";
import type { PlanFilter } from "@/lib/delivery";
interface SessionMemory {
  drafts: Record<string, unknown>;
  filters: Record<string, PlanFilter>;
  uncertainKeys: string[];
}
const empty: SessionMemory = { drafts: {}, filters: {}, uncertainKeys: [] };
const Session = createContext<{
  read: (owner: string) => SessionMemory;
  update: (
    owner: string,
    change: (value: SessionMemory) => SessionMemory,
  ) => void;
} | null>(null);
/** In-memory session state survives workbench navigation. No tokens or business
 * drafts are written to browser persistent storage. Owners are isolated. */
export function DeliverySessionProvider({ children }: { children: ReactNode }) {
  const [owners, setOwners] = useState<Record<string, SessionMemory>>({});
  return (
    <Session.Provider
      value={{
        read: (owner) => owners[owner] ?? empty,
        update: (owner, change) =>
          setOwners((previous) => ({
            ...previous,
            [owner]: change(previous[owner] ?? empty),
          })),
      }}
    >
      {children}
    </Session.Provider>
  );
}
export function useDeliverySession(owner: string) {
  const value = useContext(Session);
  if (!value) throw new Error("Delivery session missing");
  return {
    ...value.read(owner),
    update: (change: (memory: SessionMemory) => SessionMemory) =>
      value.update(owner, change),
  };
}
