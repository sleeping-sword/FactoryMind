"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import IncidentView from "./IncidentView";
import Overview from "./Overview";
import { ApiError, api } from "@/lib/api";
import { STATUS } from "@/lib/format";
import type { FactoryState, Incident } from "@/lib/types";

const POLL_MS = 1500;

export default function App() {
  const [state, setState] = useState<FactoryState | null>(null);
  const [offline, setOffline] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [incident, setIncident] = useState<Incident | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const known = useRef(new Set<string>());
  const loaded = useRef("");

  const refresh = useCallback(async () => {
    try {
      const next = await api.state();
      setState(next);
      setOffline(false);
      // 新事件進來時自動帶主管過去看,但不打斷正在看的事件
      const fresh = next.incidents.find((i) => !known.current.has(i.id));
      next.incidents.forEach((i) => known.current.add(i.id));
      if (fresh) setSelected((current) => current ?? fresh.id);
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
      try {
        await action();
      } catch (error) {
        setNotice(error instanceof ApiError ? error.message : "操作沒有成功,請確認後端服務是否正常。");
      }
      loaded.current = "";
      await refresh();
    },
    [refresh],
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
          <span className={`chip ${state.running ? "good" : ""}`}>{state.running ? "● 運轉中" : "‖ 已暫停"}</span>
          {state.running ? (
            <button type="button" onClick={() => act(api.pause)}>暫停</button>
          ) : (
            <button type="button" className="primary" onClick={() => act(api.start)}>開始運轉</button>
          )}
          <button type="button" onClick={() => act(async () => setSelected((await api.emit()).id))} title="不等虛擬工廠,立刻發生下一個事件">
            發生事件
          </button>
          <button
            type="button"
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
        {offline && <b>・連線中斷</b>}
      </p>

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
        </nav>

        <div className="content">
          {selected === null || !summary ? (
            <Overview state={state} onOpen={setSelected} />
          ) : incident && incident.id === selected ? (
            <IncidentView incident={incident} policy={state.policy} act={act} />
          ) : (
            <section className="card empty"><h2>讀取事件中…</h2></section>
          )}
        </div>
      </div>
    </main>
  );
}
