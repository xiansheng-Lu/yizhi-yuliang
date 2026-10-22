"use client";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowClockwise, ArrowRight, Broadcast, ChartLineUp, Clock, Eye, Info, Play, Robot, ShieldCheck, WarningCircle, Waveform, Users, ShoppingBag, CurrencyCny, Lightning } from "@phosphor-icons/react";
import { connectorNames, type FrontendSnapshot } from "@/lib/domain";
import { formatLiveTime, isDelayed, liveCadence, liveDuration, liveStatus, liveRefreshConfiguration, oldestTime, moduleNames, selectedRoom, statusNames, type LiveModule, type LiveModules, type LiveStatus, type ModuleState } from "@/lib/live-room";
import { useWorkspace } from "@/components/provider";
import { ScopeControls } from "@/components/operating-context";
import { CommandPanel } from "@/components/command-panel";
import { TaskList } from "@/components/task-controls";
import { initialLiveModules, LiveRoomController } from "@/services/live-room-controller";
import { canAccessPage } from "@/lib/access";
import { scopedEvidence } from "@/lib/reporting";
import { Goods, Replay } from "./business-page";
import "@/styles/live-room.css";

const numbers = new Intl.NumberFormat("zh-CN");
const money = (value: number | null | undefined) => typeof value !== "number" || !Number.isFinite(value) || value < 0 ? "--" : `¥${numbers.format(value / 100)}`;
export function LiveRoomSkeleton() {
  return <div className="live-room lr-loading" aria-label="直播作战室加载中" aria-busy="true"><Skeleton label="直播状态" /><div className="lr-panel lr-stage-placeholder"><Skeleton label="直播主画面" /></div><div className="lr-panel lr-metrics">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} label="经营指标" />)}</div><div className="lr-panel"><Skeleton label="直播趋势" /></div></div>;
}
function Skeleton({ label }: { label: string }) {
  return <div className="lr-skeleton" role="status" aria-label={`${label}加载中`}><span /><span /><span /></div>;
}
function ReadTime({ state, module, status, now, minimum = 0 }: { state: ModuleState<unknown>; module: LiveModule; status: LiveStatus; now: number; minimum?: number }) {
  const delayed = isDelayed(state, module, status, now, minimum);
  return <div className={`lr-read-time ${delayed ? "is-delayed" : ""}`} aria-live="polite">
    {state.refreshing ? <><ArrowClockwise className="lr-spin" size={12} />正在获取</> : delayed ? <><WarningCircle size={13} />数据更新延迟</> : <Clock size={12} />}
    <span>{moduleNames[module]} · {module === "tasks" ? "状态变更" : "平台更新"} {formatLiveTime(state.updatedAt)}</span>
    <span>最近成功同步 {formatLiveTime(state.lastSuccessAt)}</span>
    {state.error && <strong role="alert">获取失败，{state.data ? "保留上次数据" : "暂无有效数据"}：{state.error}</strong>}
  </div>;
}
function Section({ title, icon, action, children, className = "" }: { title: string; icon: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`lr-panel ${className}`} aria-label={title}><header className="lr-panel-heading"><h2>{icon}{title}</h2>{action}</header>{children}</section>;
}
function TrendPanel({ selected, range, setRange, metric, setMetric }: { selected: boolean; range: string; setRange: (s: string) => void; metric: string; setMetric: (s: string) => void }) {
  return <Section title="直播趋势" icon={<ChartLineUp size={18} />} action={<div className="lr-segment" aria-label="趋势时间范围">{["15分钟", "30分钟", "本场"].map((label) => <button key={label} aria-pressed={range === label} onClick={() => setRange(label)}>{label}</button>)}</div>}>
    <div className="lr-trend-tabs" role="tablist" aria-label="趋势指标">{["在线趋势", "GMV趋势", "互动趋势"].map((name) => <button key={name} role="tab" aria-selected={name === metric} onClick={() => setMetric(name)}>{name}</button>)}</div>
    <div className="lr-trend-empty" role="tabpanel" aria-label={`${range}${metric}`}><ChartLineUp size={30} weight="light" /><strong>{selected ? `${metric}数据待接入` : "选择直播间后查看趋势"}</strong><p>{selected ? `当前选择：${range}。现有快照不含时间序列，无法绘制真实曲线。` : "在线、成交与互动使用同一场次和时间范围。"}</p></div>
    <div className="lr-module-footer"><span>数据更新 -- · 成功获取 --</span><span>时间序列接口待接入 · 目标刷新 60秒</span></div>
  </Section>;
}

