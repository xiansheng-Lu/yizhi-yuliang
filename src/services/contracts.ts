import type {
  CreateTaskRequest,
  CommandTask,
  FrontendSnapshot,
  Identity,
  Approval,
} from "@/lib/domain";
export type DataMode = "mock" | "api";
export type ErrorCode =
  | "UNAUTHENTICATED"
  | "FORBIDDEN"
  | "NO_DATA"
  | "API_NOT_CONFIGURED"
  | "NETWORK_ERROR"
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
  schemaVersion: "g1a-draft-1";
}
export interface ApiFailure {
  error: { code: ErrorCode; message: string };
  requestId: string;
}
export interface ApproveTaskRequest {
  taskId: string;
  expectedVersion: number;
  decision: Approval["decision"];
}
export interface FrontendService {
  readonly mode: DataMode;
  getIdentity(): Promise<Identity | null>;
  login(input: LoginRequest): Promise<Identity>;
  logout(): Promise<void>;
  getSnapshot(): Promise<FrontendSnapshot>;
  createTask(input: CreateTaskRequest): Promise<CommandTask>;
  approveTask(input: ApproveTaskRequest): Promise<Approval>;
  subscribe(onChange: () => void): () => void;
  resetDemo?(): Promise<void>;
}
export type Resource<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: ServiceError };
