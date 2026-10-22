import test from "node:test";
import assert from "node:assert/strict";
import { createMockSnapshot } from "../src/services/mock-data";
import { LiveRoomController, type Clock } from "../src/services/live-room-controller";
import { invalidateSnapshotRead, readSnapshot } from "../src/services/snapshot-reader";
import { isDelayed, liveRefreshConfiguration, liveStatus, selectLiveModules } from "../src/lib/live-room";
import { ServiceError, type FrontendService } from "../src/services/contracts";
import type { FrontendSnapshot } from "../src/lib/domain";

const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
class TestClock implements Clock {
  at = Date.parse("2026-10-21T06:35:00Z");
  timers = new Map<number, { at: number; callback: () => void }>();
  next = 0;
  now = () => this.at;
  setTimer = (callback: () => void, ms: number) => {
    const id = ++this.next; this.timers.set(id, { at: this.at + ms, callback });
    return id as unknown as ReturnType<typeof setTimeout>;
  };
  clearTimer = (id: ReturnType<typeof setTimeout>) => { this.timers.delete(id as unknown as number); };
  async advance(ms: number) {
    const target = this.at + ms;
    for (let i = 0; i < 1000; i++) {
      const next = [...this.timers].sort((a, b) => a[1].at - b[1].at)[0];
      if (!next || next[1].at > target) { this.at = target; await flush(); return; }
      this.at = next[1].at; this.timers.delete(next[0]); next[1].callback(); await flush();
    }
    throw new Error("Unexpected polling loop");
  }
}
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture(mode: "mock" | "api" = "mock", minimum?: number) {
  const clock = new TestClock();
  let snapshot = createMockSnapshot();
  if (mode === "api") { snapshot.source = "API"; snapshot.liveSessions[0].status = "LIVE"; }
  const calls: number[] = [];
  let load: (options?: { signal?: AbortSignal }) => Promise<FrontendSnapshot> = async () => structuredClone(snapshot);
  const service = { mode, getSnapshot: (options?: { signal?: AbortSignal }) => {
    calls.push(clock.now()); return load(options);
  } } as FrontendService;
  const controller = new LiveRoomController(service, snapshot, snapshot.scope, () => {},
    minimum ? { snapshotMinimumMs: minimum } : undefined, clock);
  return { clock, service, controller, calls, get snapshot() { return snapshot; },
    set snapshot(value: FrontendSnapshot) { snapshot = value; },
    set load(value: typeof load) { load = value; } };
}

test("live modules use 10/15/30s cadences and combine coincident reads", async () => {
  const f = fixture(); f.controller.start(); await flush();
  const initial = f.clock.now();
  assert.equal(f.calls.length, 1);
  await f.clock.advance(10_000);
  assert.equal(f.controller.state.tasks.lastSuccessAt, initial + 10_000);
  assert.equal(f.controller.state.live.lastSuccessAt, initial);
  await f.clock.advance(5_000);
  assert.equal(f.controller.state.live.lastSuccessAt, initial + 15_000);
  assert.equal(f.controller.state.commerce.lastSuccessAt, initial);
  await f.clock.advance(15_000);
  assert.deepEqual(f.calls.map((t) => t - initial), [0, 10_000, 15_000, 20_000, 30_000]);
  assert.equal(f.controller.state.commerce.lastSuccessAt, initial + 30_000);
  assert.equal(f.controller.state.plans.lastSuccessAt, initial + 30_000);
  assert.equal(f.controller.state.trends.status, "unavailable");
  f.controller.stop(); assert.equal(f.clock.timers.size, 0);
});

test("no room means no session read, including manual refresh", async () => {
  const f = fixture(); f.snapshot.scope.live_session_id = "all";
  f.controller.start(); await f.controller.refresh(); await f.clock.advance(120_000);
  assert.equal(f.calls.length, 0); assert.equal(f.clock.timers.size, 0);
});
for (const status of ["NOT_STARTED", "ENDED"] as const) test(`${status} reads once and never polls on visibility resume`, async () => {
  const f = fixture(); f.snapshot.liveSessions[0].status = status;
  f.controller.start(); await flush();
  await f.clock.advance(120_000); f.controller.setVisible(false); f.controller.setVisible(true); await flush();
  assert.equal(f.calls.length, 1); assert.equal(f.clock.timers.size, 0);
  if (status === "NOT_STARTED") assert.equal(f.controller.state.commerce.data?.gmv, null);
  await f.controller.refresh(); assert.equal(f.calls.length, 2); f.controller.stop();
});

