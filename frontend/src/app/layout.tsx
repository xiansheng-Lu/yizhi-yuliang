import type { Metadata } from "next";
import type { ReactNode } from "react";
export const metadata: Metadata = {
  title: "易致·驭量｜AI 自主投放工作台",
  description: "自主投放，让每一份增长都有据可依。",
  icons: { icon: "/assets/yuliang-mark.svg" },
};
export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
