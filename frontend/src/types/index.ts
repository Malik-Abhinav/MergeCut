export type ClaimStatus = "preserved" | "degraded" | "broken";
export type Interaction =
  | "none"
  | "amplifies_existing_issue"
  | "creates_new_conflict";

export interface Evidence {
  start: number;
  end: number;
  description: string;
}

export interface ClaimEffect {
  status: ClaimStatus;
  rationale: string;
  evidence: Evidence[];
}

export interface ClaimResult {
  claim_id: string;
  claim: string;
  claim_type: string;
  importance: string;
  base_evidence: Evidence[];
  branch_a: ClaimEffect;
  branch_b: ClaimEffect;
  combined: ClaimEffect;
  interaction: Interaction;
  deterministic_rule: string;
  explanation: string | null;
}

export interface CombinedSlice {
  base_index: number;
  start: number;
  end: number;
  verdict: string;
  text: string;
  reason: string;
}

export interface AnalysisResult {
  conflict_detected: boolean;
  interaction: Interaction;
  overall_impact: ClaimStatus;
  overall_confidence: number;
  summary: string;
  provider: string;
  model: string;
  claims: ClaimResult[];
  combined_timeline: CombinedSlice[];
}
