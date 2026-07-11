"use client";

import { AlertTriangle, RotateCcw } from "lucide-react";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main className="error-screen">
      <AlertTriangle size={30} />
      <h1>控制中心暫時無法載入</h1>
      <p>資料服務可能正在更新，請稍後再試。</p>
      <button className="primary-button" onClick={reset}>
        <RotateCcw size={16} />重新載入
      </button>
    </main>
  );
}
