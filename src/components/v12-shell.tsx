"use client";
import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  ArrowClockwise, ArrowSquareOut, Broadcast, ChartLineUp, ChatCircleDots,
  DeviceMobile, List, UserCircle, X, CaretDown,
  SquaresFour, House, MagnifyingGlass, Lightning, FilmStrip, SidebarSimple,
} from "@phosphor-icons/react";
import { pageName, navigationGroups, type PageId } from "@/lib/routes";
import { canAccessPage } from "@/lib/access";
import { useWorkspace } from "./provider";
import { ScopeControls } from "./operating-context";
import { Badge } from "./ui";
import { BrandLogo } from "./brand-logo";
import { SidebarHistory } from "./sidebar-history";

const groupIcons = [ChatCircleDots, SquaresFour, Broadcast, Lightning, FilmStrip, ChartLineUp];

export function WorkspaceShell({ pageId, children }: { pageId: PageId; children: ReactNode }) {
  const [menu, setMenu] = useState(false);
  const [search, setSearch] = useState("");
  const [searching, setSearching] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const { identity, data, reload, service, sidebarCollapsed: collapsed, setSidebarCollapsed: setCollapsed } = useWorkspace();
  const snapshot = data.status === "ready" ? data.data : null;
  const isMock = service?.mode === "mock";
  const command = pageId === "command";
  const currentGroup = navigationGroups.find((group) => group.pages.some(([id]) => id === pageId));
  const allPages = navigationGroups.flatMap((group) => group.pages).filter(([id]) => canAccessPage(identity, id));
  const matchingPages = allPages.filter(([, name]) => name.includes(search.trim()));
  const currentShop = snapshot?.shops.find((shop) => shop.id === snapshot.scope.shop_id);
  useEffect(() => {
    const narrow = window.matchMedia("(max-width: 760px)");
    const expandOnMobile = () => { if (narrow.matches) setCollapsed(false); };
    narrow.addEventListener("change", expandOnMobile);
    return () => narrow.removeEventListener("change", expandOnMobile);
  }, [setCollapsed]);

  return (
    <div className={`workspace-frame v12-workspace ${command ? "command-workspace" : ""} ${collapsed ? "sidebar-collapsed" : ""}`} onKeyDown={(event) => { if (event.key === "Escape") { setMenu(false); setSearching(false); setContextOpen(false); } }}>
      <a className="skip-content" href="#workspace-content">跳到主要内容</a>
      {menu && <button className="sidebar-scrim" aria-label="关闭导航遮罩" onClick={() => setMenu(false)} />}
      <aside className={`sidebar ${menu ? "open" : ""}`} aria-label="工作区侧栏">
        <div className="sidebar-brandbar"><Link href="/" className="brand"><BrandLogo /></Link><button className="sidebar-collapse icon-button" aria-label={collapsed ? "展开侧栏" : "收起侧栏"} title={collapsed ? "展开侧栏" : "收起侧栏"} aria-expanded={!collapsed} aria-controls="workspace-navigation" onClick={() => setCollapsed((previous) => !previous)}><SidebarSimple size={20} /></button></div>
        <button className="mobile-close icon-button" aria-label="关闭菜单" onClick={() => setMenu(false)}><X size={18} /></button>
        <nav aria-label="业务导航" id="workspace-navigation">
          {navigationGroups.map((group, index) => {
            const availablePages = group.pages.filter(([id]) => canAccessPage(identity, id));
            if (!availablePages.length) return null;
            const Icon = groupIcons[index];
            const activeGroup = group === currentGroup;
            if (collapsed || group.pages.length === 1) {
              const [id, name] = availablePages.find(([id]) => id === pageId) ?? availablePages[0];
              return (
                <section className="nav-group" key={group.name}>
                  <Link href={`/app/${id}`} onClick={() => setMenu(false)} title={collapsed ? group.name : undefined} aria-label={collapsed ? group.name : name} aria-current={activeGroup ? "page" : undefined} className={`nav-direct ${activeGroup ? "active" : ""}`}>
                    <Icon size={collapsed ? 21 : 17} weight={activeGroup ? "fill" : "regular"} /><span className="nav-label">{collapsed ? group.name : name}</span>
                  </Link>
                </section>
              );
            }
            const open = expanded[group.name] ?? activeGroup;
            return (
              <section className={`nav-group ${activeGroup ? "nav-group-current" : ""}`} key={group.name}>
                <h2>
                  <button aria-expanded={open} aria-controls={`navigation-${index}`} onClick={() => setExpanded((previous) => ({ ...previous, [group.name]: !open }))}>
                    <Icon size={17} weight={activeGroup ? "fill" : "regular"} />
                    <span>{group.name}</span>
                    <CaretDown size={12} className={open ? "caret-expanded" : ""} />
                  </button>
                </h2>
                <div id={`navigation-${index}`} hidden={!open}>
                  {availablePages.map(([id, name]) => (
                    <Link key={id} href={`/app/${id}`} onClick={() => setMenu(false)} aria-current={pageId === id ? "page" : undefined} className={pageId === id ? "active" : ""}>
                      <span />{name}
                    </Link>
                  ))}
                </div>
              </section>
            );
          })}
        </nav>
        <SidebarHistory collapsed={collapsed} expand={() => setCollapsed(false)} onNavigate={() => setMenu(false)} />
        <div className="sidebar-footer">
          <Link href={identity ? "/app/account" : "/login"} title={collapsed ? "用户中心" : undefined} aria-label={identity ? "用户中心" : "账号登录"} className={`sidebar-account ${pageId === "account" ? "active" : ""}`} aria-current={pageId === "account" ? "page" : undefined} onClick={() => setMenu(false)}><UserCircle size={collapsed ? 23 : 19} /><span><b>{identity ? "用户中心" : "账号登录"}</b><small>{identity?.email ?? identity?.displayName ?? "登录并管理你的账号"}</small></span></Link>
          {canAccessPage(identity, "command") && <Link href="/mobile" aria-label="APP端" title={collapsed ? "APP端" : undefined} onClick={() => setMenu(false)}><DeviceMobile size={collapsed ? 21 : 16} /><span className="sidebar-app-label">APP端</span><ArrowSquareOut size={13} className="sidebar-out" /></Link>}
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div className="row">
            <button className="icon-button mobile-trigger" aria-label="展开菜单" aria-expanded={menu} onClick={() => setMenu(true)}><List size={22} /></button>
            <span className="topbar-breadcrumb">{currentGroup && currentGroup.pages.length > 1 && <>{currentGroup.name}<i>/</i></>}<b>{pageName(pageId)}</b></span>
          </div>
          <div className="row topbar-actions">
            <button className="context-switch" aria-expanded={contextOpen} aria-controls="global-context" onClick={() => {setContextOpen(!contextOpen); setSearching(false);}}>
              <House size={14} />{currentShop?.name ?? "全部授权店铺"}<CaretDown size={11} />
            </button>
            <button className="icon-button" aria-label="搜索工作区" aria-expanded={searching} aria-controls="workspace-search" onClick={() => {setSearching(!searching); setContextOpen(false);}}><MagnifyingGlass size={18} /></button>
            {isMock && <Badge tone="blue">本地预览</Badge>}
            <button className="icon-button" aria-label="刷新工作区" onClick={() => { if (pageId === "monitor") window.dispatchEvent(new Event("yuliang:refresh-live-room")); else void reload(); }}><ArrowClockwise size={18} /></button>
            <Link href="/app/account" className="avatar" aria-label="打开用户中心">{identity?.displayName?.slice(0, 1) ?? "易"}</Link>
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
        {isMock && <div className="mode-notice">
          <span className="mock-indicator" />
          <span>本地功能预览，数据与审批均为测试数据。</span>
        </div>}
        <main id="workspace-content" className="page-main">
          {!command && <div className="page-heading"><div><div className="eyebrow">YULIANG / OPERATIONS</div><h1>{pageName(pageId)}</h1><p>{pageId === "account" ? "管理个人资料，查看已授权的店铺与工作区。" : pageId === "overview" ? "统一统计口径，查看经营指标与任务进展。" : "让经营目标、内容、投放与结果在同一条工作流中连接。"}</p></div>{canAccessPage(identity, "command") && <Link href="/app/command" className="button secondary"><Lightning size={16} />AI 帮我处理</Link>}</div>}
          {children}
        </main>
        <footer className="workspace-footer">自主投放，让每一份增长都有据可依。<span>易致·驭量 · {isMock ? "本地功能预览" : "运营工作空间"}</span></footer>
      </div>
    </div>
  );
}