export function LiveRoomPage({ snapshot }: { snapshot: FrontendSnapshot }) {
  const { service, identity } = useWorkspace();
  const configuration = useMemo(() => liveRefreshConfiguration(), []);
  const [retryAt, setRetryAt] = useState(0);
  const selected = selectedRoom(snapshot);
  const [modules, setModules] = useState<LiveModules>(() => initialLiveModules(!!selected));
  const [now, setNow] = useState<number | null>(null);
  const [visible, setVisible] = useState(true);
  const [range, setRange] = useState("30分钟"), [metric, setMetric] = useState("在线趋势");
  const [command, setCommand] = useState("");
  const controller = useRef<LiveRoomController | null>(null);
  const live = modules.live.data;
  const status = live?.status ?? liveStatus(selected, snapshot.source);
  const room = live?.room ?? selected;
  const ended = status === "ended";
  const demo = snapshot.source === "MOCK";
  const commerce = modules.commerce.data;
  const plans = modules.plans.data ?? [];
  const tasks = modules.tasks.data ?? [];
  // Prop updates from the shared approval components must be reflected, without
  // restarting polling or discarding per-module successful reads.
  const latestSnapshot = useRef(snapshot);
  useEffect(() => {
    latestSnapshot.current = snapshot;
    controller.current?.acceptSnapshot(snapshot);
  }, [snapshot]);
  useEffect(() => {
    if (!service) return;
    const current = new LiveRoomController(service, latestSnapshot.current, latestSnapshot.current.scope, (state) => {
      setModules(state); setRetryAt(current.retryAt);
    }, configuration);
    controller.current = current;
    let ticker: ReturnType<typeof setInterval> | undefined;
    const updateVisibility = () => {
      const active = document.visibilityState !== "hidden";
      setVisible(active);
      setNow(Date.now());
      clearInterval(ticker);
      if (active) ticker = setInterval(() => setNow(Date.now()), 1000);
      current.setVisible(active);
    };
    const manual = () => { void current.refresh(true); };
    const suspend = () => current.suspend();
    const resume = () => current.resume();
    updateVisibility();
    current.start(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", updateVisibility);
    window.addEventListener("yuliang:refresh-live-room", manual);
    window.addEventListener("yuliang:scope-changing", suspend);
    window.addEventListener("yuliang:scope-settled", resume);
    return () => {
      current.stop();
      clearInterval(ticker);
      document.removeEventListener("visibilitychange", updateVisibility);
      window.removeEventListener("yuliang:refresh-live-room", manual);
      window.removeEventListener("yuliang:scope-changing", suspend);
      window.removeEventListener("yuliang:scope-settled", resume);
      controller.current = null;
    };
  }, [service, configuration]);
  const refreshing = Object.values(modules).some((m) => m.refreshing);
  const minimum = demo ? 0 : configuration.snapshotMinimumMs;
  const automatic = demo || minimum > 0;
  const waiting = now !== null && now < retryAt;
  const delayed = now !== null && (["live", "commerce", "tasks", "plans"] as LiveModule[]).some((key) => isDelayed(modules[key], key, status, now, minimum, configuration));
  const oldest = oldestTime([modules.live.updatedAt, modules.commerce.updatedAt, modules.plans.updatedAt]);
  const syncTimes = [modules.live.lastSuccessAt, modules.commerce.lastSuccessAt, modules.tasks.lastSuccessAt, modules.plans.lastSuccessAt];
  const successfulTimes = syncTimes.filter((at): at is number => at !== null);
  const syncedAt = successfulTimes.length ? Math.max(...successfulTimes) : null;
  const hasErrors = Object.values(modules).some((state) => !!state.error);
  const pending = tasks.filter((job) => job.status === "PENDING_APPROVAL").length;
  const running = tasks.filter((job) => ["EXECUTING", "VERIFYING"].includes(job.status)).length;
  const commandSnapshot = { ...snapshot, liveSessions: room ? [room] : [], plans: modules.plans.data ?? [], tasks: modules.tasks.data ?? [] };
  const canCreate = !!identity?.permissions.includes("task.create");
  const canTasks = canAccessPage(identity, "tasks");
  const metrics = [
    { name: "在线人数", value: live?.online === null || live?.online === undefined ? "--" : numbers.format(live.online), unit: "人", icon: Users, module: "live" as const, detail: "直播间当前在线" },
    { name: "累计观看", value: live?.viewers === null || live?.viewers === undefined ? "--" : numbers.format(live.viewers), unit: "人", icon: Eye, module: "live" as const, detail: live?.viewers == null ? "累计观看字段待接入" : "本场累计观看" },
    { name: "支付GMV", value: money(commerce?.gmv), unit: "", icon: CurrencyCny, module: "commerce" as const, detail: "关联计划 · 支付归因" },
    { name: "成交订单", value: commerce?.orders == null ? "--" : numbers.format(commerce.orders), unit: "单", icon: ShoppingBag, module: "commerce" as const, detail: commerce?.orders == null ? "成交订单字段待接入" : "本场成交订单" },
    { name: "广告消耗", value: money(commerce?.spend), unit: "", icon: Lightning, module: "commerce" as const, detail: "含暂停计划历史消耗" },
    { name: "投放ROI", value: commerce?.roi?.toFixed(2) ?? "--", unit: "", icon: ChartLineUp, module: "commerce" as const, detail: "沿用现有支付归因口径" },
  ];
  const planRoi = (gmv: number | null, spend: number | null) => gmv !== null && spend !== null && spend > 0 ? (gmv / spend).toFixed(2) : "--";
  const roiTarget = snapshot.riskPolicies.find((policy) => policy.tenantId === room?.tenantId)?.targetRoi;
  const roiWarning = commerce?.roi !== null && commerce?.roi !== undefined && roiTarget !== undefined && commerce.roi < roiTarget;
  const clock = now ?? 0;
  return <div className="live-room" data-source={snapshot.source} data-live-status={status}>
    <div className="lr-heading"><div><span className="lr-kicker"><Broadcast size={15} />直播运营</span><h1>AI直播作战室</h1><p>看清直播现场，让每一次决策有据可依。</p></div><div className="lr-heading-actions"><Link href="/app/reports" className="button secondary"><ChartLineUp size={16} />直播复盘</Link><button className="button primary" disabled={!selected || refreshing || waiting} onClick={() => { void controller.current?.refresh(true); }}><ArrowClockwise size={16} className={refreshing ? "lr-spin" : ""} />{refreshing ? "刷新中…" : waiting ? "稍后可刷新" : "刷新数据"}</button></div></div>
    <div className="lr-status-bar" aria-label="直播与数据状态"><div><span className={`lr-status ${status === "live" ? "is-live" : ""}`}><i />{demo && status === "live" ? "演示直播中" : statusNames[status]}</span><span className={`lr-source ${demo ? "is-mock" : ""}`}>{demo ? "模拟数据 · 本地演示" : "API数据"}</span><span className={delayed ? "lr-warning" : ""}>{hasErrors ? "同步失败 · 保留历史值" : delayed ? "数据更新延迟" : refreshing ? "正在同步" : ended ? "展示历史数据" : !syncedAt ? "尚未同步" : !oldest ? "平台更新时间待提供" : "数据已同步"} · 最近同步 {formatLiveTime(syncedAt)}</span><span>平台更新 {formatLiveTime(oldest)}</span></div><span>{!visible ? "页面不可见 · 自动刷新暂停" : ended ? "本场已结束 · 自动刷新已停止" : !selected ? "选择直播间以获取场次数据" : !automatic ? "接口限频待确认 · 仅手动刷新" : status === "live" ? demo ? "分模块获取 · 固定模拟采样" : "分模块自动刷新 · 遵循接口限频" : "当前场次仅手动刷新"}</span></div>
    <ScopeControls snapshot={snapshot} />
    {!selected ? <div className="lr-select-guide"><Broadcast size={32} weight="light" /><h2>选择一个直播间，开启作战视图</h2><p>请使用上方筛选选择具体场次。未选择直播间时，不请求场次数据。</p></div> : <>
      <Section title="直播主画面" icon={<Broadcast size={18} />} action={<span className="lr-muted">画面与经营数据独立更新</span>} className="lr-stage-panel">
        <div className="lr-stage-grid"><div className="lr-video">
          {room?.videoUrl && status === "live" && !demo ? <video key={room.videoUrl} controls playsInline poster={room.coverUrl ?? undefined} src={room.videoUrl} aria-label="真实直播视频流" /> : room?.coverUrl ? <div className="lr-cover" style={{ backgroundImage: `linear-gradient(0deg, #11264b99, #11264b22), url(${JSON.stringify(room.coverUrl)})` }}><span>直播封面 · 静态图片</span></div> : <div className="lr-video-empty"><span className="lr-play"><Play size={30} weight="light" /></span><h3>{status === "not_started" ? "等待直播开始" : ended ? "直播已结束" : "暂无可用直播画面"}</h3><p>{demo ? "模拟场次未连接视频流" : "尚未提供获权视频流或直播封面"}</p><span className="lr-video-caption">经营数据可独立查看</span></div>}
          <div className="lr-video-label"><span><Broadcast size={13} />{demo ? "演示场次" : "直播画面"}</span><span>{room?.videoUrl && !demo && status === "live" ? "视频源已提供" : "视频流未接入"}</span></div>
        </div><div className="lr-session"><div className="lr-session-tag">当前直播场次</div><h2>{room?.name ?? "场次数据未返回"}</h2><p>{snapshot.shops.find((shop) => shop.id === room?.shopId)?.name ?? "--"}</p><dl><div><dt>直播状态</dt><dd>{demo && status === "live" ? "演示直播中" : statusNames[status]}</dd></div><div><dt>{ended ? "本场时长" : "开播时长"}</dt><dd className="lr-duration">{liveDuration(room, status, clock)}</dd></div><div><dt>场次ID</dt><dd>{room?.id ?? "--"}</dd></div><div><dt>统计日期</dt><dd>{snapshot.scope.date} · 上海时区</dd></div></dl><div className="lr-session-note"><Info size={15} /><span>{room?.startedAt ? "时长依据接口返回的开播时间计算" : "开播时间未提供，时长暂无数据"}</span></div><ReadTime state={modules.live} module="live" status={status} now={clock} minimum={minimum} /></div></div>
      </Section>
      <Section title="实时经营指标" icon={<Waveform size={18} />} action={<span className="lr-muted">{ended ? "本场最终或最新快照" : "当前统计日 · 同店铺 / 场次 / 平台"}</span>}>
        <div className="lr-metrics">{metrics.map((item) => <article key={item.name} className={item.name === "支付GMV" ? "lr-primary-metric" : ""}><div className="lr-metric-name">{item.name}<item.icon size={16} /></div>{modules[item.module].status === "loading" ? <Skeleton label={item.name} /> : <strong>{item.value}<small>{item.value === "--" ? "" : item.unit}</small></strong>}<span>{item.detail}</span></article>)}</div>
        {live && !live.platformCovered && <p className="lr-inline-notice">所选平台未提供直播细分统计，在线人数、累计观看和订单显示 --。</p>}
        <div className="lr-metrics-times"><ReadTime state={modules.live} module="live" status={status} now={clock} minimum={minimum} /><ReadTime state={modules.commerce} module="commerce" status={status} now={clock} minimum={minimum} /></div><p className="lr-footnote">{commerce?.note ?? "经营数据获取中"}。{demo ? "所有数值为固定模拟样本，无实时增长。" : "数据时间使用来源时间，成功获取为本页请求完成时间。"}</p>
      </Section>
    </>}
    <TrendPanel selected={!!selected} range={range} setRange={setRange} metric={metric} setMetric={setMetric} />
    <Section title="AI作战指挥台" icon={<Robot size={19} />} action={<span className="lr-approval-tag"><ShieldCheck size={14} />投放变更须人工审批</span>}>
      <div className="lr-command-grid"><div className="lr-command-compose"><h3>让驭量协助你的下一步</h3><p>描述需求，核对操作对象与风险，再提交审批。</p><div className="lr-shortcuts">{["看看现在直播间情况", "检查投放ROI", "调整计划预算"].map((text, index) => <button key={text} disabled={!selected || !canCreate} onClick={() => setCommand(index === 0 ? text : index === 1 ? "查看当前直播间投放ROI" : "把预算调到5000")}><Lightning size={12} />{text}</button>)}</div>{selected && canCreate ? <CommandPanel snapshot={commandSnapshot} source="PC_MANUAL" value={command} onChange={setCommand} /> : <div className="lr-command-disabled">{!selected ? "选择直播间后可输入指令" : "当前账号没有任务创建权限"}</div>}</div><div className="lr-task-column"><div className="lr-task-summary"><div><strong>{selected && modules.tasks.lastSuccessAt ? running : "--"}</strong><span>执行 / 核验中</span></div><div><strong>{selected && modules.tasks.lastSuccessAt ? pending : "--"}</strong><span>等待人工审批</span></div></div>{selected ? modules.tasks.status === "loading" ? <Skeleton label="AI任务" /> : <TaskList snapshot={commandSnapshot} limit={3} /> : <p className="lr-task-empty">任务进度随所选直播场次同步</p>}<div className="lr-task-bottom"><ReadTime state={modules.tasks} module="tasks" status={status} now={clock} minimum={minimum} />{canTasks && <Link href="/app/tasks">全部任务与审批 <ArrowRight size={13} /></Link>}</div></div></div>
    </Section>
    <Section title="异常监控" icon={<WarningCircle size={18} />} action={<span className="lr-muted">跟随对应指标更新</span>}>
      <div className="lr-alerts">{[{ title: "流量下滑", detail: "缺少连续在线采样，暂无法判断流量变化", icon: Users }, { title: "转化异常", detail: "缺少同范围观看与成交订单，暂无法判断转化", icon: ShoppingBag }, { title: "ROI异常", detail: !selected || commerce?.roi === null || commerce?.roi === undefined || roiTarget === undefined ? "当前范围缺少ROI或经营目标" : `当前 ${commerce.roi.toFixed(2)} / 目标 ${roiTarget.toFixed(2)}；${roiWarning ? "低于经营目标，建议核查" : "达到当前经营目标"}`, icon: ChartLineUp }].map((item, index) => <article key={item.title} className={index === 2 && roiWarning ? "lr-alert-warning" : ""}><item.icon size={20} /><div><h3>{item.title}<span>{index === 2 && selected && commerce?.roi !== null && commerce?.roi !== undefined && roiTarget !== undefined ? roiWarning ? "待关注" : "已对照" : "待数据"}</span></h3><p>{item.detail}</p><small>{index === 2 ? `来源更新 ${formatLiveTime(modules.commerce.updatedAt)}${delayed ? " · 数据更新延迟" : ""}` : "时间序列 / 转化数据接口待接入"}</small></div></article>)}</div>
    </Section>
    <Section title="关联投放计划" icon={<Lightning size={18} />} action={<Link className="lr-text-link" href="/app/plans">投放管理 <ArrowRight size={13} /></Link>}>
      {!selected ? <p className="lr-table-empty">选择直播间后查看关联计划</p> : modules.plans.status === "loading" ? <Skeleton label="关联投放计划" /> : <div className="lr-table-scroll"><table><thead><tr><th>计划名称 / 平台</th><th>日预算</th><th>广告消耗</th><th>投放ROI</th><th>计划状态</th><th>数据更新时间</th><th>操作</th></tr></thead><tbody>{plans.map((plan) => { const account = snapshot.adAccounts.find((a) => a.id === plan.adAccountId); return <tr key={plan.id}><td><b>{plan.name}</b><small>{account ? connectorNames[account.connectorId] : "--"} · {plan.nativePlanId}</small></td><td>{money(plan.budgetMinor)}</td><td>{money(plan.spendMinor)}</td><td>{planRoi(plan.paidGmvMinor, plan.spendMinor)}</td><td><span className={`lr-plan-status ${plan.enabled ? "enabled" : ""}`}>{demo ? "模拟 · " : ""}{plan.enabled ? "投放开启" : "投放暂停"}</span></td><td>{formatLiveTime(plan.sourceUpdatedAt)}</td><td><button disabled={!canCreate} onClick={() => { setCommand(`调整${plan.name}的日预算`); document.querySelector<HTMLTextAreaElement>('.live-room textarea[aria-label="自然语言指令"]')?.focus(); }}>申请操作</button></td></tr>; })}</tbody></table>{!plans.length && <p className="lr-table-empty">当前店铺、直播场次及平台下暂无关联计划</p>}</div>}
      <ReadTime state={modules.plans} module="plans" status={status} now={clock} minimum={minimum} />
    </Section>
    {selected && <details className="lr-data-details lr-evidence"><summary><Play size={15} />回放时间轴与直播货盘</summary><Replay snapshot={scopedEvidence(commandSnapshot)} /><Goods snapshot={scopedEvidence(commandSnapshot)} /></details>}
    <details className="lr-data-details"><summary><Info size={15} />数据接入与刷新说明</summary><p>状态、请求状态与数据来源分别管理。现有接口为整包快照，到期模块共用一次读取；单个模块更新不会清空页面。</p><ul>{(Object.keys(moduleNames) as LiveModule[]).map((key) => <li key={key}>{moduleNames[key]}：建议 {liveCadence[key] / 1000}秒；{key === "trends" ? "缺少时间序列，不发起空轮询" : !automatic ? "正式接口限频未确认，自动轮询关闭" : `实际间隔至少 ${Math.max(liveCadence[key], minimum) / 1000}秒`}；过期阈值 {Math.max(configuration.maxAgeMs[key], minimum * 2) / 1000}秒。</li>)}</ul><p>平台更新使用数据来源时间；最近成功同步使用本页成功接收时间。汇总平台时间取已展示来源中最早的时间，任一来源未知则显示 --。任务时间指状态变更，任务新鲜度以最近成功同步判断。</p><p>趋势、视频流、流量与转化判定接口待接入。字段及限频约定见交接文档；无字段时显示 --，模拟数据不会自动增长。</p></details>
  </div>;
}
