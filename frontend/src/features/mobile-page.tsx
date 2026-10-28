"use client";
import Link from "next/link";
import { useState } from "react";
import {
  ArrowSquareOut,
  Broadcast,
  ChatCircleDots,
  ClockCounterClockwise,
  ListChecks,
} from "@phosphor-icons/react";
import { AuthGuard, SnapshotView, useWorkspace } from "@/components/provider";
import { Badge, EmptyState, Panel } from "@/components/ui";
import { canAccessPage } from "@/lib/access";
import { TaskList } from "@/components/task-controls";
import { CommandPanel } from "@/components/command-panel";
import { BrandLogo } from "@/components/brand-logo";
import {
  CoreMetrics,
  LiveSummary,
  ScopeControls,
} from "@/components/operating-context";
import { Goods, Review } from "./business-page";
import { scopedEvidence } from "@/lib/reporting";
const mobileTabs = [
  ["chat", "AI指挥", ChatCircleDots, "command"],
  ["live", "直播数据", Broadcast, "monitor"],
  ["tasks", "执行流水", ListChecks, "tasks"],
  ["report", "直播复盘", ClockCounterClockwise, "reports"],
] as const;
export function MobilePage() {
  const { identity } = useWorkspace();
  const [selectedTab, setTab] = useState<(typeof mobileTabs)[number][0]>("chat");
  const availableTabs = mobileTabs.filter((item) => canAccessPage(identity, item[3]));
  const tab = availableTabs.find(([id]) => id === selectedTab)?.[0] ?? availableTabs[0]?.[0];
  return (
    <AuthGuard>
      {!canAccessPage(identity, "command") ? <EmptyState title="APP端尚未授权" description="请在用户中心查看当前账号可使用的工作区。" /> : <SnapshotView>
        {(snapshot) => (
          <div className="mobile-canvas">
            <div className="phone-shell">
              <header className="phone-top">
                <div className="brand">
                  <BrandLogo />
                </div>
                <Link
                  href="/app/monitor"
                  className="icon-button"
                  aria-label="返回 PC 直播作战室"
                >
                  <ArrowSquareOut size={21} />
                </Link>
              </header>
              <main>
                <div className="phone-summary">
                  <Badge tone="blue">{snapshot.source === "MOCK" ? "本地预览 · " : ""}AI指挥</Badge>
                  <h1>一个人，指挥整个直播运营。</h1>
                  <p>{snapshot.tenants[0]?.name} · 指令与审批双端共享</p>
                  <ScopeControls snapshot={snapshot} />
                  <CoreMetrics snapshot={snapshot} />
                  <small>
                    {snapshot.source === "MOCK" ? "本地预览仅在同源浏览器中共享数据。" : "与工作台使用同一账号及授权范围。"}
                  </small>
                </div>
                <nav
                  className="phone-tabs"
                  role="tablist"
                  aria-label="手机指挥标签"
                >
                  {availableTabs.map(([id, name, Icon]) => (
                    <button
                      key={id}
                      role="tab"
                      aria-selected={tab === id}
                      onClick={() => setTab(id)}
                    >
                      <Icon size={18} />
                      {name}
                    </button>
                  ))}
                </nav>
                <section className="phone-view" role="tabpanel">
                  {tab === "chat" && (
                    <>
                      <Panel
                        title="向AI下达指令"
                        detail="先核对具体对象、变更差异与风控"
                      >
                        <CommandPanel
                          snapshot={snapshot}
                          source="MOBILE_COMMAND"
                        />
                      </Panel>
                      {canAccessPage(identity, "tasks") && <Panel title="最近任务与审批状态">
                        <TaskList
                          snapshot={snapshot}
                          limit={3}
                          review={false}
                        />
                      </Panel>}
                    </>
                  )}
                  {tab === "live" && (
                    <>
                      <Panel title="当前直播场次">
                        <LiveSummary snapshot={snapshot} />
                      </Panel>
                      <Panel title="货盘与主播话术">
                        <Goods
                          snapshot={{
                            ...snapshot,
                            products: snapshot.products.filter(
                              (p) =>
                                snapshot.scope.shop_id === "all" ||
                                p.shopId === snapshot.scope.shop_id,
                            ),
                          }}
                        />
                      </Panel>
                    </>
                  )}
                  {tab === "tasks" && (
                    <Panel
                      title="PC / 手机 / 自主投放共享流水"
                      detail="展开任务查看执行回执与核验结果"
                    >
                      <TaskList snapshot={snapshot} review={false} />
                    </Panel>
                  )}
                  {tab === "report" && (
                    <Panel title="直播联合复盘">
                      <Review snapshot={scopedEvidence(snapshot)} />
                      <Link href="/app/reports" className="button secondary">
                        打开 PC 完整复盘 <ArrowSquareOut size={16} />
                      </Link>
                    </Panel>
                  )}
                </section>
              </main>
              <footer className="phone-footer">
                自主投放，让每一份增长都有据可依。
                {snapshot.source === "MOCK" && <small>本地功能预览 · 没有真实广告操作</small>}
              </footer>
            </div>
          </div>
        )}
      </SnapshotView>}
    </AuthGuard>
  );
}
