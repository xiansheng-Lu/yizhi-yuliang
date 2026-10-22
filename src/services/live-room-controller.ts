import type { FrontendSnapshot } from "../lib/domain";
import type { ReportingScope } from "../lib/command-job";
import { liveCadence, liveStatus, scopeKey, selectedRoom, selectLiveModules, type LiveModule, type LiveModules, type ModuleState } from "../lib/live-room";
import { ServiceError, type FrontendService } from "./contracts";
import { readSnapshot, snapshotReceivedAt } from "./snapshot-reader";

const keys: LiveModule[] = ["live", "commerce", "tasks", "plans"];
export interface Clock {
  now(): number;
  setTimer(callback: () => void, ms: number): ReturnType<typeof setTimeout>;
  clearTimer(timer: ReturnType<typeof setTimeout>): void;
}
const clock: Clock = { now: () => Date.now(), setTimer: (callback, ms) => setTimeout(callback, ms), clearTimer: (timer) => clearTimeout(timer) };
const endpointWindows = new WeakMap<FrontendService, { next: number; backoff: number }>();
export interface LivePollingPolicy {
  /** Only set when backend owners have confirmed the snapshot endpoint limit. */
  snapshotMinimumMs: number;
}
export function initialLiveModules(selected: boolean): LiveModules {
  const initial = (): ModuleState<never> => ({ data: null, status: selected ? "loading" : "idle", refreshing: false,
    error: null, updatedAt: null, lastSuccessAt: null, failures: 0, nextDue: 0 });
  return { live: initial(), commerce: initial(), tasks: initial(), plans: initial(),
    trends: { ...initial(), status: selected ? "unavailable" : "idle" } };
}
/** The existing service exposes ONE snapshot endpoint, so due modules share ONE
 * transport read. No speculative endpoints or new business actions are added. */
