export type Json =
  | null
  | string
  | number
  | boolean
  | Json[]
  | { [key: string]: Json };
export type Mode = "live" | "mock";
export interface Audience {
  audience_id: string;
  version: string;
  description: string;
  interests: string[];
  assumed_knowledge: string[];
  examples: string[];
  assumptions: string[];
}
export interface Config {
  audiences: Audience[];
  rubric: {
    version: string;
    profiles: Record<string, string[]>;
    questions: Record<string, { label: string; criteria: string[] }>;
  };
  limits: Record<string, unknown>;
}
export interface Session {
  csrf_token: string;
  live_configured: boolean;
  spending: Record<string, unknown>;
  forecast: { available: boolean; reasons: string[] };
}
export interface Candidate {
  candidate_id: string;
  candidate_version: number;
  text: string;
  language: string;
  post_type: string;
  author_id: string | null;
  synthetic: boolean;
  media?: { kind: string; essential: boolean; description?: unknown };
  [key: string]: unknown;
}
export interface Factor {
  question_id: string;
  type: "score" | "choice" | "noul";
  assessability: string;
  score: number | null;
  choice: string | null;
  noul: number | null;
  confidence: number | null;
  probabilities: Record<string, number>;
  legend: Record<string, string>;
  evidence_references: string[];
  error?: string | null;
}
export interface Judgment {
  judgment_id: string;
  candidate_id: string;
  candidate_version: number;
  status: string;
  execution_mode: Mode;
  mode: string;
  profile_id: string;
  audience_id: string;
  audience_version: string;
  rubric_version: string;
  score_continuous: number | null;
  score_1_to_5: number | null;
  quality: number | null;
  risk_penalty: number | null;
  breakout_probability: number | null;
  calibration_status: string;
  factors: Record<string, Factor>;
  review_flags: string[];
  explanation: Record<string, unknown>;
  provenance: Record<string, unknown>;
  model_requested: string;
  model_returned: string | null;
  sdk_version: string;
  input_hash: string;
  reference_set_hash: string;
  code_commit: string;
  created_at: string;
  estimated_cost: number;
  latency: number | null;
  attempt_count: number;
  error_category: string | null;
  cached: boolean;
  [key: string]: unknown;
}
export interface JudgeRequest {
  candidate: Record<string, unknown>;
  context: Record<string, unknown>;
  audience_id: string;
  profile_id: string;
  execution_mode: Mode;
}
export interface Inspection {
  judgment: Judgment;
  request: JudgeRequest;
  state: unknown;
  questions: unknown;
}
export interface Job {
  job_id: string;
  status: string;
  items: {
    candidate_key: string;
    status: string;
    judgment_id?: string;
    error?: unknown;
  }[];
  [key: string]: unknown;
}
export type Report = Record<string, unknown>;
