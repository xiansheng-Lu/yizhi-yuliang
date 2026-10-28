import type { Identity } from "@/lib/domain";
import { ServiceError, type LoginRequest } from "./contracts";
export interface MockStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}
export const identityKey = "yuliang:g1a:mock-identity";
export class MockIdentityAdapter {
  constructor(private storage: MockStorage) {}
  async getIdentity(): Promise<Identity | null> {
    const value = this.storage.getItem(identityKey);
    if (!value) return null;
    try {
      const data = JSON.parse(value) as Identity;
      if (
        data.mode !== "MOCK" ||
        data.tenantId !== "tenant-demo" ||
        !Array.isArray(data.permissions)
      )
        throw new Error();
      if (data.userId === "demo-operator" && !data.email) data.email = "demo@yuliang.local";
      if (
        data.userId === "demo-operator" &&
        data.permissions.length === 3 &&
        ["snapshot.read", "task.create", "task.approve"].every((permission) =>
          data.permissions.includes(permission),
        )
      ) {
        data.shopIds = ["shop-tea", "shop-mountain"];
        data.permissions.push("task.execute", "policy.edit");
        this.storage.setItem(identityKey, JSON.stringify(data));
      }
      return data;
    } catch {
      throw new ServiceError(
        "INVALID_RESPONSE",
        "Mock 身份存储损坏，请在登录页清除演示身份后重试。",
      );
    }
  }
  async login(input: LoginRequest): Promise<Identity> {
    if (
      !("password" in input) ||
      input.email.trim() !== "demo@yuliang.local" ||
      input.password !== "demo123"
    )
      throw new ServiceError(
        "UNAUTHENTICATED",
        "请使用演示账号；本阶段尚未接入正式登录。",
      );
    const identity: Identity = {
      userId: "demo-operator",
      displayName: "演示运营员",
      email: "demo@yuliang.local",
      tenantId: "tenant-demo",
      shopIds: ["shop-tea", "shop-mountain"],
      permissions: [
        "snapshot.read",
        "task.create",
        "task.approve",
        "task.execute",
        "policy.edit",
      ],
      mode: "MOCK",
    };
    this.storage.setItem(identityKey, JSON.stringify(identity));
    return identity;
  }
  async updateProfile(input: { displayName: string }) {
    const actor = await this.getIdentity();
    if (!actor) throw new ServiceError("UNAUTHENTICATED", "请先登录。");
    const displayName = input.displayName.trim();
    if (!displayName || displayName.length > 40) throw new ServiceError("INVALID_INPUT", "昵称需要 1–40 个字符。");
    const updated = { ...actor, displayName };
    this.storage.setItem(identityKey, JSON.stringify(updated));
    return updated;
  }
  async logout() {
    this.storage.removeItem(identityKey);
  }
}
