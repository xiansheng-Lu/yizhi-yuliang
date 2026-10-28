import test from "node:test";
import assert from "node:assert/strict";
import { accountLinkKey, contentDeliveryAccounts, linkContentAccounts, validateAccountLinks } from "../src/lib/content-delivery";
import { emptyContent, validateContent, type ContentOwner } from "../src/lib/content-factory";
import type { Identity } from "../src/lib/domain";
import type { IntegrationContext } from "../src/lib/platform-catalog";
import { createMockSnapshot } from "../src/services/mock-data";

const now = Date.parse("2026-10-23T05:00:00Z"), snapshot = createMockSnapshot();
const owner: ContentOwner = { mode: "MOCK", tenantId: "tenant-demo", userId: "creator", shopId: "shop-tea" };
const identity: Identity = { ...owner, displayName: "创作者", permissions: [], shopIds: ["shop-tea", "shop-mountain"] };
const context: IntegrationContext = {
  contractVersion: "platform-workspace-v9.0", source: "API", tenantId: owner.tenantId, productLine: "adq", checkedAt: new Date(now).toISOString(), expiresAt: new Date(now + 60_000).toISOString(),
  entities: [{ id: "entity", name: "经营主体" }], shops: [{ id: owner.shopId, name: "茶韵旗舰店", businessEntityId: "entity", provider: "tencent" }],
  accounts: [{ id: "one", nativeId: "101", name: "授权账号", businessEntityId: "entity", provider: "tencent", productLine: "adq", authorization: "AUTHORIZED" }],
  links: [{ accountId: "one", shopId: owner.shopId, state: "AUTHORIZED", checkedAt: new Date(now).toISOString() }], operations: [],
};
test("content account choices isolate tenant, shop and mode; API never falls back to demo snapshot accounts", () => {
  assert.deepEqual(contentDeliveryAccounts(owner, identity, "qianchuan", snapshot, now).map(a => a.accountId), ["account-qianchuan"]);
  assert.equal(contentDeliveryAccounts({ ...owner, shopId: "shop-mountain" }, identity, "adq", snapshot, now).length, 0);
  assert.equal(contentDeliveryAccounts({ ...owner, tenantId: "other" }, identity, "adq", snapshot, now).length, 0);
  assert.equal(contentDeliveryAccounts({ ...owner, userId: "other" }, identity, "adq", snapshot, now).length, 0);
  assert.equal(contentDeliveryAccounts(owner, { ...identity, shopIds: [] }, "adq", snapshot, now).length, 0);
  assert.equal(contentDeliveryAccounts({ ...owner, mode: "API" }, { ...identity, mode: "API" }, "adq", snapshot, now).length, 0);
});
test("API choices require current account authorization and an authorized matching shop/entity/product link", () => {
  const apiOwner = { ...owner, mode: "API" as const }, apiIdentity = { ...identity, mode: "API" as const };
  const choose = (value: IntegrationContext) => contentDeliveryAccounts(apiOwner, apiIdentity, "adq", value, now);
  assert.equal(choose(context).length, 1);
  const variants: IntegrationContext[] = [
    { ...context, tenantId: "other" }, { ...context, productLine: "qianchuan" }, { ...context, expiresAt: new Date(now).toISOString() },
    { ...context, links: [{ ...context.links[0], state: "BOUND" }] }, { ...context, links: [] }, { ...context, shops: [] },
    { ...context, accounts: [{ ...context.accounts[0], authorization: "UNAUTHORIZED" }] },
    { ...context, accounts: [{ ...context.accounts[0], businessEntityId: "other" }] },
    { ...context, accounts: [{ ...context.accounts[0], productLine: "qianchuan" }] },
  ];
  for (const value of variants) assert.equal(choose(value).length, 0);
});
test("completed asset links survive serialization, deduplicate by product/account, and remove independently", () => {
  const state = emptyContent(); state.assets.push({ id: "finished", name: "成品.png", kind: "image", documentId: null, productId: "", mime: "image/png", size: 100, duration: 0, createdAt: new Date(now).toISOString() });
  const adq = contentDeliveryAccounts(owner, identity, "adq", snapshot, now)[0], qc = contentDeliveryAccounts(owner, identity, "qianchuan", snapshot, now)[0];
  assert.doesNotThrow(() => validateContent(state, snapshot.products)); // Old drafts have no accountLinks.
  const linked = linkContentAccounts(state, "finished", owner, [adq, adq, qc], [], now);
  assert.equal(linked.assets[0].accountLinks?.length, 2); assert.equal(state.assets[0].accountLinks, undefined);
  const stored = JSON.parse(JSON.stringify(linked));
  assert.doesNotThrow(() => validateAccountLinks(stored, owner));
  assert.equal(stored.assets[0].accountLinks[0].linkedBy, owner.userId);
  assert.equal(stored.assets[0].accountLinks[0].expiresAt, undefined);
  const removed = linkContentAccounts(stored, "finished", owner, [], [accountLinkKey(adq)], now);
  assert.deepEqual(removed.assets[0].accountLinks?.map(a => a.productLine), ["qianchuan"]);
  const cleared = linkContentAccounts(removed, "finished", owner, [], [accountLinkKey(qc)], now);
  assert.equal(cleared.assets[0].accountLinks?.length, 0);
});
test("association rejects stale or foreign accounts, invalid persisted links and missing finished output", () => {
  const state = emptyContent(); state.assets.push({ id: "finished", name: "成品.mp4", kind: "video", documentId: null, productId: "", mime: "video/mp4", size: 100, duration: 3, createdAt: new Date(now).toISOString() });
  const account = contentDeliveryAccounts(owner, identity, "adq", snapshot, now)[0];
  for (const bad of [{ ...account, shopId: "other" }, { ...account, tenantId: "other" }, { ...account, mode: "API" as const }, { ...account, expiresAt: new Date(now).toISOString() }]) assert.throws(() => linkContentAccounts(state, "finished", owner, [bad], [], now));
  assert.throws(() => linkContentAccounts(state, "missing", owner, [account], [], now));
  const linked = linkContentAccounts(state, "finished", owner, [account], [], now);
  assert.throws(() => validateAccountLinks(linked, { ...owner, shopId: "other" }));
  linked.assets[0].accountLinks![0].productLine = "unknown" as "adq";
  assert.throws(() => validateContent(linked, snapshot.products));
  state.assets[0].size = 0; assert.throws(() => linkContentAccounts(state, "finished", owner, [account], [], now));
});
