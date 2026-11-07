"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { METRIC_LABEL, timeOfDay } from "@/lib/format";
import type { Evidence, Incident } from "@/lib/types";

const QUICK = ["為什麼選這個方案?", "其他方案差在哪?", "不處理會怎樣?"];
const UNIT_AFTER: Record<Evidence["unit"], RegExp> = { 分鐘: /^\s*(分鐘|分|小時)/, 張: /^\s*張/, NT$: /^\s*元/ };

/** 把 [E12] 標記換成可點的數字;點了在下方顯示這個數字屬於哪個方案、指標與版本。 */
function Cited({ text, evidence, onPick }: { text: string; evidence: Evidence[]; onPick: (e: Evidence) => void }) {
  const byId = new Map(evidence.map((e) => [e.id, e]));
  const parts = text.split(/\[(E\d+)\]/);
  return (
    <>
      {parts.map((part, i) => {
        const e = i % 2 ? byId.get(part) : undefined;
        if (!e) {
          // 標記已帶單位;模型又在後面寫一次單位(「[E11] 張」)就拿掉,免得顯示成「12 張 張」
          const prev = i % 2 === 0 && i > 0 ? byId.get(parts[i - 1]) : undefined;
          return i % 2 ? `[${part}]` : prev ? part.replace(UNIT_AFTER[prev.unit], "") : part;
        }
        return (
          <button key={i} type="button" className="ev" title={`${e.plan_id} ${METRIC_LABEL[e.metric]}・點看來源`} onClick={() => onPick(e)}>
            {e.display}
          </button>
        );
      })}
    </>
  );
}

function EvidenceDetail({ e, onClose }: { e: Evidence; onClose: () => void }) {
  return (
    <p className="ev-detail" role="status">
      <strong>
        {e.plan_id} {METRIC_LABEL[e.metric]} = {e.display}
      </strong>
      <span>證據 {e.id}</span>
      <span>快照 v{e.snapshot_version}</span>
      <span>政策 {e.policy_version}</span>
      <span>引擎 {e.engine_version}</span>
      <button type="button" className="link" onClick={onClose}>
        關閉
      </button>
    </p>
  );
}

/** 等 AI 時的跳動圓點;Gemma 要 4–12 秒,附上已等秒數讓主管知道沒有當掉。 */
function Thinking({ label }: { label: string }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="thinking" role="status">
      <span className="dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {label}
      {seconds > 0 && <small>{seconds} 秒</small>}
    </span>
  );
}

/** AI 摘要。方案指標一律以證據標記引用、已核對;AI 只負責講成白話。 */
export default function AiPanel({ incident, aiEnabled }: { incident: Incident; aiEnabled: boolean }) {
  const [picked, setPicked] = useState<Evidence | null>(null);
  const brief = incident.ai_brief;
  if (!brief) return null;
  // 摘要寫於決策前;套用後保留原文當歷史,不隨後續進度改寫
  const historical = incident.applied_plan_id !== null;

  return (
    <section className="card ai">
      {historical && <p className="eyebrow">決策當時的摘要・{timeOfDay(brief.at)}</p>}
      <header className="card-head split">
        <h2>{brief.headline}</h2>
        <span
          className={`chip ${brief.source === "ai" ? "good" : ""}`}
          title={brief.note ?? (brief.source === "ai" ? "方案數字皆已核對方案、指標、單位與版本" : undefined)}
        >
          {brief.source === "ai" ? `✦ AI・${brief.model}` : aiEnabled && !brief.note ? <Thinking label="AI 撰寫中" /> : "規則摘要"}
        </span>
      </header>
      {brief.reasons.length > 0 && (
        <ul className="bullets">
          {brief.reasons.map((r) => (
            <li key={r}>
              <Cited text={r} evidence={brief.evidence} onPick={setPicked} />
            </li>
          ))}
        </ul>
      )}
      {brief.watch.length > 0 && (
        <ul className={`watch${historical ? " past" : ""}`}>
          {brief.watch.map((w) => (
            <li key={w}>
              <Cited text={w} evidence={brief.evidence} onPick={setPicked} />
            </li>
          ))}
        </ul>
      )}
      {picked && <EvidenceDetail e={picked} onClose={() => setPicked(null)} />}
      {brief.source === "rules" && brief.note && <p className="footnote">AI 沒有完成:{brief.note}</p>}
    </section>
  );
}

/** 問 AI;放在右側欄,看方案、甘特圖時都能對照著問。 */
export function AiChat({ incident, aiEnabled, locked }: { incident: Incident; aiEnabled: boolean; locked: boolean }) {
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<Evidence | null>(null);
  const scroller = useRef<HTMLDivElement>(null);

  // 新回答或等待中時把問答捲到最下面;只捲問答區,不動整頁
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [incident.chat.length, pending]);

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
    <section className="card chat-panel" aria-label="問 AI">
      <header className="card-head split">
        <h2>問 AI</h2>
        <small>{incident.id}</small>
      </header>
      <div className="chat-scroll" ref={scroller}>
        {incident.chat.length === 0 && !pending && <p className="hint">針對這個事件的方案提問</p>}
        {incident.chat.length > 0 && (
          <ol className="chat">
            {incident.chat.map((turn, i) => (
              <li key={i}>
                <p className="q">{turn.question}</p>
                <p className={turn.source === "ai" ? "a" : "a muted"}>
                  <Cited text={turn.answer} evidence={turn.evidence ?? []} onPick={setPicked} />
                </p>
              </li>
            ))}
          </ol>
        )}
        {pending && (
          <div className="chat-pending">
            <p className="q">{pending}</p>
            <Thinking label="AI 思考中" />
          </div>
        )}
      </div>
      {picked && <EvidenceDetail e={picked} onClose={() => setPicked(null)} />}
      <div className="quick">
        {QUICK.map((q) => (
          <button key={q} type="button" disabled={locked || !!pending} onClick={() => ask(q)}>
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
        <button type="submit" className="primary" disabled={locked || !!pending || !draft.trim()}>
          問
        </button>
      </form>
      {error && <p className="footnote">✕ {error}</p>}
    </section>
  );
}
