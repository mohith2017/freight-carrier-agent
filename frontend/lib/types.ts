export interface ToolCall {
  tool: string;
  args: Record<string, unknown>;
  result_summary: string | null;
}

export interface QueryResponse {
  answer: string;
  supporting_records: string[];
  confidence: number;
  follow_up_needed: boolean;
  draft_email: string | null;
  tool_calls: ToolCall[];
}

export interface Health {
  status: string;
  llm_configured: boolean;
  store: string;
  agent_model: string;
}

export interface Evidence {
  event_id?: number | null;
  source_type?: string | null;
  source_id?: string | null;
  load_id?: string | null;
  carrier_id?: number | null;
  text?: string;
  score?: number;
}
