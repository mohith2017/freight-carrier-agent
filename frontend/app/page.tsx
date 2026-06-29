"use client";

import { useEffect, useRef, useState } from "react";
import ResultView from "@/components/ResultView";
import { getHealth, streamQuery } from "@/lib/api";
import type { Health, QueryResponse } from "@/lib/types";

const EXAMPLES = [
  "What is the best rate on offer for load #29372289, and how does it compare to market?",
  "Which carriers have confirmed availability for PA to NJ Box Truck loads?",
  "Draft a reply to the carrier with the best rate on load #29372289 confirming next steps.",
  "Is MC 123456 cleared to book, or are there compliance issues?",
];

export default function Home() {
  const [question, setQuestion] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [liveTools, setLiveTools] = useState<string[]>([]);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  async function ask(q: string) {
    const text = q.trim();
    if (!text || streaming) return;
    setStreaming(true);
    setResult(null);
    setError(null);
    setLiveTools([]);
    setStatus("thinking");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamQuery(
        text,
        {
          onTool: (tool) => setLiveTools((prev) => [...prev, tool]),
          onResult: (r) => setResult(r),
          onError: (detail) => setError(detail),
        },
        controller.signal,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "request failed");
    } finally {
      setStreaming(false);
      setStatus(null);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    ask(question);
  }

  return (
    <main className="wrap">
      <header className="app">
        <div>
          <h1>Freight Carrier Agent</h1>
          <div className="sub">
            Intake assistant for a broker&apos;s inbound carrier queue — emails + calls,
            retrieval, and typed tools.
          </div>
        </div>
        <div className="badges">
          {health ? (
            <>
              <span className="badge ok">api: {health.status}</span>
              <span className="badge">store: {health.store}</span>
              <span className="badge">{health.agent_model}</span>
              <span className={`badge ${health.llm_configured ? "ok" : "off"}`}>
                {health.llm_configured ? "llm: ready" : "llm: no key"}
              </span>
            </>
          ) : (
            <span className="badge off">api: offline</span>
          )}
        </div>
      </header>

      <div className="examples">
        {EXAMPLES.map((ex) => (
          <button
            key={ex}
            className="example"
            onClick={() => {
              setQuestion(ex);
              ask(ex);
            }}
            disabled={streaming}
          >
            {ex.length > 64 ? ex.slice(0, 61) + "…" : ex}
          </button>
        ))}
      </div>

      <form className="ask" onSubmit={onSubmit}>
        <textarea
          className="q"
          placeholder="Ask about a load, carrier, rate, availability, or request a draft reply…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) onSubmit(e);
          }}
        />
        <button className="send" type="submit" disabled={streaming || !question.trim()}>
          {streaming ? "…" : "Ask"}
        </button>
      </form>

      {streaming && (
        <div className="card">
          <div className="thinking">
            <span className="dot" />
            {status === "thinking" ? "Agent is reasoning over the data…" : "Working…"}
          </div>
          {liveTools.length > 0 && (
            <div className="live-tools">
              {liveTools.map((t, i) => (
                <span className="live-tool" key={i}>
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="card">
          <div className="error">Error: {error}</div>
        </div>
      )}

      {result && <ResultView result={result} />}
    </main>
  );
}
