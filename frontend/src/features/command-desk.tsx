"use client";
import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowUpRight, Broadcast, ChartLineUp, ChatCircleDots, FilmStrip, Folder, NotePencil } from "@phosphor-icons/react";
import type { FrontendSnapshot } from "@/lib/domain";
import { jobStatusNames } from "@/lib/command-job";
import { canAccessPage } from "@/lib/access";
import { conversationHref, projectHref, taskProjectId } from "@/lib/workspace-history";
import { CommandPanel } from "@/components/command-panel";
import { useWorkspace } from "@/components/provider";
import { useHistory } from "@/components/history-provider";
import { EmptyState, ErrorState, LoadingState } from "@/components/ui";
import { JobDetails } from "@/components/task-controls";

const categories = [
  { name: "运营协作", icon: ChatCircleDots, permission: "command", prompts: ["看看现在直播间情况", "把千川A计划日预算调到5000", "把千川A计划ROI目标调到6", "暂停千川A计划广告"] },
  { name: "内容创作", icon: FilmStrip, permission: "planning", prompts: ["为秋茶新品策划一组短视频素材", "写一份新品直播讲解提纲", "整理这周的内容选题", "为商品设计一组图文创意"] },
  { name: "经营分析", icon: ChartLineUp, permission: "analytics", prompts: ["分析今天各平台的投放表现", "复盘秋茶专场并整理下一步行动", "分析哪些素材值得继续投放", "整理本周的经营数据"] },
] as const;

