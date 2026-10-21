"use client";
import { useState, type ReactNode } from "react";
import Link from "next/link";
import {
  ArrowClockwise,
  ArrowSquareOut,
  Broadcast,
  ChartLineUp,
  DeviceMobile,
  List,
  SignOut,
  Sparkle,
  Stack,
  X,
} from "@phosphor-icons/react";
import { pageName, workspaces, type PageId } from "@/lib/routes";
import { useWorkspace } from "./provider";
import { Badge } from "./ui";
const groupIcons = [ChartLineUp, Sparkle, Broadcast, Stack];

export function WorkspaceShell({
  pageId,
  children,
}: {
  pageId: PageId;
  children: ReactNode;
}) {
  const [menu, setMenu] = useState(false);
  const { identity, data, reload, logout, service } = useWorkspace();
  const tenant =
    data.status === "ready"
      ? data.data.tenants.find((x) => x.id === identity?.tenantId)
      : undefined;
  const isMock = service?.mode === "mock";
  return (
    <div className="workspace-frame">
      {menu && (
        <button
          className="sidebar-scrim"
          aria-label="关闭导航遮罩"
          onClick={() => setMenu(false)}
        />
      )}
      <aside className={`sidebar ${menu ? "open" : ""}`}>
        <Link href="/" className="brand">
          <span className="brand-symbol">
            <Sparkle size={23} weight="fill" />
          </span>
          <span>
            <b>易致·驭量</b>
            <small>YULIANG WORKSPACE</small>
          </span>
        </Link>
        <button
          className="mobile-close icon-button"
          aria-label="关闭菜单"
          onClick={() => setMenu(false)}
        >
          <X size={18} />
        </button>
        <div className="tenant-block">
          <span>易</span>
          <div>
            <b>{tenant?.name ?? "当前企业"}</b>
            <small>{identity?.displayName}</small>
          </div>
        </div>
        <nav aria-label="四大工作区">
          {workspaces.map((group, i) => {
            const Icon = groupIcons[i];
            return (
              <section className="nav-group" key={group.name}>
                <h2>
                  <Icon size={15} />
                  {group.name}
                </h2>
                {group.pages.map(([id, name]) => (
                  <Link
                    key={id}
                    href={`/app/${id}`}
                    onClick={() => setMenu(false)}
                    aria-current={pageId === id ? "page" : undefined}
                    className={pageId === id ? "active" : ""}
                  >
                    <span />
                    {name}
                  </Link>
                ))}
              </section>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <Link href="/mobile">
            <DeviceMobile size={16} />
            手机 AI 指挥
            <ArrowSquareOut size={13} />
          </Link>
          <a href="/">
            <ArrowSquareOut size={16} />
            返回独立官网
          </a>
          <button onClick={() => void logout()}>
            <SignOut size={16} />
            退出演示
          </button>
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div className="row">
            <button
              className="icon-button mobile-trigger"
              aria-label="展开菜单"
              onClick={() => setMenu(true)}
            >
              <List size={22} />
            </button>
            <span>
              工作台 <i>/</i> <b>{pageName(pageId)}</b>
            </span>
          </div>
          <div className="row">
            <Badge tone="blue">{isMock ? "Mock 身份与数据" : "API 模式"}</Badge>
            <button
              className="icon-button"
              aria-label="刷新工作区"
              onClick={() => void reload()}
            >
              <ArrowClockwise size={18} />
            </button>
            <span className="avatar">易</span>
          </div>
        </header>
        <div className="mode-notice">
          {isMock
            ? "前端演示：所有数据、审批均为 Mock，无真实广告操作。同源同浏览器可共享状态，不能跨设备同步。"
            : "API 模式：由独立后端提供会话、数据和操作权限。"}
        </div>
        <main className="page-main">
          <div className="page-heading">
            <div>
              <div className="eyebrow">YULIANG / AI WORKSPACE</div>
              <h1>{pageName(pageId)}</h1>
              <p>
                {pageId === "overview"
                  ? "从三项经营指标出发，查看 AI 正在做什么。"
                  : "连接经营目标、内容任务、投放申请与复盘证据。"}
              </p>
            </div>
            <Link href="/mobile" className="button secondary">
              <DeviceMobile size={17} />
              手机 AI 指挥
            </Link>
          </div>
          {children}
        </main>
        <footer className="workspace-footer">
          自主投放，让每一份增长都有据可依。<span>G1-B1 · 统一任务交互</span>
        </footer>
      </div>
    </div>
  );
}
