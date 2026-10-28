"use client";
import { useState, type FormEvent } from "react";
import { actionAvailability, redact } from "@/lib/delivery";
import { useDelivery } from "./context";
import { Gate, Json, Modal } from "./ui";
/** Generic approvals keep their own resource version, independent of task and
 * allocation versions. Legacy command_job approvals remain on the old API. */
export function ApprovalReview({
  approvalId,
  accountId,
  budgetId,
  executionId,
  initial = "APPROVE",
  close,
}: {
  approvalId: string | null;
  accountId: string;
  budgetId?: string;
  executionId?: string;
  initial?: "APPROVE" | "REJECT";
  close: () => void;
}) {
  const d = useDelivery();
  const approval = d.data.approvals.find(
    (a) =>
      a.id === approvalId &&
      a.accountId === accountId &&
      (!budgetId || a.budgetId === budgetId) &&
      (!executionId || a.executionId === executionId),
  );
  const saved = d.draft as {
    kind?: string;
    approvalId?: string;
    decision?: "APPROVE" | "REJECT";
    comment?: string;
  } | null;
  const restore = saved?.kind === "APPROVAL" && saved.approvalId === approvalId;
  const [decision, setDecision] = useState(
      restore ? saved!.decision! : initial,
    ),
    [comment, setComment] = useState(restore ? (saved!.comment ?? "") : ""),
    [preview, setPreview] = useState(false);
  const rule = actionAvailability(d.data, d.identity, accountId, "review");
  const ready =
    !!approval &&
    approval.status === "PENDING" &&
    Number.isInteger(approval.version) &&
    approval.version > 0 &&
    approval.before !== undefined &&
    approval.after !== undefined;
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready || !approval) return;
    if (!preview) {
      setPreview(true);
      return;
    }
    await d.mutate(
      accountId,
      "review",
      () =>
        d.api.approval(approval.id, {
          expectedVersion: approval.version,
          decision,
          comment,
          idempotencyKey: crypto.randomUUID(),
        }),
      close,
    );
  }
  return (
    <Modal title="审批申请与执行交接" close={() => d.guard(close)}>
      <form className="dw-form" onSubmit={submit}>
        <p>
          审批资源 {approvalId ?? "未返回"} · 版本{" "}
          {approval?.version ?? "未提供"}
        </p>
        {!ready && (
          <p className="dw-notice">
            尚未取得属于此对象、待处理且具备前后参数和版本的审批资源，不能提交审批。
          </p>
        )}
        <div className="dw-param-comparison">
          <div>
            <h3>调整前</h3>
            <Json value={approval?.before} />
          </div>
          <div>
            <h3>调整后</h3>
            <Json value={approval?.after} />
          </div>
        </div>
        <p>申请原因：{String(redact(approval?.reason ?? "未提供"))}</p>
        <label>
          审批结论
          <select
            value={decision}
            onChange={(e) => {
              setDecision(e.target.value as typeof decision);
              setPreview(false);
              d.markDirty(true);
              d.saveDraft({
                kind: "APPROVAL",
                approvalId,
                decision: e.target.value,
                comment,
              });
            }}
          >
            <option value="APPROVE">同意并交由后端处理</option>
            <option value="REJECT">驳回申请</option>
          </select>
        </label>
        <label>
          审批意见
          <textarea
            required
            value={comment}
            onChange={(e) => {
              setComment(e.target.value);
              setPreview(false);
              d.markDirty(true);
              d.saveDraft({
                kind: "APPROVAL",
                approvalId,
                decision,
                comment: e.target.value,
              });
            }}
          />
        </label>
        {preview && (
          <p className="dw-notice">
            确认{decision === "APPROVE" ? "同意" : "驳回"}审批 v
            {approval?.version}
            。同意内部方案不代表平台预算已修改，投放变更仍需执行及回读核验。
          </p>
        )}
        <Gate capability={rule} />
        <button
          className="button primary"
          disabled={
            !ready ||
            d.busy ||
            d.uncertain ||
            (preview && rule.status !== "AUTHORIZED")
          }
        >
          {preview ? "确认审批结论" : "预览审批结论"}
        </button>
      </form>
    </Modal>
  );
}
