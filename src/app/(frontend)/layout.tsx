import type { ReactNode } from "react";
import { WorkspaceProvider } from "@/components/provider";
import { HistoryProvider } from "@/components/history-provider";
import { DeliverySessionProvider } from "@/features/delivery/session";
import "@/styles/tokens.css";
import "@/styles/workspace.css";
import "@/styles/v12.css";
import "@/styles/account.css";
import "@/styles/hub.css";
import "@/styles/content-factory.css";
export default function FrontendLayout({ children }: { children: ReactNode }) {
  return <WorkspaceProvider><HistoryProvider><DeliverySessionProvider>{children}</DeliverySessionProvider></HistoryProvider></WorkspaceProvider>;
}
