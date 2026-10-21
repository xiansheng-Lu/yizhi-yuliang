"use client";
import { ErrorState } from "@/components/ui";
import { ServiceError } from "@/services/contracts";
export default function FrontendError({
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  return (
    <ErrorState
      error={
        new ServiceError(
          "INVALID_RESPONSE",
          "页面数据无法显示，请重新加载；若持续出现，请核对后端契约。",
        )
      }
      retry={reset}
    />
  );
}
