#!/usr/bin/env python3
"""Convert the HumanEval+ dataset into a Mahoraga verifiable bench bank.

Two subcommands:
  fetch — download the evalplus v0.1.10 release and gunzip it to
          experiments/humaneval_plus_raw.jsonl (skipped if present, --force to redo).
  build — emit experiments/prompts_humaneval_plus.jsonl (bank rows in the
          prompts_verifiable.jsonl schema, plus a task_id field the loader
          ignores) and experiments/prompts_humaneval_plus_refs.jsonl
          (task_id -> complete reference program).

Expected outputs are computed offline by executing the contract-augmented
canonical solution (prompt + contract + canonical_solution, the evalplus
construction) and calling the entry point on a deep copy of each input.
Inputs a contract rejects, that error/hang, or whose repr does not round-trip
through ast.literal_eval are dropped. Each task's final reference+tests script
is then executed via `python3 -c` (the same mechanics as verify_replay.run_case)
and must pass in <= 10s; slow tasks progressively halve their plus-input sample
down to a base-input-only floor before being excluded.

Stdlib only — no evalplus dependency.
"""
from __future__ import annotations

import argparse
import ast
import copy
import gzip
import json
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
RAW_PATH = _HERE / "humaneval_plus_raw.jsonl"
BANK_PATH = _HERE / "prompts_humaneval_plus.jsonl"
REFS_PATH = _HERE / "prompts_humaneval_plus_refs.jsonl"

RELEASE_URL = (
    "https://github.com/evalplus/humanevalplus_release/releases/download/"
    "v0.1.10/HumanEvalPlus.jsonl.gz"
)

MAX_ASSERTS = 64
CALL_TIMEOUT_S = 2.0
VERIFY_TIMEOUT_S = 10
VERIFY_SLOW_S = 5.0
CASE_REPR_BYTES = 8_000
MAX_SCRIPT_BYTES = 256_000

PROMPT_TEMPLATE = (
    "Complete the following Python function. Return the complete, runnable "
    "code — including the signature, any imports it needs, and the function "
    "body — in a single ```python code block.\n\n```python\n{stub}\n```"
)

MEQ_PRELUDE = '''\
def _meq(a, b, atol):
    if isinstance(a, float) or isinstance(b, float):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return abs(a - b) <= max(atol, 1e-6)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if type(a) is not type(b) or len(a) != len(b):
            return False
        return all(_meq(x, y, atol) for x, y in zip(a, b))
    return a == b
'''


class _CallTimeout(Exception):
    pass


def _raise_timeout(signum, frame):
    raise _CallTimeout


def _call_with_timeout(fn, args, seconds: float):
    old = signal.signal(signal.SIGALRM, _raise_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*args)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _roundtrips(value) -> bool:
    try:
        return ast.literal_eval(repr(value)) == value
    except (ValueError, SyntaxError, MemoryError, RecursionError, TypeError):
        return False


def _task_num(task_id: str) -> int:
    return int(task_id.rsplit("/", 1)[1])


def cmd_fetch(force: bool) -> None:
    if RAW_PATH.exists() and not force:
        print(f"{RAW_PATH} already exists — skipping (use --force to redownload)")
        return
    print(f"downloading {RELEASE_URL}")
    with urllib.request.urlopen(RELEASE_URL) as resp:
        raw = gzip.decompress(resp.read())
    RAW_PATH.write_bytes(raw)
    print(f"wrote {RAW_PATH} ({RAW_PATH.stat().st_size:,} bytes)")


def _load_raw() -> list[dict]:
    rows = [json.loads(line) for line in RAW_PATH.read_text().splitlines() if line.strip()]
    rows.sort(key=lambda r: _task_num(r["task_id"]))
    return rows


def _assign_tiers(rows: list[dict]) -> dict[str, str]:
    order = sorted(
        rows,
        key=lambda r: (len(r["canonical_solution"].splitlines()), _task_num(r["task_id"])),
    )
    n = len(order)
    cut1, cut2 = (n + 2) // 3, 2 * (n + 2) // 3
    tiers: dict[str, str] = {}
    for i, row in enumerate(order):
        tiers[row["task_id"]] = "easy" if i < cut1 else ("medium" if i < cut2 else "hard")
    return tiers


