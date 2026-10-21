"use client";
import Link from "next/link";
import { useState } from "react";
import {
  ArrowSquareOut,
  Broadcast,
  ChatCircleDots,
  ClockCounterClockwise,
  ListChecks,
  Sparkle,
} from "@phosphor-icons/react";
import { AuthGuard, SnapshotView } from "@/components/provider";
import { Badge, Panel } from "@/components/ui";
import { TaskList } from "@/components/task-controls";
import { CommandPanel } from "@/components/command-panel";
import {
  CoreMetrics,
  LiveSummary,
  ScopeControls,
} from "@/components/operating-context";
import { Goods, Review } from "./business-page";
import { scopedEvidence } from "@/lib/reporting";
const mobileTabs = [
  ["chat", "AI指挥", ChatCircleDots],
  ["live", "直播数据", Broadcast],
  ["tasks", "执行流水", ListChecks],
  ["report", "直播复盘", ClockCounterClockwise],
] as const;
export function MobilePage() {
  const [tab, setTab] = useState<(typeof mobileTabs)[number][0]>("chat");
  return (
    <AuthGuard>
      <SnapshotView>
        {(snapshot) => (
          <div className="mobile-canvas">
            <div className="phone-shell">
              <header className="phone-top">
                <div className="brand">
                  <span className="brand-symbol">
                    <Sparkle size={22} weight="fill" />
                  </span>
                  <span>
                    <b>驭量 AI 指挥官</b>
                    <small>YULIANG MOBILE COMMAND</small>
                  </span>
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
                  <Badge tone="blue">{snapshot.source} · AI指挥</Badge>
                  <h1>一个人，指挥整个直播运营。</h1>
                  <p>{snapshot.tenants[0]?.name} · 指令与审批双端共享</p>
                  <ScopeControls snapshot={snapshot} />
                  <CoreMetrics snapshot={snapshot} />
                  <small>
                    仅本浏览器同源 Mock 演示；localStorage
                    不支持真实跨设备同步。
                  </small>
                </div>
                <nav
                  className="phone-tabs"
                  role="tablist"
                  aria-label="手机指挥标签"
                >
                  {mobileTabs.map(([id, name, Icon]) => (
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
                      <Panel title="最近任务与审批状态">
                        <TaskList
                          snapshot={snapshot}
                          limit={3}
                          review={false}
                        />
                      </Panel>
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
                <small>Mock 交互演示 · 没有真实广告写入或直播推流控制</small>
              </footer>
            </div>
          </div>
        )}
      </SnapshotView>
    </AuthGuard>
  );
}
