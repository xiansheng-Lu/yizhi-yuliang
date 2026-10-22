import type { ReactNode } from "react";
import { WorkspaceProvider } from "@/components/provider";
import { HistoryProvider } from "@/components/history-provider";
import "@/styles/tokens.css";
import "@/styles/workspace.css";
import "@/styles/v12.css";
import "@/styles/account.css";
import "@/styles/hub.css";
export default function FrontendLayout({ children }: { children: ReactNode }) {
  return <WorkspaceProvider><HistoryProvider>{children}</HistoryProvider></WorkspaceProvider>;
}
