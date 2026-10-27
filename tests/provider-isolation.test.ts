import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import ts from "typescript";
import type { FrontendSnapshot } from "../src/lib/domain";
import type { FrontendService } from "../src/services/contracts";
import { createMockSnapshot } from "../src/services/mock-data";
import { invalidateSnapshotRead, readSnapshot } from "../src/services/snapshot-reader";

// No DOM/test renderer dependency: check the actual provider's auth fences and
// exercise the shared reader races separately. Browser UAT covers mounted state.
const source = ts.createSourceFile("provider.tsx", readFileSync("src/components/provider.tsx", "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function nodes(root: ts.Node): ts.Node[] {
  const result: ts.Node[] = [];
  const visit = (node: ts.Node) => { result.push(node); node.forEachChild(visit); };
  visit(root); return result;
}
function calls(root: ts.Node, name: string) {
  return nodes(root).filter(ts.isCallExpression).filter((node) => node.expression.getText(source) === name);
}
function providerBody(name: "reload" | "logout"): ts.Node {
  if (name === "logout") {
    const declaration = nodes(source).filter(ts.isFunctionDeclaration).find((node) => node.name?.text === name);
    assert.ok(declaration?.body); return declaration.body;
  }
  const declaration = nodes(source).filter(ts.isVariableDeclaration).find((node) => node.name.getText(source) === name);
  assert.ok(declaration?.initializer && ts.isCallExpression(declaration.initializer));
  const callback = declaration.initializer.arguments[0];
  assert.ok(ts.isArrowFunction(callback)); return callback.body;
}

test("provider invalidates shared snapshots before reading a changed or absent actor", () => {
  const reload = providerBody("reload");
  const changedOwner = nodes(reload).filter(ts.isIfStatement).find((node) => node.expression.getText(source).includes("nextOwner !== owner.current"));
  assert.ok(changedOwner);
  assert.match(changedOwner.expression.getText(source), /!actor/);
  const invalidation = calls(changedOwner.thenStatement, "invalidateSnapshotRead");
  assert.equal(invalidation.length, 1);
  assert.ok(invalidation[0].pos < calls(reload, "readSnapshot")[0].pos);
  const unauthorized = nodes(reload).filter(ts.isIfStatement).find((node) => node.expression.getText(source).includes('error.code === "UNAUTHENTICATED"'));
  assert.ok(unauthorized);
  assert.equal(calls(unauthorized.thenStatement, "invalidateSnapshotRead").length, 1);
  assert.match(unauthorized.thenStatement.getText(source), /owner\.current = null/);
});

test("provider fences logout before transport, clears state only after success, and rechecks failures", () => {
  const logout = providerBody("logout");
  const logoutAwait = nodes(logout).filter(ts.isAwaitExpression).find((node) => node.expression.getText(source) === "service.current?.logout()");
  assert.ok(logoutAwait);
  const invalidations = calls(logout, "invalidateSnapshotRead");
  assert.ok(invalidations.some((node) => node.pos < logoutAwait.pos));
  assert.ok(nodes(logout).filter(ts.isPrefixUnaryExpression).some((node) => node.getText(source) === "++generation.current" && node.pos < logoutAwait.pos));
  for (const name of ["setIdentity", "setLastValidSnapshot", "setData"]) {
    const updates = calls(logout, name);
    assert.equal(updates.length, 1);
    assert.ok(updates.every((node) => node.pos > logoutAwait.pos));
  }
  const failed = nodes(logout).filter(ts.isCatchClause)[0];
  assert.ok(failed && calls(failed, "reload").length === 1);
  assert.ok(nodes(failed).some(ts.isThrowStatement));
  assert.equal(calls(failed, "setIdentity").length, 0);
  assert.match(providerBody("reload").getText(source), /if \(logoutRequest\.current\) return null/);
});

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
function fixture() {
  const old = deferred<FrontendSnapshot>();
  const actorA = createMockSnapshot(), actorB = structuredClone(actorA);
  actorA.tenants[0].name = "Actor A"; actorB.tenants[0].name = "Actor B";
  let activeActor = "A", reads = 0, generation = 0;
  let published: FrontendSnapshot | null = null;
  const service = {
    mode: "mock",
    getSnapshot: async () => { reads++; return activeActor === "A" ? old.promise : actorB; },
  } as FrontendService;
  const load = async () => {
    const request = ++generation;
    const snapshot = await readSnapshot(service);
    if (request === generation) published = snapshot;
  };
  return { old, actorA, actorB, load,
    get reads() { return reads; }, get published() { return published; },
    switchActor() { activeActor = "B"; },
    fence() { generation++; invalidateSnapshotRead(service); },
  };
}

test("an actor B read waits for actor A transport but never reuses its result", async () => {
  const state = fixture();
  const oldRead = state.load(); await flush();
  assert.equal(state.reads, 1);
  state.switchActor(); state.fence();
  const freshRead = state.load(); await flush();
  assert.equal(state.reads, 1, "the old request retains the shared transport lock");
  state.old.resolve(state.actorA);
  await Promise.all([oldRead, freshRead]);
  assert.equal(state.reads, 2);
  assert.equal(state.published, state.actorB);
});

test("a snapshot finishing after logout cannot publish or be reused on the next login", async () => {
  const state = fixture();
  const oldRead = state.load(); await flush();
  state.fence();
  state.old.resolve(state.actorA); await oldRead;
  assert.equal(state.published, null);
  state.switchActor();
  await state.load();
  assert.equal(state.reads, 2);
  assert.equal(state.published, state.actorB);
});