test("hidden pages stop timers; returning fresh does nothing, returning stale reads once", async () => {
  const f = fixture(); f.controller.start(); await flush();
  f.controller.setVisible(false); assert.equal(f.clock.timers.size, 0);
  await f.clock.advance(5_000); f.controller.setVisible(true); await flush();
  assert.equal(f.calls.length, 1);
  f.controller.setVisible(false); await f.clock.advance(90_000);
  assert.equal(f.calls.length, 1);
  f.controller.setVisible(true); await flush(); assert.equal(f.calls.length, 2);
  assert.equal(f.clock.timers.size, 1); f.controller.stop();
});

test("manual refresh is single flight and leaves prior values while fetching", async () => {
  const f = fixture(); f.controller.start(); await flush();
  const old = f.controller.state.commerce.data;
  const pending = deferred<FrontendSnapshot>(); f.load = () => pending.promise;
  const first = f.controller.refresh(), second = f.controller.refresh(); await flush();
  assert.equal(first, second); assert.equal(f.calls.length, 2);
  assert.equal(f.controller.state.commerce.data, old);
  assert.equal(f.controller.state.commerce.refreshing, true);
  pending.resolve(f.snapshot); await first; f.controller.stop();
});

test("an initial refresh failure retains the valid bootstrap snapshot", async () => {
  const f = fixture(); f.load = async () => { throw new Error("offline on entry"); };
  f.controller.start(); await flush();
  assert.equal(f.controller.state.live.data?.online, 1284);
  assert.equal(f.controller.state.commerce.data?.gmv, 8704000);
  assert.equal(f.controller.state.live.status, "error");
  assert.equal(f.controller.state.live.lastSuccessAt, null);
  f.controller.stop();
});

test("failed reads preserve data and timestamps and use exponential backoff", async () => {
  const f = fixture(); f.controller.start(); await flush();
  const old = f.controller.state.commerce.data, at = f.controller.state.commerce.updatedAt;
  f.load = async () => { throw new Error("offline"); };
  const initial = f.clock.now(); await f.controller.refresh();
  assert.equal(f.controller.state.commerce.data, old); assert.equal(f.controller.state.commerce.updatedAt, at);
  assert.equal(f.controller.state.commerce.status, "error");
  assert.equal(isDelayed(f.controller.state.commerce, "commerce", "live", initial), true);
  await f.controller.refresh(); assert.equal(f.calls.length, 2);
  await f.clock.advance(10_000); assert.equal(f.calls.length, 3);
  await f.clock.advance(19_999); assert.equal(f.calls.length, 3);
  await f.clock.advance(1); assert.equal(f.calls.length, 4);
  await f.clock.advance(40_000); assert.equal(f.calls.length, 5);
  f.load = async () => f.snapshot; await f.clock.advance(80_000);
  assert.equal(f.controller.state.commerce.failures, 0); f.controller.stop();
});

test("429 Retry-After bounds retries and configured endpoint cadence is respected", async () => {
  const f = fixture("api", 60_000); f.controller.start(); await flush();
  assert.equal(f.controller.automatic, true);
  await f.clock.advance(59_999); assert.equal(f.calls.length, 1);
  await f.clock.advance(1); assert.equal(f.calls.length, 2);
  f.load = async () => { throw new ServiceError("RATE_LIMITED", "wait", 180); };
  await f.clock.advance(60_000); assert.equal(f.calls.length, 3);
  await f.clock.advance(179_999); assert.equal(f.calls.length, 3);
  await f.clock.advance(1); assert.equal(f.calls.length, 4); f.controller.stop();
});

test("API mode does not guess a safe request limit", async () => {
  const f = fixture("api"); f.controller.start(); await flush();
  assert.equal(f.controller.automatic, false);
  await f.clock.advance(600_000); assert.equal(f.calls.length, 1); f.controller.stop();
});

test("live to ended captures final commerce even when only the task module is due", async () => {
  const f = fixture(); f.controller.start(); await flush();
  f.snapshot.liveSessions[0].status = "ENDED"; f.snapshot.plans[0].paidGmvMinor = 9000000;
  await f.clock.advance(10_000);
  assert.equal(f.controller.state.live.data?.status, "ended");
  assert.equal(f.controller.state.commerce.data?.gmv, 14292000);
  await f.clock.advance(120_000); assert.equal(f.calls.length, 2); f.controller.stop();
});

