"use client";
import { useState, type ReactNode } from "react";
import Link from "next/link";
import {
  ArrowClockwise, ArrowSquareOut, Broadcast, ChartLineUp, ChatCircleDots,
  DeviceMobile, List, SignOut, X, CaretDown, Plus,
  CheckSquare, House, Question, MagnifyingGlass, Lightning, Gear, FilmStrip,
} from "@phosphor-icons/react";
import { pageName, navigationGroups, type PageId } from "@/lib/routes";
import { useWorkspace } from "./provider";
import { ScopeControls } from "./operating-context";
import { Badge } from "./ui";
import { BrandLogo } from "./brand-logo";

const groupIcons = [ChatCircleDots, CheckSquare, Broadcast, Lightning, FilmStrip, ChartLineUp, Gear];

export function WorkspaceShell({ pageId, children }: { pageId: PageId; children: ReactNode }) {
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState("");
  const [searching, setSearching] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const { identity, data, reload, logout, service } = useWorkspace();
  const snapshot = data.status === "ready" ? data.data : null;
  const tenant = snapshot?.tenants.find((x) => x.id === identity?.tenantId);
  const isMock = service?.mode === "mock";
  const pending = snapshot?.tasks.filter((job) => job.status === "PENDING_APPROVAL").length ?? 0;
  const command = pageId === "command";
  const currentGroup = navigationGroups.find((group) => group.pages.some(([id]) => id === pageId));
  const allPages = navigationGroups.flatMap((group) => group.pages);
  const matchingPages = allPages.filter(([, name]) => name.includes(search.trim()));
  const currentShop = snapshot?.shops.find((shop) => shop.id === snapshot.scope.shop_id);

  return (
    <div className={`workspace-frame v12-workspace ${command ? "command-workspace" : ""}`} onKeyDown={(event) => { if (event.key === "Escape") { setMenu(false); setSearching(false); setContextOpen(false); } }}>
      <a className="skip-content" href="#workspace-content">跳到主要内容</a>
      {menu && <button className="sidebar-scrim" aria-label="关闭导航遮罩" onClick={() => setMenu(false)} />}
      <aside className={`sidebar ${menu ? "open" : ""}`} aria-label="工作区侧栏">
        <Link href="/" className="brand">
          <BrandLogo />
          <span className="version-label">V12</span>
        </Link>
        <button className="mobile-close icon-button" aria-label="关闭菜单" onClick={() => setMenu(false)}><X size={18} /></button>
        <div className="tenant-block">
          <span>易</span><div><b>{tenant?.name ?? "当前企业"}</b><small>企业运营空间</small></div>
          <Badge tone="blue">{isMock ? "演示" : "API"}</Badge>
        </div>
        <Link className="new-command" href="/app/command" onClick={() => { setMenu(false); window.dispatchEvent(new Event("yuliang:new-command")); }}>
          <Plus size={17} weight="bold" />开始运营指挥<span>AI</span>
        </Link>
        <nav aria-label="V12 七组工作区">
          {navigationGroups.map((group, index) => {
            const Icon = groupIcons[index];
            const activeGroup = group === currentGroup;
            const open = expanded[group.name] ?? activeGroup;
            return (
              <section className={`nav-group ${activeGroup ? "nav-group-current" : ""}`} key={group.name}>
                <h2>
                  <button aria-expanded={open} aria-controls={`navigation-${index}`} onClick={() => setExpanded((previous) => ({ ...previous, [group.name]: !open }))}>
                    <Icon size={17} weight={activeGroup ? "fill" : "regular"} />
                    <span>{group.name}</span>
                    {group.name === "任务中心" && pending > 0 && <span className="nav-count">{pending}</span>}
                    <CaretDown size={12} className={open ? "caret-expanded" : ""} />
                  </button>
                </h2>
                <div id={`navigation-${index}`} hidden={!open}>
                  {group.pages.map(([id, name]) => (
                    <Link key={id} href={`/app/${id}`} onClick={() => setMenu(false)} aria-current={pageId === id ? "page" : undefined} className={pageId === id ? "active" : ""}>
                      <span />{name}
                    </Link>
                  ))}
                </div>
              </section>
            );
          })}
        </nav>
        <div className="sidebar-footer">
          <Link href="/mobile"><DeviceMobile size={16} />手机 AI 指挥<ArrowSquareOut size={13} /></Link>
          <a href="/"><House size={16} />返回官网</a>
          <button onClick={() => void logout()}><SignOut size={16} />{isMock ? "退出演示" : "退出登录"}</button>
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div className="row">
            <button className="icon-button mobile-trigger" aria-label="展开菜单" aria-expanded={menu} onClick={() => setMenu(true)}><List size={22} /></button>
            <span className="topbar-breadcrumb">{currentGroup?.name}<i>/</i><b>{pageName(pageId)}</b></span>
          </div>
          <div className="row topbar-actions">
            <button className="context-switch" aria-expanded={contextOpen} aria-controls="global-context" onClick={() => {setContextOpen(!contextOpen); setSearching(false);}}>
              <House size={14} />{currentShop?.name ?? "全部授权店铺"}<CaretDown size={11} />
            </button>
            <button className="icon-button" aria-label="搜索工作区" aria-expanded={searching} aria-controls="workspace-search" onClick={() => {setSearching(!searching); setContextOpen(false);}}><MagnifyingGlass size={18} /></button>
            <Badge tone="blue">{isMock ? "Mock 演示" : "API 模式"}</Badge>
            <button className="icon-button" aria-label="刷新工作区" onClick={() => void reload()}><ArrowClockwise size={18} /></button>
            <Link href="/app/settings" className="icon-button help-button" aria-label="帮助与设置"><Question size={19} /></Link>
            <span className="avatar">{identity?.displayName?.slice(0, 1) ?? "易"}</span>
          </div>
        </header>
        {contextOpen && snapshot && (
          <section className="workspace-search context-popover" id="global-context" aria-label="当前经营上下文">
            <div className="row between"><h2>当前经营上下文</h2><button className="icon-button" aria-label="关闭经营上下文" onClick={() => setContextOpen(false)}><X size={17} /></button></div>
            <ScopeControls snapshot={snapshot} />
          </section>
        )}
        {searching && (
          <section className="workspace-search" id="workspace-search" aria-label="工作区搜索">
            <div className="row between"><h2>查找工作区</h2><button className="icon-button" aria-label="关闭搜索" onClick={() => setSearching(false)}><X size={17} /></button></div>
            <label>业务名称<input autoFocus aria-label="搜索工作区名称" placeholder="如素材、预算、复盘" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
            <div>{matchingPages.map(([id, name]) => <Link key={id} href={`/app/${id}`} onClick={() => setSearching(false)}>{name}<ArrowSquareOut size={12} /></Link>)}</div>
            {!matchingPages.length && <p>没有找到相关工作区，试试“素材”或“复盘”。</p>}
          </section>
        )}
        <div className="mode-notice">
          <span className="mock-indicator" />
          <span>{isMock ? "当前为前端 Mock 演示，数据与审批均为模拟，无真实广告操作。" : "API 模式 · 会话、业务数据与权限由独立后端提供。"}</span>
          <Link href="/app/connections">平台连接<ArrowSquareOut size={12} /></Link>
        </div>
        <main id="workspace-content" className="page-main">
          {!command && <div className="page-heading"><div><div className="eyebrow">YULIANG / OPERATIONS</div><h1>{pageName(pageId)}</h1><p>{pageId === "overview" ? "统一统计口径，查看经营指标与任务进展。" : "让经营目标、内容、投放与结果在同一条工作流中连接。"}</p></div><Link href="/app/command" className="button secondary"><Lightning size={16} />AI 帮我处理</Link></div>}
          {children}
        </main>
        <footer className="workspace-footer">自主投放，让每一份增长都有据可依。<span>易致·驭量 V12 · {isMock ? "Mock 前端工作空间" : "API 工作空间"}</span></footer>
      </div>
    </div>
  );
}
