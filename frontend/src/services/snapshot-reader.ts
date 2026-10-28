import type { FrontendSnapshot } from "../lib/domain";
import { ServiceError, type FrontendService } from "./contracts";
import { liveRefreshConfiguration } from "../lib/live-room";

type Read = { epoch: number; controller: AbortController; promise: Promise<FrontendSnapshot>; consumers: number; settled: boolean };
type Entry = { epoch: number; active?: Read; lastStartedAt: number; retryAt: number };
const entries = new WeakMap<FrontendService, Entry>();
const receipts = new WeakMap<FrontendSnapshot, number>();
export function snapshotReceivedAt(snapshot: FrontendSnapshot) { return receipts.get(snapshot) ?? null; }
interface ReadClock {
  now(): number;
  setTimer(callback: () => void, ms: number): ReturnType<typeof setTimeout>;
  clearTimer(timer: ReturnType<typeof setTimeout>): void;
}
const realtime: ReadClock = { now: () => Date.now(), setTimer: (callback, ms) => setTimeout(callback, ms), clearTimer: (timer) => clearTimeout(timer) };
function entry(service: FrontendService) {
  let value = entries.get(service);
  if (!value) { value = { epoch: 0, lastStartedAt: 0, retryAt: 0 }; entries.set(service, value); }
  return value;
}
/** Invalidate the result, not the transport lock. New scopes wait for an old
 * read to settle; multiple components never overlap snapshot requests. */
export function invalidateSnapshotRead(service: FrontendService) { entry(service).epoch++; }
function pause(ms: number, signal: AbortSignal, time: ReadClock) {
  return new Promise<void>((resolve, reject) => {
    const abort = () => { time.clearTimer(timer); reject(signal.reason); };
    const timer = time.setTimer(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}
function wait<T>(promise: Promise<T>, signal?: AbortSignal, release = () => {}) {
  return new Promise<T>((resolve, reject) => {
    let done = false;
    const finish = (callback: () => void) => {
      if (done) return;
      done = true; signal?.removeEventListener("abort", abort); release(); callback();
    };
    const abort = () => finish(() => reject(signal?.reason ?? new DOMException("Cancelled", "AbortError")));
    promise.then((value) => finish(() => resolve(value)), (error) => finish(() => reject(error)));
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}
export function readSnapshot(service: FrontendService, options: { signal?: AbortSignal; clock?: ReadClock } = {}): Promise<FrontendSnapshot> {
  if (options.signal?.aborted) return Promise.reject(options.signal.reason);
  const state = entry(service);
  const time = options.clock ?? realtime;
  const active = state.active;
  if (active && (active.epoch !== state.epoch || active.controller.signal.aborted)) {
    return wait(active.promise.catch(() => undefined), options.signal).then(() => readSnapshot(service, options));
  }
  if (!active) {
    const controller = new AbortController();
    const request: Read = { epoch: state.epoch, controller, consumers: 0, settled: false,
      promise: Promise.resolve().then(async () => {
        const minimum = service.mode === "api" ? liveRefreshConfiguration().snapshotMinimumMs : 0;
        const delay = Math.max(minimum > 0 ? state.lastStartedAt + minimum : 0, state.retryAt) - time.now();
        if (delay > 0) await pause(delay, controller.signal, time);
        controller.signal.throwIfAborted();
        state.lastStartedAt = time.now();
        return service.getSnapshot({ signal: controller.signal });
      }).then((snapshot) => {
        receipts.set(snapshot, time.now()); return snapshot;
      }).catch((error) => {
        if (error instanceof ServiceError && error.retryAfterSeconds)
          state.retryAt = Math.max(state.retryAt, time.now() + error.retryAfterSeconds * 1000);
        throw error;
      }).finally(() => { request.settled = true; if (state.active === request) state.active = undefined; }) };
    state.active = request;
  }
  const request = state.active!;
  request.consumers++;
  return wait(request.promise, options.signal, () => {
    request.consumers--;
    // One view unmounting must not cancel a shared provider read.
    if (!request.consumers && !request.settled) request.controller.abort();
  });
}
