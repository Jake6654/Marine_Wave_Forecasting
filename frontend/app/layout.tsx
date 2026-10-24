import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = {
  title: "덕적도 관측·예측 | Marine Wave Forecasting",
  description: "덕적도 최신 해양기상 관측과 6시간 뒤 유의파고 실험 예측. 공식 예보나 운항 안전 판정이 아닙니다.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="ko"><body>{children}</body></html>;
}
