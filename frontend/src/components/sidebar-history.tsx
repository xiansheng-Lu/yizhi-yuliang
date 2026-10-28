"use client";
import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ChatCircleDots, CheckCircle, ClockCounterClockwise, Folder, Plus, NotePencil } from "@phosphor-icons/react";
import { useHistory } from "./history-provider";
import { useWorkspace } from "./provider";
import { canAccessPage } from "@/lib/access";
import { conversationHref, projectHref } from "@/lib/workspace-history";

export function SidebarHistory({ collapsed, expand, onNavigate }: { collapsed: boolean; expand: () => void; onNavigate: () => void }) {
  const { identity, service, data } = useWorkspace();
  const { history, refreshHistory } = useHistory();
  const router = useRouter();
  const query = useSearchParams();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!canAccessPage(identity, "command")) return null;
  const records = history.status === "ready" ? history.data : null;
  const conversations = records?.conversations ?? [];
  const linked = new Set(conversations.flatMap((conversation) => conversation.messages.flatMap((message) => message.jobId ? [message.jobId] : [])));
  const tasks = data.status === "ready" ? data.data.tasks.filter((job) => !linked.has(job.job_id)) : [];
  const recent = [
    ...conversations.map((conversation) => ({ id: conversation.id, title: conversation.title, href: conversationHref(conversation.id), at: conversation.updatedAt, task: false })),
    ...tasks.map((job) => ({ id: job.job_id, title: job.intent, href: `/app/command?task=${encodeURIComponent(job.job_id)}`, at: job.updated_at, task: true })),
  ].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!service || busy || !name.trim()) return;
    setBusy(true); setError("");
    try {
      const project = await service.createProject({ name });
      await refreshHistory(); setName(""); setCreating(false); onNavigate(); router.push(projectHref(project.id));
    } catch (error) { setError(error instanceof Error ? error.message : "项目创建失败，请重试。"); }
    finally { setBusy(false); }
  }
  if (collapsed) return <div className="sidebar-history history-compact"><button aria-label="展开项目列表" title="项目" onClick={expand}><Folder size={21} /></button><button aria-label="展开最近对话" title="最近对话" onClick={expand}><ClockCounterClockwise size={21} /></button></div>;
  return <div className="sidebar-history">
    <div className="history-section-title"><span>项目</span><button aria-label="新建项目" title="新建项目" onClick={() => { setCreating(!creating); setError(""); }}><Plus size={15} /></button></div>
    {creating && <form className="new-project-form" onSubmit={createProject}><input autoFocus aria-label="项目名称" placeholder="输入项目名称" required maxLength={40} value={name} disabled={busy} onChange={(event) => setName(event.target.value)} /><div><button type="button" onClick={() => setCreating(false)}>取消</button><button type="submit" disabled={busy || !name.trim()}>{busy ? "创建中…" : "创建"}</button></div></form>}
    {error && <p className="history-error" role="alert">{error}</p>}
    {records?.projects.map((project) => <Link className={`history-project ${query.get("project") === project.id ? "selected" : ""}`} key={project.id} href={projectHref(project.id)} onClick={onNavigate} title={project.name}><Folder size={15} /><span>{project.name}</span></Link>)}
    {records && !records.projects.length && !creating && <p className="history-empty">用项目归拢对话和任务</p>}
    <div className="history-section-title"><span>最近对话</span><Link href="/app/command" aria-label="新建对话" title="新建对话" onClick={onNavigate}><NotePencil size={15} /></Link></div>
    {history.status === "loading" && <p className="history-empty" role="status">正在读取记录…</p>}
    {history.status === "error" && <div className="history-error" role="alert"><p>对话记录暂时无法加载</p><button onClick={() => void refreshHistory()}>重新加载</button></div>}
    {recent.map((item) => <Link className={`history-record ${query.get(item.task ? "task" : "conversation") === item.id ? "selected" : ""}`} href={item.href} key={`${item.task ? "task" : "chat"}-${item.id}`} title={item.title} onClick={onNavigate}>{item.task ? <CheckCircle size={14} /> : <ChatCircleDots size={14} />}<span>{item.title}</span></Link>)}
    {records && !recent.length && <p className="history-empty">发送第一条消息后，记录会保存在这里</p>}
  </div>;
}
