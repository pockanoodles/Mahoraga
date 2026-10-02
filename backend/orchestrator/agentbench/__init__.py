"""Agent bench — which coding agent + model can fix bugs in *your* repo?

A repo's own commit history becomes the benchmark. For a commit that changed
both source and tests, the task is its parent plus the commit's test changes:
the tests are present, the fix is not. An agent gets the commit message and
the test file names; an attempt is resolved only when the commit's tests pass
AND nothing that passed before has regressed across the full suite.

    mine     repo history -> gated tasks          (mine.py)
    agents   who attempts them                    (agents.py)
    runner   one attempt, isolated and graded     (runner.py)
    guard    only a healthy machine's attempts count  (guard.py, host.py)
    report   per-arm and per-task tables          (report.py)

All state lives under ~/.mahoraga-v2/agentbench/<repo>/; the benchmarked repo
is only ever read, through `git clone --shared`.
"""
