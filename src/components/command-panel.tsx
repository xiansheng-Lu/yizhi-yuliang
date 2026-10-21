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

export function CommandPanel({
  snapshot,
  source,
}: {
  snapshot: FrontendSnapshot;
  source: JobSource;
}) {
  const { service, reload } = useWorkspace();
  const [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
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
    result?.scope === scopeKey ? result.interpretation : null;
  const job =
    result?.scope === scopeKey
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
    <div className="command-panel">
      <form className="task-form" onSubmit={parse}>
        <label>
          自然语言指令
          <textarea
            aria-label="自然语言指令"
            required
            disabled={busy}
            maxLength={500}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setResult(null);
            }}
            placeholder="看看现在直播间情况；或：把千川A计划日预算调到5000"
          />
        </label>
        <button className="button primary" disabled={busy}>
          {busy ? "处理中…" : "分析指令"}
        </button>
        <p className="command-hint">
          {service?.mode === "mock"
            ? "Mock 规则解析，仅支持明确预算、出价、ROI、计划启停及直播交接指令；真实 AI 解析待后端。"
            : "由后端解析指令，前端仅展示候选对象与待确认操作。"}
        </p>
      </form>
      {interpretation?.kind === "UNRECOGNIZED" && (
        <p role="status" className="notice">
          {interpretation.text}
        </p>
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
