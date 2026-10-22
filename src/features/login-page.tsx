"use client";
import Link from "next/link";
import { useSearchParams, useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, ArrowRight } from "@phosphor-icons/react";
import { loginTarget, pageName } from "@/lib/routes";
import { useWorkspace } from "@/components/provider";
import { Badge } from "@/components/ui";
import { BrandLogo } from "@/components/brand-logo";
export function LoginPage() {
  const { service, ready, identity, reload, logout, data } = useWorkspace();
  const query = useSearchParams(),
    router = useRouter(),
    target = loginTarget(query.get("next"));
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const mock = (process.env.NEXT_PUBLIC_DATA_MODE ?? "mock") === "mock";
  useEffect(() => {
    if (ready && identity) router.replace(target);
  }, [ready, identity, target, router]);
  async function login(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!service || busy) return;
    setBusy(true);
    setError("");
    try {
      await service.login({ email, password });
      await reload();
      router.replace(target);
    } catch (error) {
      setError(error instanceof Error ? error.message : "登录未完成，请重试。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-layout">
      <aside className="login-visual">
        <Link href="/" className="brand">
          <BrandLogo />
        </Link>
        <div>
          <div className="eyebrow">YULIANG INTELLIGENCE</div>
          <h1>
            自主投放，
            <br />
            让每一份增长
            <br />
            <em>都有据可依。</em>
          </h1>
          <p>
            同一工作台，连接经营目标、内容生产、
            <br />
            投放申请与完整复盘。
          </p>
        </div>
        <span>
          {mock ? "V12 前端演示 · 无真实广告操作" : "正式登录由独立后端验证"}
        </span>
      </aside>
      <section className="login-form-wrap">
        <a href="/" className="back-link">
          <ArrowLeft size={16} />
          返回官网
        </a>
        <div className="login-panel">
          <Badge tone="blue">{mock ? "Mock 身份适配器" : "API 登录"}</Badge>
          <h2>登录工作台</h2>
          <p>从一句话开始，让 AI 协助你的日常运营。</p>
          <form onSubmit={login}>
            <label>
              账号 / 邮箱
              <input
                name="email"
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder={mock ? "演示邮箱" : "账号邮箱"}
              />
            </label>
            <label>
              密码
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder={mock ? "请使用演示密码" : "密码"}
              />
            </label>
            <button className="button primary" disabled={!ready || busy}>
              {busy ? "正在验证…" : `进入 ${pageName("command")}`}
              <ArrowRight size={18} />
            </button>
            <p role="alert" className="form-error">
              {error || (data.status === "error" ? data.error.message : "")}
            </p>
          </form>
          {mock && (
            <>
              <button
                className="demo-fill"
                onClick={() => {
                  setEmail("demo@yuliang.local");
                  setPassword("demo123");
                }}
              >
                一键填入演示账号
              </button>
              <div className="demo-account">
                <b>仅供浏览器内演示</b>
                <span>demo@yuliang.local / demo123</span>
                <p>请勿输入真实账号密码。正式服务端认证由后端提供。</p>
                <button className="text-link" onClick={() => void logout()}>
                  清除演示身份
                </button>
              </div>
            </>
          )}
          <small className="login-destination">登录后进入：{target}</small>
        </div>
      </section>
    </main>
  );
}
