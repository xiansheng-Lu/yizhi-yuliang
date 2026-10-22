"use client";
import { useState } from "react";
import { Robot } from "@phosphor-icons/react";
import { actionAvailability } from "@/lib/delivery";
import type { AnalysisRequest } from "@/services/delivery-service";
import { useDelivery } from "./context";
import { Gate, Json, Modal } from "./ui";
export function AnalyzeButton({
  kind,
  ids,
  label,
  accountId,
}: {
  kind: AnalysisRequest["kind"];
  ids: string[];
  label: string;
  accountId?: string;
}) {
  const d = useDelivery();
  const [open, setOpen] = useState(false),
    [result, setResult] = useState<string | null>(null);
  const account = accountId ?? d.scope.accountId;
  const cap = actionAvailability(d.data, d.identity, account, "analyze");
  return (
    <>
      <button className="button secondary" onClick={() => setOpen(true)}>
        <Robot size={15} />
        {label}
      </button>
      {open && (
        <Modal title={label} close={() => setOpen(false)}>
          <p>
            分析仅使用当前范围内选中的真实业务对象，观察模式不执行投放写入。
          </p>
          <Json
            value={{ scope: d.scope, analysisType: kind, objectIds: ids }}
          />
          {!ids.length && (
            <p className="dw-notice">当前未选中可分析的真实业务对象。</p>
          )}
          <Gate capability={cap} />
          <button
            className="button primary"
            disabled={
              !ids.length ||
              cap.status !== "AUTHORIZED" ||
              d.busy
            }
            onClick={() =>
              void d.mutate(
                account,
                "analyze",
                () =>
                  d.api.analyze({
                    scope: d.scope,
                    kind,
                    objectIds: ids,
                    idempotencyKey: crypto.randomUUID(),
                  }),
                (value) => setResult(`${value.state} · ${value.resourceId}`),
              )
            }
          >
            确认创建分析任务
          </button>
          {result && <p role="status">分析任务：{result}</p>}
        </Modal>
      )}
    </>
  );
}
