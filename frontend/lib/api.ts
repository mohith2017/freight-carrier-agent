import type { Health, QueryResponse } from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

export async function getHealth(): Promise<Health> {
  const r = await fetch(`${API_URL}/health`, { cache: "no-store" });
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export interface StreamHandlers {
  onStatus?: (state: string) => void;
  onTool?: (tool: string, args: Record<string, unknown>) => void;
  onResult?: (result: QueryResponse) => void;
  onError?: (detail: string) => void;
}

export async function streamQuery(
  question: string,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_URL}/query`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question }),
    signal,
  });

  if (!res.ok || !res.body) {
    handlers.onError?.(`request failed (${res.status})`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");

    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      dispatchFrame(frame, handlers);
    }
  }
}

function dispatchFrame(frame: string, handlers: StreamHandlers): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const raw of frame.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  const data = dataLines.join("\n");
  if (!data) return;

  try {
    const parsed = JSON.parse(data);
    if (event === "status") handlers.onStatus?.(parsed.state ?? "");
    else if (event === "tool") handlers.onTool?.(parsed.tool, parsed.args ?? {});
    else if (event === "result") handlers.onResult?.(parsed as QueryResponse);
    else if (event === "error") handlers.onError?.(parsed.detail ?? "error");
  } catch {
    /* ignore non-JSON keep-alive frames */
  }
}
