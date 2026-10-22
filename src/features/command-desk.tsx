"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, Broadcast, ChartLineUp, CheckCircle, Clock, FilmStrip, Lightning, Robot, ShieldCheck, TrendUp } from "@phosphor-icons/react";
import type { FrontendSnapshot } from "@/lib/domain";
import { pageName } from "@/lib/routes";
import { displayTime, jobStatusNames } from "@/lib/command-job";
import { scopedJobs } from "@/lib/reporting";
import { CommandPanel } from "@/components/command-panel";
import { Badge } from "@/components/ui";
import { BrandMark } from "@/components/brand-logo";

const capabilities = [
  { name: "内容生产", icon: FilmStrip, text: "为秋茶新品策划一组短视频投流素材", href: "/app/planning", note: "从创意到投放素材" },
  { name: "自主投放", icon: Lightning, text: "把千川A计划日预算调到5000", href: "/app/plans", note: "目标、预算与执行" },
  { name: "直播运营", icon: Broadcast, text: "看看现在直播间情况", href: "/app/monitor", note: "直播、货盘与话术" },
  { name: "经营分析", icon: ChartLineUp, text: "分析今天各平台的消耗与支付GMV", href: "/app/analytics", note: "指标与经营洞察" },
  { name: "经营复盘", icon: TrendUp, text: "复盘秋茶专场，整理下一轮行动", href: "/app/reports", note: "证据与下一步行动" },
] as const;

