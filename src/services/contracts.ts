import type { FrontendSnapshot, Identity, RiskPolicy } from "@/lib/domain";
import type {
  CommandJob,
  CreateJobRequest,
  JobVersionRequest,
  CommandInterpretation,
  MockScenario,
  ReportingScope,
} from "@/lib/command-job";
export type DataMode = "mock" | "api";
export type ErrorCode =
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "NO_DATA"
  | "API_NOT_CONFIGURED"
  | "NETWORK_ERROR"
  | "TIMEOUT"
  | "INVALID_RESPONSE"
  | "INVALID_INPUT"
  | "CONFLICT";
export class ServiceError extends Error {
  constructor(
    public code: ErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "ServiceError";
  }
}
export interface LoginRequest {
  email: string;
  password: string;
}
export interface ApiSuccess<T> {
  data: T;
  requestId: string;
  schemaVersion: "command-job-v1.0";
}
export interface ApiFailure {
  error: { code: ErrorCode; message: string };
  requestId: string;
}
export interface FrontendService {
  readonly mode: DataMode;
  getIdentity(): Promise<Identity | null>;
  login(input: LoginRequest): Promise<Identity>;
  logout(): Promise<void>;
  getSnapshot(): Promise<FrontendSnapshot>;
  interpretCommand(input: {
    text: string;
    scope: ReportingScope;
  }): Promise<CommandInterpretation>;
  createJob(
    input: CreateJobRequest,
    scenario?: MockScenario,
  ): Promise<CommandJob>;
  confirmJob(input: JobVersionRequest): Promise<CommandJob>;
  reviewJob(
    input: JobVersionRequest & { decision: "APPROVE" | "REJECT" },
  ): Promise<CommandJob>;
  cancelJob(input: JobVersionRequest): Promise<CommandJob>;
  advanceMockJob(input: JobVersionRequest): Promise<CommandJob>;
  expireMockJob(input: JobVersionRequest): Promise<CommandJob>;
  updatePolicy(input: RiskPolicy): Promise<RiskPolicy>;
  setScope(input: ReportingScope): Promise<void>;
  subscribe(onChange: () => void): () => void;
  resetDemo?(): Promise<void>;
}
export type Resource<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: ServiceError };
