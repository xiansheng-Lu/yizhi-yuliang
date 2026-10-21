import { ApiClient } from "./api-client";
import { browserMockAdapter } from "./mock-adapter";
import { ServiceError, type FrontendService } from "./contracts";
export function createFrontendService(): FrontendService {
  const mode = process.env.NEXT_PUBLIC_DATA_MODE ?? "mock";
  if (mode === "mock") return browserMockAdapter();
  if (mode === "api")
    return new ApiClient(process.env.NEXT_PUBLIC_API_BASE_URL ?? "");
  throw new ServiceError("API_NOT_CONFIGURED", "数据模式只能为 mock 或 api。");
}
