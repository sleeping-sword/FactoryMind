"use client";

import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Incident } from "@/lib/types";

const QUICK = ["為什麼選這個方案?", "其他方案差在哪?", "不處理會怎樣?"];

/** AI 摘要與問答。數字一律來自系統計算;AI 只負責講成白話。 */
export default function AiPanel({ incident, aiEnabled }: { incident: Incident; aiEnabled: boolean }) {
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const brief = incident.ai_brief;
  if (!brief) return null;

  async function ask(question: string) {
    if (!question.trim() || pending) return;
    setPending(question);
    setError(null);
    try {
      await api.ask(incident.id, question);
      setDraft("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "沒有送出,請確認後端服務是否正常。");
    }
    setPending(null);
  }

  return (
    <section className="card ai">
      <header className="card-head split">
        <h2>{brief.headline}</h2>
        <span
          className={`chip ${brief.source === "ai" ? "good" : ""}`}
          title={brief.note ?? (brief.source === "ai" ? "數字皆已核對出自系統計算" : undefined)}
        >
          {brief.source === "ai" ? `✦ AI・${brief.model}` : aiEnabled && !brief.note ? "AI 撰寫中…" : "規則摘要"}
        </span>
      </header>
      {brief.reasons.length > 0 && (
        <ul className="bullets">
          {brief.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      {brief.watch.length > 0 && (
        <ul className="watch">
          {brief.watch.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      )}
      {brief.source === "rules" && brief.note && <p className="footnote">AI 沒有完成:{brief.note}</p>}

      <details className="ask" open={incident.chat.length > 0 || undefined}>
        <summary>問 AI</summary>
        {incident.chat.length > 0 && (
          <ol className="chat">
            {incident.chat.map((turn, i) => (
              <li key={i}>
                <p className="q">{turn.question}</p>
                <p className={turn.source === "ai" ? "a" : "a muted"}>{turn.answer}</p>
              </li>
            ))}
          </ol>
        )}
        {pending && (
          <p className="q" aria-live="polite">
            {pending} <small>AI 思考中…</small>
          </p>
        )}
        <div className="actions">
          {QUICK.map((q) => (
            <button key={q} type="button" disabled={!!pending} onClick={() => ask(q)}>
              {q}
            </button>
          ))}
        </div>
        <form
          className="ask-form"
          onSubmit={(e) => {
            e.preventDefault();
            ask(draft);
          }}
        >
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={300}
            placeholder={aiEnabled ? "例如:P4 加班的話會怎樣?" : "AI 尚未啟用(後端沒有金鑰)"}
            aria-label="問 AI"
          />
          <button type="submit" className="primary" disabled={!!pending || !draft.trim()}>
            問
          </button>
        </form>
        {error && <p className="footnote">✕ {error}</p>}
      </details>
    </section>
  );
}
