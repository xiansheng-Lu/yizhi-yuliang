"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { loginTarget } from "@/lib/routes";
export default function LegacyEntry() {
  const router = useRouter();
  useEffect(() => {
    router.replace(loginTarget(window.location.hash));
  }, [router]);
  return <p role="status">正在恢复历史工作台入口…</p>;
}
