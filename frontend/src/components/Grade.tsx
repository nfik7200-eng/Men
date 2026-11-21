/** Evidence grades (docs/FRONTEND_ARCHITECTURE.md §6.4): text always shown; shape is a second channel. */
const GRADES: Record<string, string> = {
  verified: "Verified observation",
  corroborated: "Corroborated",
  blockchain_observation: "Observation",
  unverified: "Unverified observation",
  deterministic_rule: "Rule-based",
  rule_based: "Rule-based",
  model_prediction: "Model prediction",
  statistical_signal: "Statistical signal",
  ai_interpretation: "AI interpretation",
  hypothesis: "Hypothesis",
  missing_information: "Not established",
  insufficient_evidence: "Not established",
  conflicted: "Conflicted",
  invalidated: "Invalidated",
};

export function Grade({ kind, label }: { kind: string; label?: string }) {
  const known = kind in GRADES;
  const text = label ?? GRADES[kind] ?? kind.replace(/_/g, " ");
  return (
    <span className={`grade ${known ? `grade-${kind}` : "grade-unknown"}`}>
      <i aria-hidden="true" className="grade-mark" />
      {text}
    </span>
  );
}
