"use client";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle, DeviceMobile, EnvelopeSimple, ShieldCheck } from "@phosphor-icons/react";
import { loginTarget } from "@/lib/routes";
import { authorizedLoginTarget } from "@/lib/access";
import { useWorkspace } from "@/components/provider";
import { BrandLogo } from "@/components/brand-logo";
import { ServiceError, type EmailLoginChallenge } from "@/services/contracts";

function loginError(error: unknown) {
  if (error instanceof ServiceError && error.code === "API_NOT_CONFIGURED") return "登录服务暂未连接，请稍后再试。";
  return error instanceof Error ? error.message : "登录未完成，请重试。";
}

export function LoginPage() {
  const { service, ready, identity, reload, logout, data } = useWorkspace();
  const router = useRouter();
  const query = useSearchParams();
  const target = loginTarget(query.get("next"));
  const mock = (process.env.NEXT_PUBLIC_DATA_MODE ?? "api") === "mock";
  const [method, setMethod] = useState<"email_code" | "password">(mock ? "password" : "email_code");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState<EmailLoginChallenge | null>(null);
  const [retryAt, setRetryAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const retrySeconds = Math.max(0, Math.ceil((retryAt - now) / 1000));
  const expired = Boolean(challenge && now >= Date.parse(challenge.expiresAt));
  useEffect(() => {
    if (ready && identity) router.replace(authorizedLoginTarget(identity, target));
  }, [ready, identity, target, router]);
  useEffect(() => {
    if (!challenge && !retryAt) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [challenge, retryAt]);
  async function sendCode() {
    if (!service || !ready || sending || busy || retrySeconds) return;
    setSending(true); setError("");
    try {
      const result = await service.requestEmailCode({ email });
      setChallenge(result); setCode("");
      const sentAt = Date.now(); setNow(sentAt); setRetryAt(sentAt + result.retryAfterSeconds * 1000);
    } catch (error) {
      setError(loginError(error));
      if (error instanceof ServiceError && error.code === "RATE_LIMITED") {
        const limitedAt = Date.now(); setNow(limitedAt); setRetryAt(limitedAt + (error.retryAfterSeconds ?? 60) * 1000);
      }
    } finally { setSending(false); }
  }
  async function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!service || busy || sending) return;
    setBusy(true); setError("");
    try {
      if (method === "password") await service.login({ email, password, method: "password" });
      else {
        if (!challenge || Date.now() >= Date.parse(challenge.expiresAt)) throw new ServiceError("INVALID_INPUT", "请重新获取邮箱验证码。");
        await service.login({ email, code, challengeId: challenge.challengeId, method: "email_code" });
      }
      const actor = await reload();
      if (!actor) throw new ServiceError("UNAUTHENTICATED", "未能建立登录会话，请重试。");
      router.replace(authorizedLoginTarget(actor, target));
    } catch (error) {
      if (method === "email_code" && error instanceof ServiceError && error.code === "FORBIDDEN") {
        setChallenge(null); setCode("");
        setError("登录页面的安全凭证已更新，请重新获取验证码后登录。");
      } else setError(loginError(error));
    }
    finally { setBusy(false); }
  }
  return (
    <main className="login-layout">
      <aside className="login-visual">
        <Link href="/" className="brand"><BrandLogo /></Link>
        <div><div className="eyebrow">YULIANG INTELLIGENCE</div><h1>自主投放，<br />让每一份增长<br /><em>都有据可依。</em></h1><p>同一工作台，连接经营目标、内容生产、<br />投放协作与经营复盘。</p></div>
        <span>安全登录 · 多端协同</span>
      </aside>
      <section className="login-form-wrap">
        <div className="login-panel">
          <span className="login-symbol"><EnvelopeSimple size={25} /></span>
          <h2>账号登录</h2><p>{mock ? "本地功能预览，请使用测试账号。" : "登录你的邮箱账号，进入运营工作空间。"}</p>
          <div className="login-methods" role="tablist" aria-label="登录方式">{([ ["email_code", "邮箱验证码"], ["password", "邮箱密码"] ] as const).map(([value, name]) => <button key={value} type="button" role="tab" id={`login-${value}`} aria-selected={method === value} aria-controls="login-form" disabled={busy || sending} onClick={() => { setMethod(value); setError(""); setPassword(""); }}>{name}</button>)}</div>
          <form onSubmit={login} id="login-form" role="tabpanel" aria-labelledby={`login-${method}`}>
            <label>邮箱地址<input name="email" type="email" autoComplete="email" required value={email} disabled={busy || sending} placeholder="请输入你的邮箱" onChange={(event) => { setEmail(event.target.value); setChallenge(null); setCode(""); setError(""); }} /></label>
            {method === "password" ? <label>{mock ? "预览密码" : "密码"}<input name="password" type="password" autoComplete="current-password" required value={password} disabled={busy} onChange={(event) => setPassword(event.target.value)} placeholder="请输入账号密码" /></label> : <>
              <label>邮箱验证码<div className="email-code-field"><input name="code" inputMode="numeric" autoComplete="one-time-code" required pattern="[0-9]{6}" maxLength={6} value={code} disabled={busy || sending} placeholder="6 位验证码" onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))} /><button type="button" disabled={!ready || sending || busy || retrySeconds > 0 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())} onClick={() => void sendCode()}>{sending ? "正在发送…" : retrySeconds > 0 ? `${retrySeconds} 秒后重发` : challenge ? "重新发送" : "获取验证码"}</button></div></label>
              {challenge && <p className={`login-code-status ${expired ? "expired" : ""}`} role="status">{expired ? "验证码已过期，请重新获取。" : <><CheckCircle size={14} />验证码已发送，请检查邮箱及垃圾邮件。</>}</p>}
            </>}
            <button className="button primary" disabled={!ready || busy || sending || (method === "email_code" && (!challenge || expired || code.length !== 6))}>{busy ? "正在登录…" : "登录并进入工作台"}<ArrowRight size={18} /></button>
            <p role="alert" className="form-error">{error || (data.status === "error" ? loginError(data.error) : "")}</p>
          </form>
          {!mock && <p className="login-security"><ShieldCheck size={15} />请妥善保管账号凭证，勿向他人转发验证码。</p>}
          {mock && <div className="demo-account"><b>仅供本地开发预览</b><span>demo@yuliang.local / demo123</span><button className="demo-fill" onClick={() => { setMethod("password"); setEmail("demo@yuliang.local"); setPassword("demo123"); }}>填入测试账号</button><button className="text-link" onClick={() => void logout().catch((error) => setError(loginError(error)))}>清除测试登录</button></div>}
          <Link className="login-app-link" href="/mobile"><DeviceMobile size={16} />APP端<ArrowRight size={14} /></Link>
        </div>
      </section>
    </main>
  );
}
