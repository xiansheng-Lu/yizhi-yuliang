import type { ReactNode } from "react";
import { WorkspaceProvider } from "@/components/provider";
import "@/styles/tokens.css";
import "@/styles/workspace.css";
import "@/styles/v12.css";
export default function FrontendLayout({ children }: { children: ReactNode }) {
  return <WorkspaceProvider>{children}</WorkspaceProvider>;
}
