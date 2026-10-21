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
import { Badge, EmptyState, Panel } from "@/components/ui";
import { TaskComposer, TaskList } from "@/components/task-controls";
import { CoreMetrics, Goods, Review } from "./business-page";
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
                  <Badge tone="blue">{snapshot.source} · 直播指挥</Badge>
                  <h1>一个人，指挥整个直播运营。</h1>
                  <p>
                    {snapshot.tenants[0]?.name} ·{" "}
                    {snapshot.liveSessions[0]?.name}
                  </p>
                  <CoreMetrics snapshot={snapshot} />
                  <small>
                    仅演示同源浏览器共享；真实手机跨设备同步待后端。
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
                      <div className="chat-message">
                        <Sparkle size={20} />
                        <div>
                          <b>先明确意图，再确认操作对象。</b>
                          <p>
                            本阶段接收结构化申请。预算、出价和 ROI
                            目标分别记录；直播开关播交给负责人确认。
                          </p>
                        </div>
                      </div>
                      <Panel title="向 AI 提交运营意图">
                        <TaskComposer snapshot={snapshot} origin="MOBILE" />
                      </Panel>
                      <Panel title="同一任务，双端查看">
                        <TaskList snapshot={snapshot} limit={3} />
                      </Panel>
                    </>
                  )}
                  {tab === "live" && (
                    <>
                      <Panel title="当前直播场次">
                        <div className="live-figures">
                          <div>
                            <span>直播在线</span>
                            <b>{snapshot.liveSessions[0]?.online ?? "缺失"}</b>
                          </div>
                          <div>
                            <span>商品点击</span>
                            <b>
                              {snapshot.liveSessions[0]?.productClicks ??
                                "缺失"}
                            </b>
                          </div>
                        </div>
                        <p>Mock 固定采样，不代表实时平台数据。</p>
                      </Panel>
                      <Panel title="货盘与主播话术">
                        <Goods snapshot={snapshot} />
                      </Panel>
                    </>
                  )}
                  {tab === "tasks" && (
                    <>
                      <Panel title="PC / 手机 / 自主投放共享任务">
                        <TaskList snapshot={snapshot} />
                      </Panel>
                      <Panel title="平台执行回执">
                        <EmptyState
                          title="尚无真实执行回执"
                          description="人工批准不等于平台执行成功。"
                        />
                      </Panel>
                    </>
                  )}
                  {tab === "report" && (
                    <Panel title="直播联合复盘">
                      <Review snapshot={snapshot} />
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
