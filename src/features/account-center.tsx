"use client";
import { useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowUpRight, CheckCircle, EnvelopeSimple, ShieldCheck, Storefront, UserCircle } from "@phosphor-icons/react";
import { useWorkspace } from "@/components/provider";
import { Badge, EmptyState, Panel } from "@/components/ui";
import { canAccessPage } from "@/lib/access";
import { navigationGroups } from "@/lib/routes";

export function AccountCenter() {
  const { identity, data, service, reload, logout } = useWorkspace();
  const router = useRouter();
  const [displayName, setDisplayName] = useState(identity?.displayName ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  if (!identity) return null;
  const snapshot = data.status === "ready" ? data.data : null;
  const tenant = snapshot?.tenants.find((item) => item.id === identity.tenantId);
  const shops = snapshot?.shops.filter((shop) => identity.shopIds.includes(shop.id));
  const groups = navigationGroups.map((group) => ({ ...group, pages: group.pages.filter(([id]) => canAccessPage(identity, id)) })).filter((group) => group.pages.length);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!service || busy) return;
    setBusy(true); setError(""); setSaved(false);
    try {
      const updated = await service.updateProfile({ displayName });
      await reload();
      setDisplayName(updated.displayName); setSaved(true);
    } catch (error) {
      setError(error instanceof Error ? error.message : "资料保存失败，请稍后重试。");
    } finally { setBusy(false); }
  }
  async function signOut() {
    setSigningOut(true); setError("");
    try { await logout(); router.replace("/login"); }
    catch (error) { setError(error instanceof Error ? error.message : "退出未完成，请重试。"); }
    finally { setSigningOut(false); }
  }
  return (
    <div className="account-center">
      <section className="account-summary">
        <span className="account-avatar"><UserCircle size={34} /></span>
        <div><span className="eyebrow">MY WORKSPACE</span><h2>{identity.displayName}</h2><p><EnvelopeSimple size={14} />{identity.email ?? "邮箱信息尚未同步"}</p></div>
        <Badge tone="blue">{identity.mode === "API" ? "已登录" : "本地预览"}</Badge>
      </section>
      <div className="account-grid">
        <Panel title="个人资料" detail="用于工作区内展示和协作识别。">
          <form onSubmit={save} className="account-profile">
            <label>昵称<input required maxLength={40} autoComplete="nickname" value={displayName} onChange={(event) => { setDisplayName(event.target.value); setSaved(false); }} /></label>
            <label>登录邮箱<input type="email" value={identity.email ?? ""} readOnly /></label>
            <p className="account-hint">邮箱作为账号凭证，由登录服务验证和维护。</p>
            <div className="row"><button className="button primary" disabled={busy || signingOut || !displayName.trim() || displayName.trim() === identity.displayName}>{busy ? "正在保存…" : "保存资料"}</button>{saved && <span className="account-saved" role="status"><CheckCircle size={16} />已保存</span>}</div>
          </form>
        </Panel>
        <Panel title="账号与工作空间" detail="查看当前账号的登录状态与授权范围。">
          <dl className="account-facts"><div><dt>所属企业</dt><dd>{tenant?.name ?? "企业信息待同步"}</dd></div><div><dt>授权店铺</dt><dd>{identity.shopIds.length} 家</dd></div><div><dt>可用工作区</dt><dd>{groups.reduce((count, group) => count + group.pages.length, 0)} 项</dd></div></dl>
          <div className="account-security"><ShieldCheck size={19} /><p>功能和店铺范围随账号授权同步。需要调整时，请联系企业管理员。</p></div>
          <button className="button secondary" disabled={signingOut || busy} onClick={() => void signOut()}>{signingOut ? "正在退出…" : "退出登录"}</button>
        </Panel>
      </div>
      {error && <p className="form-error" role="alert">{error}</p>}
      <Panel title="我的工作台" detail="只展示当前账号已授权的功能。">
        <div className="account-workspaces">{groups.map((group) => <section key={group.name}><h3>{group.name}</h3>{group.pages.map(([id, name]) => <Link key={id} href={`/app/${id}`}>{name}<ArrowUpRight size={15} /></Link>)}</section>)}</div>
        {!groups.length && <EmptyState title="暂无已授权的工作区" description="账号已登录，管理员完成授权后可在这里进入对应功能。" />}
        {canAccessPage(identity, "tasks") && <Link className="account-tasks" href="/app/tasks"><CheckCircle size={18} /><span><b>任务与审批</b><small>查看运营任务、确认变更与处理审批</small></span><ArrowUpRight size={16} /></Link>}
      </Panel>
      <Panel title="已授权店铺" detail="与当前账号可访问的数据范围保持一致。">
        {shops?.length ? <div className="account-shops">{shops.map((shop) => <article key={shop.id}><Storefront size={23} /><div><b>{shop.name}</b><small>已授权访问</small></div></article>)}</div> : <EmptyState title={snapshot ? "暂无已授权的店铺" : "店铺信息暂未加载"} description={snapshot ? "完成店铺授权后将在这里显示。" : "账号信息可正常管理，店铺列表将在工作区数据恢复后显示。"} />}
      </Panel>
    </div>
  );
}
