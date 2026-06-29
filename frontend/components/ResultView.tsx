"use client";

import { useMemo, useState } from "react";
import type { Evidence, QueryResponse, ToolCall } from "@/lib/types";

function parseEvidence(tool_calls: ToolCall[]): Evidence[] {
  const out: Evidence[] = [];
  for (const tc of tool_calls) {
    if (tc.tool !== "search_communications" || !tc.result_summary) continue;
    try {
      const rows = JSON.parse(tc.result_summary);
      if (Array.isArray(rows)) {
        for (const r of rows) {
          if (r && typeof r === "object" && "text" in r) out.push(r as Evidence);
        }
      }
    } catch {
    }
  }
  return out;
}

function DraftEmail({ draft }: { draft: string }) {
  const [text, setText] = useState(draft);
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked; user can still select text */
    }
  }

  return (
    <div className="card draft">
      <div className="row">
        <p className="section-title" style={{ margin: 0 }}>
          Draft reply
        </p>
        <button className="copy" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <textarea value={text} onChange={(e) => setText(e.target.value)} />
    </div>
  );
}

export default function ResultView({ result }: { result: QueryResponse }) {
  const evidence = useMemo(() => parseEvidence(result.tool_calls), [result]);
  const confPct = Math.round((result.confidence ?? 0) * 100);

  return (
    <>
      <div className="card">
        <div className="answer">{result.answer}</div>

        <div className="meta">
          <div className="conf">
            confidence
            <span className="bar">
              <span style={{ width: `${confPct}%` }} />
            </span>
            {confPct}%
          </div>
          {result.follow_up_needed ? (
            <span className="pill followup">follow-up needed</span>
          ) : (
            <span className="pill clear">no follow-up</span>
          )}
        </div>

        {result.supporting_records.length > 0 && (
          <div className="meta">
            <span className="conf">records</span>
            <div className="records">
              {result.supporting_records.map((r, i) => (
                <span className="rec" key={i}>
                  {r}
                </span>
              ))}
            </div>
          </div>
        )}

        {result.tool_calls.length > 0 && (
          <details className="trace">
            <summary>{result.tool_calls.length} tool call(s)</summary>
            {result.tool_calls.map((tc, i) => (
              <div className="tool" key={i}>
                <div className="name">{tc.tool}</div>
                <div className="args">{JSON.stringify(tc.args)}</div>
                {tc.result_summary && (
                  <div className="summary">{tc.result_summary}</div>
                )}
              </div>
            ))}
          </details>
        )}
      </div>

      {evidence.length > 0 && (
        <div className="card">
          <p className="section-title">Evidence ({evidence.length})</p>
          {evidence.map((e, i) => (
            <div className="evidence" key={i}>
              <div className="src">
                <span>{e.source_type ?? "comm"}</span>
                {e.source_id && <span>· {e.source_id}</span>}
                {e.load_id && <span>· load {e.load_id}</span>}
                {typeof e.score === "number" && <span>· score {e.score}</span>}
              </div>
              <div className="txt">{e.text}</div>
            </div>
          ))}
        </div>
      )}

      {result.draft_email && <DraftEmail draft={result.draft_email} />}
    </>
  );
}