def _sample_evenly(items: list, k: int) -> list:
    if k <= 0:
        return []
    if len(items) <= k:
        return list(items)
    return [items[i * len(items) // k] for i in range(k)]


def _compute_cases(row: dict, drops: dict[str, int]) -> tuple[list, list]:
    """Return (base_cases, plus_cases) of (args, expected) with bad inputs dropped."""
    contract_program = row["prompt"] + row["contract"] + row["canonical_solution"]
    namespace: dict = {}
    exec(contract_program, namespace)
    fn = namespace[row["entry_point"]]

    base_inputs = row["base_input"][:MAX_ASSERTS]
    plus_inputs = _sample_evenly(row["plus_input"], MAX_ASSERTS - len(base_inputs))

    def evaluate(inputs: list) -> list:
        cases = []
        for args in inputs:
            if not _roundtrips(args):
                drops["repr_no_roundtrip"] += 1
                continue
            try:
                expected = _call_with_timeout(fn, copy.deepcopy(args), CALL_TIMEOUT_S)
            except AssertionError:
                drops["contract_rejected"] += 1
                continue
            except _CallTimeout:
                drops["call_timeout"] += 1
                continue
            except Exception:
                drops["call_error"] += 1
                continue
            if not _roundtrips(expected):
                drops["repr_no_roundtrip"] += 1
                continue
            if len(repr(args)) + len(repr(expected)) > CASE_REPR_BYTES:
                drops["repr_too_large"] += 1
                continue
            cases.append((args, expected))
        return cases

    return evaluate(base_inputs), evaluate(plus_inputs)


def _make_tests(entry_point: str, cases: list, atol: float) -> str:
    lines = [MEQ_PRELUDE]
    for args, expected in cases:
        lines.append(f"assert _meq({entry_point}(*{args!r}), {expected!r}, {atol!r})")
    return "\n".join(lines)


def _verify(reference: str, tests: str) -> tuple[bool, float, str]:
    script = reference + "\n\n" + tests
    start = time.perf_counter()
    try:
        proc = subprocess.run(
            ["python3", "-c", script],
            capture_output=True, text=True, timeout=VERIFY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, time.perf_counter() - start, f"timeout after {VERIFY_TIMEOUT_S}s"
    except OSError as exc:
        return False, time.perf_counter() - start, f"could not exec: {exc}"
    elapsed = time.perf_counter() - start
    if proc.returncode == 0:
        return True, elapsed, ""
    err = (proc.stderr or "").strip().splitlines()
    return False, elapsed, (err[-1] if err else "nonzero exit")


def cmd_build() -> None:
    if not RAW_PATH.exists():
        sys.exit(f"{RAW_PATH} missing — run `{sys.argv[0]} fetch` first")
    rows = _load_raw()
    tiers = _assign_tiers(rows)

    entry_points = [r["entry_point"] for r in rows]
    dup_eps = sorted({e for e in entry_points if entry_points.count(e) > 1})

    bank_rows: list[dict] = []
    ref_rows: list[dict] = []
    excluded: list[tuple[str, str]] = []
    drops = {
        "contract_rejected": 0, "repr_no_roundtrip": 0, "repr_too_large": 0,
        "call_timeout": 0, "call_error": 0,
    }
    halved_tasks = 0
    verify_wall = 0.0

    for row in rows:
        task_id = row["task_id"]
        stub = row["prompt"].rstrip()
        reference = row["prompt"] + row["canonical_solution"]

        try:
            base_cases, plus_cases = _compute_cases(row, drops)
        except Exception as exc:
            excluded.append((task_id, f"reference execution failed offline: {exc!r}"))
            continue
        if not base_cases and not plus_cases:
            excluded.append((task_id, "no usable inputs after drops"))
            continue

        halved = False
        tests = None
        while True:
            candidate = _make_tests(row["entry_point"], base_cases + plus_cases, row["atol"])
            if len(reference) + len(candidate) > MAX_SCRIPT_BYTES and plus_cases:
                # run_case grades via `python3 -c`, so the whole script must
                # stay well under ARG_MAX — treat oversize like a slow run.
                plus_cases = plus_cases[::2] if len(plus_cases) > 1 else []
                halved = True
                continue
            ok, elapsed, err = _verify(reference, candidate)
            verify_wall += elapsed
            if ok and elapsed <= VERIFY_SLOW_S:
                tests = candidate
                break
            if plus_cases:
                plus_cases = plus_cases[::2] if len(plus_cases) > 1 else []
                halved = True
                continue
            if ok:
                tests = candidate
                break
            excluded.append((task_id, f"base-input-only verification failed: {err}"))
            break
        if tests is None:
            continue
        halved_tasks += int(halved)

        bank_rows.append({
            "prompt": PROMPT_TEMPLATE.format(stub=stub),
            "bucket": "code",
            "tier": tiers[task_id],
            "entrypoint": row["entry_point"],
            "verify": "solution",
            "tests": tests,
            "task_id": task_id,
        })
        ref_rows.append({
            "task_id": task_id,
            "entrypoint": row["entry_point"],
            "reference": reference,
        })
        n_asserts = len(base_cases) + len(plus_cases)
        print(f"  {task_id}: {n_asserts} asserts, tier={tiers[task_id]}"
              + (" (plus sample halved)" if halved else ""))

    header = (
        "# experiments/prompts_humaneval_plus.jsonl — HumanEval+ converted to the\n"
        "# verifiable-bank schema by experiments/build_humaneval_bank.py. Each row:\n"
        "# {prompt, bucket, tier, entrypoint, verify, tests, task_id}; task_id is an\n"
        "# extra field verify_replay.load_bank ignores. Expected outputs computed\n"
        "# offline from the canonical solutions; guarded by\n"
        "# tests/orchestrator_v2/test_humaneval_bank.py.\n"
    )
    with open(BANK_PATH, "w") as f:
        f.write(header)
        for r in bank_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ref_header = (
        "# experiments/prompts_humaneval_plus_refs.jsonl — reference programs\n"
        "# (raw stub + canonical solution) for prompts_humaneval_plus.jsonl,\n"
        "# keyed by task_id. CI-enforced by tests/orchestrator_v2/test_humaneval_bank.py.\n"
    )
    with open(REFS_PATH, "w") as f:
        f.write(ref_header)
        for r in ref_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    assert_counts = sorted(
        sum(1 for line in r["tests"].splitlines() if line.startswith("assert "))
        for r in bank_rows
    )
    tier_split = {t: sum(1 for r in bank_rows if r["tier"] == t) for t in ("easy", "medium", "hard")}

    print("\n=== summary ===")
    print(f"tasks: {len(bank_rows)} included, {len(excluded)} excluded of {len(rows)}")
    for task_id, reason in excluded:
        print(f"  excluded {task_id}: {reason}")
    print(f"input drops: {drops}")
    print(f"tasks with halved plus sample: {halved_tasks}")
    if assert_counts:
        mid = assert_counts[len(assert_counts) // 2]
        print(f"asserts/task: min={assert_counts[0]} median={mid} max={assert_counts[-1]}")
    print(f"tier split: {tier_split}")
    print(f"duplicate entry_points across raw dataset: {dup_eps or 'none'}")
    print(f"bank: {BANK_PATH} ({BANK_PATH.stat().st_size:,} bytes)")
    print(f"refs: {REFS_PATH} ({REFS_PATH.stat().st_size:,} bytes)")
    print(f"reference-verification wall time: {verify_wall:.1f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="download and gunzip the raw dataset")
    fetch.add_argument("--force", action="store_true", help="redownload even if present")
    sub.add_parser("build", help="emit bank + refs JSONL from the raw dataset")
    args = parser.parse_args()
    if args.command == "fetch":
        cmd_fetch(args.force)
    else:
        cmd_build()


if __name__ == "__main__":
    main()