test("unmount aborts the last transport consumer and ignores an adapter's late response", async () => {
  const f = fixture(); const pending = deferred<FrontendSnapshot>(); let signal: AbortSignal | undefined;
  f.load = (options) => { signal = options?.signal; return pending.promise; };
  f.controller.start(); await flush(); f.controller.stop();
  assert.equal(signal?.aborted, true);
  pending.resolve(f.snapshot); await flush();
  assert.equal(f.controller.state.live.lastSuccessAt, null); assert.equal(f.clock.timers.size, 0);
});

test("snapshot invalidation serializes old and new scopes and deduplicates consumers", async () => {
  const f = fixture(); const old = deferred<FrontendSnapshot>(); f.load = () => old.promise;
  const first = readSnapshot(f.service), duplicate = readSnapshot(f.service); await flush();
  assert.equal(f.calls.length, 1);
  invalidateSnapshotRead(f.service);
  const next = readSnapshot(f.service), nextDuplicate = readSnapshot(f.service); await flush();
  assert.equal(f.calls.length, 1);
  const fresh = structuredClone(f.snapshot); fresh.scope.connector_id = "qianchuan";
  f.load = async () => fresh; old.resolve(f.snapshot);
  await Promise.all([first, duplicate]);
  const [a, b] = await Promise.all([next, nextDuplicate]);
  assert.equal(f.calls.length, 2); assert.equal(a, b); assert.equal(a.scope.connector_id, "qianchuan");
});

test("a just-arrived provider bootstrap is reused without an entrance duplicate", async () => {
  const f = fixture(); f.clock.at = Date.now();
  const snapshot = await readSnapshot(f.service); f.clock.at = Date.now();
  const view = new LiveRoomController(f.service, snapshot, snapshot.scope, () => {}, undefined, f.clock);
  view.start(); await flush(); assert.equal(f.calls.length, 1);
  assert.equal(view.state.live.data?.online, 1284);
  await f.clock.advance(10_000); assert.equal(f.calls.length, 2); view.stop();
});

test("one consumer unmounting never cancels another component's shared snapshot", async () => {
  const f = fixture(), abort = new AbortController(), pending = deferred<FrontendSnapshot>(); let signal: AbortSignal | undefined;
  f.load = (options) => { signal = options?.signal; return pending.promise; };
  const provider = readSnapshot(f.service), view = readSnapshot(f.service, { signal: abort.signal }); await flush();
  abort.abort(); await assert.rejects(view); assert.equal(signal?.aborted, false);
  pending.resolve(f.snapshot); await provider; assert.equal(f.calls.length, 1);
});

test("the shared transport also limits provider reads and cancels a queued read on unmount", async () => {
  const previous = process.env.NEXT_PUBLIC_SNAPSHOT_MIN_INTERVAL_MS;
  process.env.NEXT_PUBLIC_SNAPSHOT_MIN_INTERVAL_MS = "60000";
  try {
    const f = fixture("api"); await readSnapshot(f.service, { clock: f.clock });
    const next = readSnapshot(f.service, { clock: f.clock }); await flush();
    assert.equal(f.calls.length, 1);
    await f.clock.advance(59_999); assert.equal(f.calls.length, 1);
    await f.clock.advance(1); await next; assert.equal(f.calls.length, 2);
    const abort = new AbortController();
    const cancelled = readSnapshot(f.service, { clock: f.clock, signal: abort.signal }); await flush();
    assert.equal(f.clock.timers.size, 1); abort.abort(); await assert.rejects(cancelled); await flush();
    assert.equal(f.clock.timers.size, 0); assert.equal(f.calls.length, 2);
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_SNAPSHOT_MIN_INTERVAL_MS;
    else process.env.NEXT_PUBLIC_SNAPSHOT_MIN_INTERVAL_MS = previous;
  }
});