export function CommandDesk({ snapshot }: { snapshot: FrontendSnapshot }) {
  const { service, identity } = useWorkspace();
  const { history, refreshHistory } = useHistory();
  const query = useSearchParams();
  const router = useRouter();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const draftKey = query.get("conversation") ?? query.get("project") ?? "new";
  const draft = drafts[draftKey] ?? "";
  const setDraft = (value: string) => setDrafts((previous) => ({ ...previous, [draftKey]: value }));
  const [category, setCategory] = useState("运营协作");
  const [moving, setMoving] = useState(false);
  const [error, setError] = useState("");
  const [jobToAdd, setJobToAdd] = useState("");
  if (history.status === "loading") return <LoadingState />;
  if (history.status === "error") return <ErrorState error={history.error} retry={() => void refreshHistory()} />;
  const records = history.data;
  const conversationId = query.get("conversation");
  const projectId = query.get("project");
  const jobId = query.get("task");
  const conversation = records.conversations.find((item) => item.id === conversationId);
  const project = records.projects.find((item) => item.id === (conversation?.projectId ?? projectId));
  const job = snapshot.tasks.find((item) => item.job_id === jobId);
  if (conversationId && !conversation) return <EmptyState title="没有找到这条对话" description="记录可能属于其他账号，请从左侧最近对话重新打开。" />;
  if (projectId && !project) return <EmptyState title="没有找到这个项目" description="请从左侧项目列表重新打开。" />;
  if (jobId && !job) return <EmptyState title="没有找到这项任务" description="任务不在当前账号的授权范围内，或数据尚未同步。" />;
  const availableCategories = categories.filter((item) => canAccessPage(identity, item.permission));
  const currentCategory = availableCategories.find((item) => item.name === category) ?? availableCategories[0];
  const inProject = project ? records.conversations.filter((item) => item.projectId === project.id) : [];
  const projectTasks = project ? snapshot.tasks.filter((item) => taskProjectId(records, item.job_id) === project.id) : [];
  const newHref = project ? projectHref(project.id) : "/app/command";
  const empty = !conversation && !project && !job;
  async function move(projectId: string | null) {
    if (!service || moving) return;
    setMoving(true); setError("");
    try {
      if (conversation) await service.moveConversation({ conversationId: conversation.id, projectId });
      else if (job) await service.assignTaskProject({ jobId: job.job_id, projectId });
      else { router.push(projectId ? projectHref(projectId) : "/app/command"); setDraft(""); }
      await refreshHistory();
    } catch (error) { setError(error instanceof Error ? error.message : "归类失败，请重试。"); }
    finally { setMoving(false); }
  }
  async function addTask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!service || !project || !jobToAdd || moving) return;
    setMoving(true); setError("");
    try { await service.assignTaskProject({ jobId: jobToAdd, projectId: project.id }); await refreshHistory(); setJobToAdd(""); }
    catch (error) { setError(error instanceof Error ? error.message : "任务归类失败，请重试。"); }
    finally { setMoving(false); }
  }
  const projectPicker = <label className="hub-project-picker"><Folder size={16} /><span className="sr-only">{job ? "任务所属项目" : "对话所属项目"}</span><select aria-label={job ? "任务所属项目" : "对话所属项目"} disabled={moving} value={job ? taskProjectId(records, job.job_id) ?? "" : project?.id ?? ""} onChange={(event) => void move(event.target.value || null)}><option value="">未归入项目</option>{records.projects.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>;
  return <div className={`hub-workspace ${empty ? "hub-empty" : "hub-active"}`}>
    {empty && <header className="hub-welcome"><h1>驭量，今天帮你做什么？</h1><p>从一个想法开始，把内容、投放和经营连起来。</p><div className="hub-categories" role="tablist" aria-label="运营能力">{availableCategories.map((item) => <button key={item.name} role="tab" id={`hub-${item.permission}`} aria-selected={currentCategory?.name === item.name} aria-controls="hub-prompts" onClick={() => setCategory(item.name)}><item.icon size={17} />{item.name}</button>)}</div></header>}
    {(conversation || project || job) && <header className="hub-thread-header"><div>{project && (conversation || job) && <span className="hub-project-name"><Folder size={15} />{project.name}</span>}<h1>{conversation?.title ?? job?.intent ?? project?.name}</h1></div><Link href={newHref} onClick={() => setDraft("")}><NotePencil size={17} />新建对话</Link></header>}
    {error && <p className="feedback" role="alert">{error}</p>}
    {conversation && <section className="hub-messages" aria-label="对话记录">{conversation.messages.map((message) => <article key={message.id} className={`hub-message message-${message.role}`}><span className="hub-message-author">{message.role === "user" ? "我" : "驭量"}</span><div><p>{message.text}</p>{message.jobId && <Link className="hub-message-job" href={`/app/command?task=${encodeURIComponent(message.jobId)}`}>查看关联任务{snapshot.tasks.find((item) => item.job_id === message.jobId) && <span>{jobStatusNames[snapshot.tasks.find((item) => item.job_id === message.jobId)!.status]}</span>}<ArrowUpRight size={14} /></Link>}</div></article>)}</section>}
    {job && <section className="hub-task-detail"><div className="hub-task-project">{projectPicker}</div><JobDetails job={job} snapshot={snapshot} /></section>}
    {project && !conversation && !job && <section className="hub-project-records"><div><h2>项目对话 <span>{inProject.length}</span></h2>{inProject.map((item) => <Link href={conversationHref(item.id)} key={item.id}><ChatCircleDots size={16} /><span>{item.title}</span><ArrowUpRight size={14} /></Link>)}{!inProject.length && <p className="hub-empty-note">在下方发送消息，开始这个项目的第一段对话。</p>}</div><div><h2>项目任务 <span>{projectTasks.length}</span></h2>{projectTasks.map((item) => <Link href={`/app/command?task=${encodeURIComponent(item.job_id)}`} key={item.job_id}><Broadcast size={16} /><span>{item.intent}</span><small>{jobStatusNames[item.status]}</small></Link>)}{!projectTasks.length && <p className="hub-empty-note">对话中创建的任务会自动归入，也可添加已有任务。</p>}<form className="hub-add-task" onSubmit={addTask}><select aria-label="加入项目的任务" value={jobToAdd} onChange={(event) => setJobToAdd(event.target.value)}><option value="">选择已有任务</option>{snapshot.tasks.filter((item) => taskProjectId(records, item.job_id) !== project.id).map((item) => <option key={item.job_id} value={item.job_id}>{item.intent}</option>)}</select><button disabled={!jobToAdd || moving}>归入项目</button></form></div></section>}
    {!job && <>
      {empty && <div className="hub-prompts" id="hub-prompts" role="tabpanel" aria-labelledby={`hub-${currentCategory?.permission}`}>{currentCategory?.prompts.map((prompt) => <button key={prompt} onClick={() => setDraft(prompt)}>{prompt}<ArrowUpRight size={13} /></button>)}</div>}
      <div className="hub-composer"><CommandPanel key={conversation?.id ?? project?.id ?? "new"} snapshot={snapshot} source="PC_MANUAL" desk conversation={conversation} projectId={project?.id} value={draft} onChange={setDraft} onRecorded={(id) => router.replace(conversationHref(id))} /><div className="hub-composer-context">{projectPicker}<span>Shift + Enter 换行</span></div></div>
      {empty && <div className="hub-suggestions"><button onClick={() => setDraft("看看现在直播间情况")}>先看看当前直播间的表现<ArrowUpRight size={14} /></button><button onClick={() => setDraft("为秋茶新品策划一组短视频素材")}>帮我梳理一份内容计划<ArrowUpRight size={14} /></button></div>}
    </>}
  </div>;
}
