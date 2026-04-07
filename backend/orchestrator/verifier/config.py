# backend/orchestrator/verifier/config.py
PASS_THRESHOLD = 7       # score >= 7 → pass (was 8 — fewer retries)
RETRY_THRESHOLD = 5      # score 5-6 → soft retry; score 0-4 → escalate (was 4)
MAX_SOFT_RETRIES = 1     # max same-worker retries before hard escalation (was 2)

VERIFIER_MODEL = "claude-haiku-4-5-20251001"