export class LiveRoomController {
  state: LiveModules;
  private timer?: ReturnType<typeof setTimeout>;
  private stopped = true;
  private visible = true;
  private running: Promise<void> | null = null;
  private endpoint: { next: number; backoff: number };
  private get backoffUntil() { return this.endpoint.backoff; }
  private set backoffUntil(value: number) { this.endpoint.backoff = value; }
  private get endpointNext() { return this.endpoint.next; }
  private set endpointNext(value: number) { this.endpoint.next = value; }
  private latest: FrontendSnapshot;
  private suspended = false;
  private request?: AbortController;
  private generation = 0;
  private manualPending = false;
  readonly minimumMs: number;
  readonly automatic: boolean;
  constructor(private service: FrontendService, snapshot: FrontendSnapshot,
    readonly scope: ReportingScope, private changed: (state: LiveModules) => void,
    policy?: LivePollingPolicy, private time: Clock = clock) {
    this.endpoint = endpointWindows.get(service) ?? { next: 0, backoff: 0 };
    endpointWindows.set(service, this.endpoint);
    this.latest = snapshot;
    this.state = initialLiveModules(!!selectedRoom(snapshot, scope));
    this.minimumMs = service.mode === "mock" ? 0 : policy && Number.isFinite(policy.snapshotMinimumMs) && policy.snapshotMinimumMs > 0 ? policy.snapshotMinimumMs : 0;
    this.automatic = service.mode === "mock" || this.minimumMs > 0;
    // Bootstrap is already a valid read. Keep its values if the immediate
    // refresh fails, without inventing a local successful-sync timestamp.
    if (selectedRoom(snapshot, scope) && snapshot.source === (service.mode === "mock" ? "MOCK" : "API")) {
      const initial = selectLiveModules(snapshot, scope);
      for (const key of keys) this.state[key] = { ...this.state[key], data: initial.data[key],
        status: Array.isArray(initial.data[key]) && !initial.data[key]!.length ? "empty" : "ready",
        updatedAt: initial.timestamps[key] } as never;
    }
  }
  start(visible = true) {
    this.stopped = false;
    this.visible = visible;
    // No selected room => no session read, including manual refresh.
    if (selectedRoom(this.latest, this.scope)) {
      const received = snapshotReceivedAt(this.latest);
      // Bootstrap / scope saving already requested this same endpoint. Reuse
      // a just-arrived response instead of making a duplicate entrance read.
      if (received !== null && this.time.now() - received >= 0 && this.time.now() - received < 1000) {
        this.apply(this.latest, keys); this.schedule();
      } else void this.refresh(true);
    }
  }
  stop() {
    this.stopped = true;
    this.cancelTimer();
    this.generation++;
    this.request?.abort();
  }
  suspend() {
    this.suspended = true;
    this.cancelTimer();
    this.generation++;
    this.request?.abort();
  }
  resume() {
    if (!this.suspended || this.stopped) return;
    this.suspended = false;
    // Scope saving may have failed: recover the old view without remounting.
    void (this.running ?? Promise.resolve()).then(() => this.refresh(true));
  }
  /** Shared approval controls reload the provider. Consume that successful read
   * immediately, including ended sessions, without starting another poller. */
  acceptSnapshot(snapshot: FrontendSnapshot) {
    if (this.stopped || this.suspended || scopeKey(snapshot.scope) !== scopeKey(this.scope) ||
      snapshot.source !== (this.service.mode === "mock" ? "MOCK" : "API")) return;
    this.generation++;
    this.apply(snapshot, keys);
    this.schedule();
  }
  setVisible(visible: boolean) {
    this.visible = visible;
    this.cancelTimer();
    if (visible && !this.stopped) void this.refresh(false);
  }
  private cancelTimer() {
    if (this.timer) this.time.clearTimer(this.timer);
    this.timer = undefined;
  }
  private emit() { this.changed({ ...this.state }); }
  private schedule() {
    this.cancelTimer();
    if (this.stopped || this.suspended || this.running) return;
    const live = liveStatus(selectedRoom(this.latest, this.scope), this.latest.source) === "live";
    if (!this.manualPending && (!this.visible || !this.automatic || !live)) return;
    const due = Math.max(this.endpointNext, this.backoffUntil,
      this.manualPending ? this.time.now() : Math.min(...keys.map((key) => this.state[key].nextDue)));
    this.timer = this.time.setTimer(() => { void this.refresh(this.manualPending); }, Math.max(1, due - this.time.now()));
  }
  refresh(manual = true): Promise<void> {
    if (this.stopped || this.suspended || !selectedRoom(this.latest, this.scope)) return Promise.resolve();
    if (this.running) return this.running;
    const now = this.time.now();
    if (now < this.backoffUntil || now < this.endpointNext) { this.manualPending ||= manual; this.schedule(); return Promise.resolve(); }
    if (!manual && (!this.visible || !this.automatic || liveStatus(selectedRoom(this.latest, this.scope), this.latest.source) !== "live")) return Promise.resolve();
    const due = keys.filter((key) => manual || now >= this.state[key].nextDue);
    if (!due.length) { this.schedule(); return Promise.resolve(); }
    this.manualPending = false;
    this.cancelTimer();
    for (const key of due) this.state[key] = { ...this.state[key], refreshing: true } as never;
    const generation = this.generation;
    const request = new AbortController();
    this.request = request;
    this.running = Promise.resolve().then(() => this.load(due, generation, request.signal)).finally(() => {
      this.running = null;
      if (!this.stopped) this.schedule();
    });
    this.emit();
    return this.running;
  }
  private apply(snapshot: FrontendSnapshot, due: LiveModule[]) {
    const previous = liveStatus(selectedRoom(this.latest, this.scope), this.latest.source);
    this.latest = snapshot;
    const selected = selectLiveModules(snapshot, this.scope);
    const terminal = selected.data.live.status === "ended" || selected.data.live.status === "not_started";
    const modules = terminal || previous !== selected.data.live.status ? keys : due;
    const now = this.time.now();
    this.endpointNext = now + this.minimumMs;
    this.backoffUntil = 0;
    for (const key of modules) {
      const data = selected.data[key];
      this.state[key] = { data, status: data === null || (Array.isArray(data) && !data.length) ? "empty" : "ready",
        refreshing: false, error: null, updatedAt: selected.timestamps[key], lastSuccessAt: now,
        failures: 0, nextDue: now + Math.max(liveCadence[key], this.minimumMs) } as never;
    }
    this.emit();
  }
  private async load(due: LiveModule[], generation: number, signal: AbortSignal) {
    try {
      const snapshot = await readSnapshot(this.service, { signal, clock: this.time });
      if (this.stopped || this.suspended || generation !== this.generation) return;
      if (snapshot.source !== (this.service.mode === "mock" ? "MOCK" : "API"))
        throw new ServiceError("INVALID_RESPONSE", "数据来源不匹配，已拒绝混用模拟与真实数据。");
      if (scopeKey(snapshot.scope) !== scopeKey(this.scope))
        throw new ServiceError("CONFLICT", "筛选范围已变化，请重新选择直播间。");
      this.apply(snapshot, due);
    } catch (error) {
      if (this.stopped || this.suspended || signal.aborted || generation !== this.generation) return;
      const now = this.time.now();
      const failures = Math.max(...due.map((key) => this.state[key].failures)) + 1;
      const retryAfter = error instanceof ServiceError ? (error.retryAfterSeconds ?? 0) * 1000 : 0;
      const delay = Math.max(Math.min(300_000, 10_000 * 2 ** Math.min(failures - 1, 5)), retryAfter, this.minimumMs);
      this.backoffUntil = now + delay;
      this.endpointNext = Math.max(this.endpointNext, this.backoffUntil);
      for (const key of due) this.state[key] = { ...this.state[key], status: "error", refreshing: false, failures,
        error: error instanceof Error ? error.message : "数据获取失败", nextDue: this.backoffUntil } as never;
      this.emit();
    }
  }
  get busy() { return !!this.running; }
  get retryAt() { return Math.max(this.endpointNext, this.backoffUntil); }
}
