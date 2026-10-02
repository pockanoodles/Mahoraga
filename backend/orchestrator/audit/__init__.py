"""The audit: is local coding AI worth it for this user's work, on this machine?

Sources produce tasks, runners attempt them, graders decide them (all in
agentbench for v1, which is commit-mined and test-graded). This package turns
the measurements into the user's answer:

    pricing   dated, sourced cloud plan prices (pricing.json)
    verdict   stay / split / switch, with what would change it

ADR: brain/decisions/2026-10-02-local-audit-product.md
"""
