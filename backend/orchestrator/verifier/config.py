# backend/orchestrator/verifier/config.py
PASS_THRESHOLD = 8       # score >= 8 → pass
RETRY_THRESHOLD = 4      # score 4-7 → soft retry; score 0-3 → hard escalate
MAX_SOFT_RETRIES = 2     # max same-worker retries before hard escalation

VERIFIER_MODEL = "claude-haiku-4-5-20251001"