test("scope or source mismatches are rejected without contaminating the current session", async () => {
  for (const mismatch of ["scope", "source"]) {
    const f = fixture(); f.controller.start(); await flush();
    const old = f.controller.state.live.data;
    const wrong = structuredClone(f.snapshot);
    if (mismatch === "scope") wrong.scope.connector_id = "qianchuan"; else wrong.source = "API";
    f.load = async () => wrong; await f.controller.refresh();
    assert.equal(f.controller.state.live.data, old); assert.equal(f.controller.state.live.status, "error"); f.controller.stop();
  }
});

test("shared approval reload is consumed immediately without another request", async () => {
  const f = fixture(); f.controller.start(); await flush();
  const updated = structuredClone(f.snapshot); updated.liveSessions[0].status = "ENDED";
  updated.plans[0].budgetMinor = 700000; f.controller.acceptSnapshot(updated);
  assert.equal(f.controller.state.plans.data?.[0].budgetMinor, 700000);
  assert.equal(f.controller.state.live.data?.status, "ended"); assert.equal(f.calls.length, 1);
  assert.equal(f.clock.timers.size, 0); f.controller.stop();
});

test("changing scope suspends and discards a late read before resuming safely", async () => {
  const f = fixture(); f.controller.start(); await flush();
  const old = f.controller.state.live.data;
  const pending = deferred<FrontendSnapshot>(); f.load = () => pending.promise;
  void f.controller.refresh(); await flush(); f.controller.suspend();
  const wrong = structuredClone(f.snapshot); wrong.liveSessions[0].online = 99999;
  pending.resolve(wrong); await flush();
  assert.equal(f.controller.state.live.data, old); assert.equal(f.clock.timers.size, 0);
  f.load = async () => f.snapshot; f.controller.resume(); await flush();
  assert.equal(f.controller.state.live.data?.online, 1284); f.controller.stop();
});

test("remounting during endpoint cooldown cannot bypass the confirmed API limit", async () => {
  const f = fixture("api", 60_000); f.controller.start(); await flush(); f.controller.stop();
  f.snapshot.liveSessions[0].status = "ENDED";
  const next = new LiveRoomController(f.service, f.snapshot, f.snapshot.scope, () => {}, { snapshotMinimumMs: 60_000 }, f.clock);
  next.start(); await flush(); assert.equal(f.calls.length, 1);
  await f.clock.advance(60_000); assert.equal(f.calls.length, 2);
  assert.equal(next.state.live.data?.status, "ended"); assert.equal(f.clock.timers.size, 0); next.stop();
});

test("freshness distinguishes platform publication time from successful fetch time, with per-source thresholds", async () => {
  const f = fixture(); f.controller.start(); await flush(); const fetched = f.clock.now();
  const policy = { maxAgeMs: { ...liveRefreshConfiguration().maxAgeMs, live: 90_000, commerce: 180_000 } };
  assert.equal(isDelayed(f.controller.state.live, "live", "live", fetched + 60_000, 0, policy), false);
  assert.equal(isDelayed(f.controller.state.live, "live", "live", fetched + 90_001, 0, policy), true);
  await f.clock.advance(30_000);
  assert.equal(f.controller.state.live.updatedAt, "2026-10-21T06:35:00.000Z");
  assert.equal(f.controller.state.live.lastSuccessAt, fetched + 30_000);
  assert.equal(isDelayed(f.controller.state.live, "live", "ended", fetched + 900_000), false);
  f.controller.stop();
});

test("missing values remain null, real zero remains zero, and mismatched attribution windows are not summed", () => {
  const f = fixture(); f.snapshot.liveSessions[0].online = 0;
  f.snapshot.liveSessions[0].totalViewers = undefined;
  let result = selectLiveModules(f.snapshot, f.snapshot.scope);
  assert.equal(result.data.live.online, 0); assert.equal(result.data.live.viewers, null);
  f.snapshot.plans[0].paidGmvMinor = null;
  result = selectLiveModules(f.snapshot, f.snapshot.scope); assert.equal(result.data.commerce.gmv, null);
  f.snapshot.plans[0].paidGmvMinor = 100; f.snapshot.plans[0].attributionWindow = "different";
  result = selectLiveModules(f.snapshot, f.snapshot.scope); assert.equal(result.data.commerce.roi, null);
  f.snapshot.scope.connector_id = "qianchuan";
  result = selectLiveModules(f.snapshot, f.snapshot.scope); assert.equal(result.data.live.online, null);
  assert.equal(liveStatus(f.snapshot.liveSessions[0], "API"), "unknown");
});
