import { Suspense } from "react";
import { LoginPage } from "@/features/login-page";
export default function LoginRoute() {
  return (
    <Suspense fallback={<p>正在准备登录入口…</p>}>
      <LoginPage />
    </Suspense>
  );
}
