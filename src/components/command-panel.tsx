"use client";
import { useRef, useState, type FormEvent } from "react";
import {
  type CommandInterpretation,
  type JobSource,
  type MockScenario,
} from "@/lib/command-job";
import type { FrontendSnapshot } from "@/lib/domain";
import { useWorkspace } from "./provider";
import { JobDetails, MockScenarioSelect } from "./task-controls";
import { LiveSummary } from "./operating-context";
import Link from "next/link";
import { ArrowUp, Sparkle } from "@phosphor-icons/react";

export function CommandPanel({
  snapshot,
  source,
  value,
  onChange,
  desk = false,
}: {
  snapshot: FrontendSnapshot;
  source: JobSource;
  value?: string;
  onChange?: (value: string) => void;
  desk?: boolean;
}) {
  const { service, reload } = useWorkspace();
  const [localText, setLocalText] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const text = value ?? localText;
  function setText(next: string) {
    setLocalText(next);
    onChange?.(next);
  }
  const [result, setResult] = useState<{
    interpretation: CommandInterpretation;
    scope: string;
    intent: string;
  } | null>(null);
  const submission = useRef<{ signature: string; key: string } | null>(null);
  const [target, setTarget] = useState(""),
    [created, setCreated] = useState<string | null>(null),
    [scenario, setScenario] = useState<MockScenario>("SUCCESS");
  const scopeKey = JSON.stringify(snapshot.scope);
  const interpretation =
    result?.scope === scopeKey && result.intent === text.trim() ? result.interpretation : null;
  const job =
    result?.scope === scopeKey && result.intent === text.trim()
      ? snapshot.tasks.find((j) => j.job_id === created)
      : undefined;
  async function parse(e: FormEvent) {
    e.preventDefault();
    if (!service || busy) return;
    setBusy(true);
    setError("");
    setCreated(null);
    setResult(null);
    setTarget("");
    submission.current = null;
    try {
      const parsed = await service.interpretCommand({
        text,
        scope: snapshot.scope,
      });
      setResult({
        interpretation: parsed,
        scope: scopeKey,
        intent: text.trim(),
      });
      if (parsed.kind === "ACTION" && parsed.candidate_ids.length === 1)
        setTarget(parsed.candidate_ids[0]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "指令解析未完成");
    } finally {
      setBusy(false);
    }
  }
  async function create() {
    if (!service || busy || interpretation?.kind !== "ACTION" || !target)
      return;
    const object = (
      interpretation.target_type === "AD_PLAN"
        ? snapshot.plans
        : snapshot.liveSessions
    ).find((x) => x.id === target);
    if (!object || !interpretation.candidate_ids.includes(target)) return;
    setBusy(true);
    setError("");
    try {
      const signature = JSON.stringify([
        source,
        target,
        interpretation.action_type,
        interpretation.new_value,
        result?.intent,
        scenario,
      ]);
      if (submission.current?.signature !== signature)
        submission.current = { signature, key: crypto.randomUUID() };
      const job = await service.createJob(
        {
          source,
          shop_id: object.shopId,
          target_type: interpretation.target_type,
          target_id: target,
          action_type: interpretation.action_type,
          new_value: interpretation.new_value,
          intent: result!.intent,
          idempotency_key: submission.current.key,
        },
        scenario,
      );
      setCreated(job.job_id);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "任务创建未完成");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className={`command-panel ${desk ? "desk-command" : ""}`}>
      <form className="task-form" onSubmit={parse}>
        <label>
          {desk ? "告诉驭量，你想完成什么？" : "自然语言指令"}
          <textarea
            aria-label="自然语言指令"
            required
            disabled={busy}
            maxLength={500}
            onKeyDown={(e) => {
              if (desk && e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                if (text.trim() && !busy) e.currentTarget.form?.requestSubmit();
              }
            }}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setResult(null);
            }}
            placeholder={desk ? "描述你的经营目标，或试试：把千川A计划日预算调到5000…" : "看看现在直播间情况；或：把千川A计划日预算调到5000"}
          />
        </label>
        <div className="command-compose-footer">
          {desk && <span><Sparkle size={15} /> AI 运营协作 <i>·</i> {service?.mode === "mock" ? "Mock 规则解析" : "API 解析"}</span>}
          <button className="button primary" disabled={busy || !text.trim()}>
            {busy ? "处理中…" : desk ? "发送指令" : "分析指令"}
            {desk && <ArrowUp size={17} weight="bold" />}
          </button>
        </div>
        <p className="command-hint">
          {service?.mode === "mock"
            ? "Mock 规则解析，仅支持明确预算、出价、ROI、计划启停及直播交接指令；真实 AI 解析待后端。"
            : "由后端解析指令，前端仅展示候选对象与待确认操作。"}
        </p>
      </form>
      {interpretation?.kind === "UNRECOGNIZED" && (
        <div role="status" className="notice command-guide">
          <b>需求已收到 · Mock 能力说明</b>
          <p>{interpretation.text}</p>
          {desk && <><p>当前自然语言演示支持计划预算、出价、ROI、启停与直播查询。其他运营需求可继续进入对应工作区；尚未调用生成或投放服务。</p><div className="row"><Link href="/app/planning">内容生产 →</Link><Link href="/app/plans">自主投放 →</Link><Link href="/app/monitor">直播运营 →</Link><Link href="/app/analytics">经营分析 →</Link><Link href="/app/reports">经营复盘 →</Link></div></>}
        </div>
      )}
      {interpretation?.kind === "QUERY" && (
        <LiveSummary
          snapshot={snapshot}
          liveId={interpretation.live_session_id}
        />
      )}
      {interpretation?.kind === "ACTION" && !job && (
        <div className="task-form command-result">
          {interpretation.candidate_ids.length > 1 && (
            <p role="status" className="notice">
              存在多个可操作对象，请明确选择计划或直播场次。系统不会猜测目标。
            </p>
          )}
          {!interpretation.candidate_ids.length ? (
            <p role="status">
              当前范围没有匹配对象，请检查店铺、直播间或平台。
            </p>
          ) : (
            <>
              <label>
                确认操作对象
                <select
                  aria-label="确认操作对象"
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                >
                  <option value="">请选择具体对象</option>
                  {interpretation.candidate_ids.map((id) => {
                    const o = (
                      interpretation.target_type === "AD_PLAN"
                        ? snapshot.plans
                        : snapshot.liveSessions
                    ).find((x) => x.id === id);
                    return (
                      <option key={id} value={id}>
                        {o?.name ?? id} ·{" "}
                        {snapshot.shops.find((s) => s.id === o?.shopId)?.name}
                      </option>
                    );
                  })}
                </select>
              </label>
              <MockScenarioSelect value={scenario} onChange={setScenario} />
              <button
                className="button secondary"
                disabled={!target || busy}
                onClick={() => void create()}
              >
                创建标准任务，查看变更与风险
              </button>
            </>
          )}
        </div>
      )}
      {job && (
        <div className="created-job" aria-label="指令任务确认">
          <JobDetails
            job={job}
            snapshot={snapshot}
            review={source !== "MOBILE_COMMAND"}
          />
        </div>
      )}
      {result && result.scope !== scopeKey && (
        <p role="status" className="notice">
          统计范围已改变，请重新分析指令以确认目标。
        </p>
      )}
      {error && (
        <p role="alert" className="feedback">
          {error}
        </p>
      )}
    </div>
  );
}
