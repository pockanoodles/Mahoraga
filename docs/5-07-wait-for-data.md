What's left, in order of unblock-condition
F2.2 real double-run. Code-only — needs worker-dispatch surgery to actually run two adapters in parallel, but doesn't depend on data. ~1 day if the executor architecture cooperates.
F3 counterfactual estimation. Needs ~500+ organic decisions to calibrate against. Today's bootstrap data is too clean.
F4 composer flip. Needs F3 + the shadow telemetry signal we're already capturing. Counterfactual reward delta in orch analyze composer-counterfactual is the gate — when it's consistently positive over a week, flip MAHORAGA_COMPOSER_ENABLED=1.
L3.1 champion/challenger. Needs traffic to A/B against. Probably ships alongside F4.
R1.2/R1.3 episode-data integrity + bandit-state backup. Defensive code, low priority until something breaks.
R2 integration test suite. 7 E2E scenarios from the spec. Honestly already covered piecemeal by the +853 unit tests; could batch into the existing harness later.
The data-accumulation gate is the natural pause. Light daily use of /mahoraga for a week or two and the rest of the spec becomes implementable. Until then, the system is observable, guarded, replayable, analyzable — and that's the "trustworthy daily use" target you set at the top.