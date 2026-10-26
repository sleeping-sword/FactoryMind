"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import IncidentView from "./IncidentView";
import Overview from "./Overview";
import { ApiError, api } from "@/lib/api";
import { NEEDS_DECISION, STAGES, STATUS, timeOfDay } from "@/lib/format";
import type { FactoryState, Incident, Status } from "@/lib/types";

const POLL_MS = 1500;

export default function App() {
  const [state, setState] = useState<FactoryState | null>(null);
  const [offline, setOffline] = useState(false);
  const [syncedAt, setSyncedAt] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [moved, setMoved] = useState<string | null>(null);
  const known = useRef(new Set<string>());
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const lastStatus = useRef(new Map<string, Status>());
  const loaded = useRef("");

  const refresh = useCallback(async () => {
    try {
      const next = await api.state();
      setState(next);
      setOffline(false);
      setSyncedAt(new Date().toTimeString().slice(0, 8));
      // 新事件進來時自動帶主管過去看,但不打斷還要做決定的事件;
      // 正在看的事件剛處理完(或早已處理完)而有下一件等決定時,帶到最早的那一件
      const fresh = next.incidents.find((i) => !known.current.has(i.id));
      next.incidents.forEach((i) => known.current.add(i.id));
      const current = selectedRef.current;
      const viewing = next.incidents.find((i) => i.id === current);
      const before = current ? lastStatus.current.get(current) : undefined;
      const justDone = !!viewing && !!before && NEEDS_DECISION.has(before) && !NEEDS_DECISION.has(viewing.status);
      const idle = !viewing || !NEEDS_DECISION.has(viewing.status);
      const waiting = next.incidents.find((i) => i.id !== current && NEEDS_DECISION.has(i.status));
      if (current === null && fresh) setSelected(fresh.id);
      else if (waiting && (justDone || (fresh && idle))) {
        setSelected(waiting.id);
        setMoved(`${current} 已處理,帶你到下一件 ${waiting.id}`);
      }
      lastStatus.current = new Map(next.incidents.map((i) => [i.id, i.status]));
    } catch {
      setOffline(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [refresh]);

  const summary = state?.incidents.find((i) => i.id === selected);
  const fingerprint = summary ? `${summary.id}:${summary.status}:${summary.trace_length}:${state!.revision}` : "";
  useEffect(() => {
    if (!summary) {
      setIncident(null);
      loaded.current = "";
      return;
    }
    if (loaded.current === fingerprint) return;
    loaded.current = fingerprint;
    api.incident(summary.id).then(setIncident, () => undefined);
  }, [summary, fingerprint]);

  /** 所有會改變狀態的操作都走這裡:後端擋下時把原因原樣顯示給主管。 */
  const act = useCallback(
    async (action: () => Promise<unknown>) => {
      setNotice(null);
      if (offline) {
        setNotice("連線中斷,畫面唯讀;恢復同步後再操作。");
        return;
      }
      try {
        await action();
      } catch (error) {
        setNotice(error instanceof ApiError ? error.message : "操作沒有成功,請確認後端服務是否正常。");
      }
      loaded.current = "";
      await refresh();
    },
    [refresh, offline],
  );

  if (!state) {
    return (
      <main className="shell">
        <section className="card empty">
          <h2>{offline ? "連不到虛擬工廠" : "正在連線到虛擬工廠…"}</h2>
          {offline && (
            <p>
              請先啟動後端:<code>cd backend &amp;&amp; .venv/bin/uvicorn factorymind.api:app --port 8000</code>
            </p>
          )}
        </section>
      </main>
    );
  }

  const waiting = state.incidents.filter((i) => STATUS[i.status].tone === "wait" || STATUS[i.status].tone === "critical");

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">FactoryMind · 決策介面</p>
          <h1>{state.snapshot.factory_id === "demo-injection-plant" ? "示範射出廠" : state.snapshot.factory_id}</h1>
        </div>
        <div className="controls">
          {/* 「已設定」只代表模型找得到,不代表每次呼叫都會成功;每次結果看摘要與問答上的來源標記 */}
          <span
            className={`chip ${state.ai.enabled ? "good" : ""}`}
            title={
              state.ai.note ??
              (state.ai.enabled ? `${state.ai.provider}・${state.ai.model_id ?? state.ai.model}` : "在 backend/.env 填入 GEMINI_API_KEY 後重啟後端")
            }
          >
            {state.ai.check === "missing" ? "AI 模型不可用" : state.ai.enabled ? "✦ AI 已設定" : "AI 未設定"}
          </span>
          <span className={`chip ${state.running ? "good" : ""}`}>{state.running ? "● 運轉中" : "‖ 已暫停"}</span>
          {state.backlog > 0 && <span className="chip wait" title="已發生、等你處理完目前事件後自動開啟">排隊 {state.backlog} 件</span>}
          {state.running ? (
            <button type="button" disabled={offline} onClick={() => act(api.pause)}>暫停</button>
          ) : (
            <button type="button" className="primary" disabled={offline} onClick={() => act(api.start)}>開始運轉</button>
          )}
          <button
            type="button"
            disabled={offline}
            onClick={() =>
              act(async () => {
                const { id } = await api.emit();
                if (id) setSelected(id);
                else setMoved("還有事件等你決定,這件先排隊,處理完會自動帶你過去");
              })
            }
            title="不等虛擬工廠,立刻發生下一個事件">
            發生事件
          </button>
          <button
            type="button"
            disabled={offline}
            onClick={() => {
              if (window.confirm("重設會清掉所有事件與決策紀錄,回到原始工廠。確定嗎?")) {
                known.current.clear();
                setSelected(null);
                act(api.reset);
              }
            }}
          >
            重設
          </button>
        </div>
      </header>

      <p className="identity">
        生產主管(示範)・<span className="warn">合成資料</span>
        {offline && <b>・連線中斷,畫面唯讀{syncedAt && `(最後同步 ${syncedAt})`}</b>}
      </p>

      {moved && (
        <p className="moved" role="status">
          → {moved}
          <button type="button" className="link" onClick={() => setMoved(null)}>知道了</button>
        </p>
      )}

      {notice && (
        <p className="alert" role="alert">
          <span aria-hidden>✕</span> {notice}
          <button type="button" className="link" onClick={() => setNotice(null)}>知道了</button>
        </p>
      )}

      <div className="layout">
        <nav className="sidebar" aria-label="事件清單">
          <button type="button" className={selected === null ? "is-current" : ""} onClick={() => setSelected(null)}>
            <b>營運總覽</b>
            <small>{waiting.length ? `${waiting.length} 件待處理` : "無待處理"}</small>
          </button>
          <p className="eyebrow">事件</p>
          {state.incidents.length === 0 && <p className="hint">尚無事件</p>}
          {[...state.incidents].reverse().map((i) => {
            const status = STATUS[i.status];
            return (
              <button key={i.id} type="button" className={selected === i.id ? "is-current" : ""} onClick={() => setSelected(i.id)}>
                <b>{i.title}</b>
                <small>
                  {i.id}・<span className={`dot ${status.tone}`}>{status.icon}</span> {status.label}
                </small>
              </button>
            );
          })}
          {/* 選中事件的處理歷程;放在側欄,看方案時隨時可展開對照 */}
          {incident && incident.id === selected && (
            <details className="trace-side">
              <summary>處理歷程({incident.trace.length})</summary>
              <ol className="trace">
                {incident.trace.map((entry) => (
                  <li key={entry.seq}>
                    <time>{timeOfDay(entry.at)}</time>
                    <span className="chip">{STAGES.find((s) => s.key === entry.stage)?.label}</span>
                    <p>
                      <b>{entry.actor}</b> {entry.message}
                    </p>
                  </li>
                ))}
              </ol>
            </details>
          )}
        </nav>

        <div className="content">
          {selected === null || !summary ? (
            <Overview state={state} onOpen={setSelected} />
          ) : incident && incident.id === selected ? (
            <IncidentView incident={incident} policy={state.policy} aiEnabled={state.ai.enabled} act={act} locked={offline} />
          ) : (
            <section className="card empty"><h2>讀取事件中…</h2></section>
          )}
        </div>
      </div>
    </main>
  );
}