export function CommandDesk({ snapshot }: { snapshot: FrontendSnapshot }) {
  const [draft, setDraft] = useState("");
  const [capability, setCapability] = useState(0);
  const [composerVersion, setComposerVersion] = useState(0);
  useEffect(() => {
    const reset = () => {
      setDraft("");
      setCapability(0);
      setComposerVersion((previous) => previous + 1);
    };
    window.addEventListener("yuliang:new-command", reset);
    return () => window.removeEventListener("yuliang:new-command", reset);
  }, []);
  const jobs = scopedJobs(snapshot);
  const pending = jobs.filter((job) => job.status === "PENDING_APPROVAL");
  const autonomous = jobs.filter((job) => job.source === "AUTOPILOT");
  const active = jobs.filter((job) => ["EXECUTING", "VERIFYING"].includes(job.status));
  const completed = jobs.filter((job) => job.status === "SUCCEEDED");
  return (
    <div className="command-desk">
      <div className="desk-titlebar"><div><span className="eyebrow">YOUR AI OPERATIONS PARTNER</span><h1>{pageName("command")}</h1></div><Link href="/app/goals" className="desk-goals"><ShieldCheck size={15} />经营目标与边界<ArrowUpRight size={14} /></Link></div>
      <div className="desk-grid">
        <section className="conversation-area" aria-label="AI 运营对话">
          <div className="desk-welcome"><span className="desk-ai-mark"><BrandMark /></span><div className="welcome-kicker">你好，我是你的 AI 运营伙伴</div><h2>把经营目标交给 AI，<br /><em>让增长有序发生。</em></h2><p>从一条指令开始，连接内容生产、投放执行与经营复盘。</p></div>
          <div className="capability-tabs" role="tablist" aria-label="AI 运营能力">{capabilities.map((item, index) => <button key={item.name} role="tab" aria-selected={capability === index} aria-controls="capability-prompt" id={`capability-${index}`} onClick={() => {setCapability(index); setDraft(item.text);}}><item.icon size={17} />{item.name}</button>)}</div>
          <div id="capability-prompt" className="capability-prompt" role="tabpanel" aria-labelledby={`capability-${capability}`}><span>{capabilities[capability].note}</span><Link href={capabilities[capability].href}>进入专业工作区<ArrowUpRight size={14} /></Link></div>
          <CommandPanel key={composerVersion} snapshot={snapshot} source="PC_MANUAL" desk value={draft} onChange={setDraft} />
          <div className="prompt-shortcuts"><span>试着说</span><button onClick={() => setDraft("看看现在直播间情况")}>看看直播间情况<ArrowUpRight size={12} /></button><button onClick={() => setDraft("把千川A计划日预算调到5000")}>调整计划预算<ArrowUpRight size={12} /></button><button onClick={() => setDraft("把千川A计划ROI目标调到6")}>优化 ROI 目标<ArrowUpRight size={12} /></button></div>
          <Link className="overview-entry" href="/app/overview"><span className="row"><ChartLineUp size={19} /><span><b>经营总览</b><small>广告消耗、支付 GMV 与投放 ROI · 同一统计口径</small></span></span><ArrowUpRight size={17} /></Link>
          <div className="recent-work"><div className="row between"><h2>最近的运营任务</h2><Link href="/app/tasks">全部任务<ArrowUpRight size={13} /></Link></div>{jobs.length ? jobs.slice(0,3).map((job) => <Link href="/app/tasks" key={job.job_id} className="recent-job"><span className="recent-job-icon">{job.source === "AUTOPILOT" ? <Robot size={18} /> : <CheckCircle size={18} />}</span><div><b>{job.intent}</b><small>{job.source === "AUTOPILOT" ? "AI自主运营" : job.source === "MOBILE_COMMAND" ? "手机自然语言指挥" : "人工运营指挥"} · {displayTime(job.created_at)}</small></div><Badge tone={job.status === "SUCCEEDED" ? "green" : "blue"}>{jobStatusNames[job.status]}</Badge></Link>) : <p className="desk-empty">还没有运营任务，发送一条指令开始。</p>}</div>
        </section>
        <aside className="operations-rail" aria-label="自主运营与待办">
          <div className="autonomy-panel"><div className="row between"><span className="row"><Robot size={21} /><b>AI 自主运营</b></span><Badge>{snapshot.source}</Badge></div><div className="autonomy-state"><span /><b>{active.length ? `${active.length} 个任务模拟执行中` : "等待目标与任务"}</b></div><p>人工指挥与 AI 自主候选，<br />在同一条任务链中协作。</p><div className="autonomy-counters"><div><strong>{autonomous.length}</strong><span>自主任务</span></div><div><strong>{active.length}</strong><span>执行与核验</span></div><div><strong>{completed.length}</strong><span>核验完成</span></div></div><Link href="/app/goals">配置自主运营目标<ArrowRight size={15} /></Link></div>
          <section className="desk-todo"><div className="row between"><h2>需要你确认</h2><span className="todo-number">{pending.length}</span></div>{pending.length ? pending.slice(0,2).map((job) => <Link href="/app/tasks" className="todo-item" key={job.job_id}><span className="row"><Clock size={14} />待审批</span><b>{job.intent}</b><small>查看变更与风险<ArrowRight size={13} /></small></Link>) : <div className="todo-clear"><CheckCircle size={25} /><b>暂时没有待审批任务</b><p>新的操作申请将在这里等待你确认。</p></div>}<Link className="rail-link" href="/app/tasks">打开任务与审批<ArrowUpRight size={14} /></Link></section>
          <section className="workflow-rail"><h2>每一步，都有迹可循</h2>{[{title:"明确经营目标",text:"范围、预算与风控边界"},{title:"生成标准任务",text:"人工指令 / AI 自主候选"},{title:"确认与审批",text:"逐项查看变更和风险"},{title:"执行、核验与复盘",text:"回读匹配后才算完成"}].map((item,index) => <div className="rail-step" key={item.title}><span>{index + 1}</span><div><b>{item.title}</b><small>{item.text}</small></div></div>)}</section>
          <div className="rail-note"><ShieldCheck size={17} /><p>当前未连接真实 AI 与广告平台。自主候选不会跳过审批；演示状态仅在同源浏览器内共享。</p></div>
        </aside>
      </div>
    </div>
  );
}
