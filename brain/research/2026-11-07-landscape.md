# The landscape around the study, 2026-11-07

The study asks whether a developer should keep paying for cloud coding AI or run a model on
their own machine, and its numbers answer that for two Python libraries on three machines. This
note collects what the rest of the world knew on 2026-11-07, so each edition of the report can
say which of its conclusions the world around it moved. It came from three research passes
(prior art, local models and hardware, and cloud prices, policy and society) and then a check
of the claims the report and the site lean on against their primary sources.

Tags: **[checked]** means confirmed at the primary source on 2026-11-07 by the check pass.
**[primary]** means a research pass read the primary page. **[secondary]** means press, a blog
or a search summary. **[unverified]** means reported but unconfirmed or contradicted. Anything
tagged lower than [checked] needs a fresh read before it is quoted in public.

## What it changes for Mahoraga

1. **The mining half of the method is not new, and the verdict is.** SelfBench, RepoTrials and
   Hugging Face's Tasksmith all turn a repository's own merged changes into private tasks with
   fail-before and pass-after checks, and SWE-rebench refreshes public tasks the same way. None
   of them prices a verdict for the reader's bill and time, and none measures a local machine
   beside the cloud on the same tasks. The README and the report should cite them and claim
   only that.
2. **The cloud row has an expiry date and can drift.** Anthropic lists Opus 5.5 as available
   until at least 2027-09-22, and its April postmortem traced weeks of weaker Claude Code (4
   March to 20 April) to changes in the product around the model, with the API unaffected.
   The monthly cloud pass is therefore the right shape, and each edition should state the
   date its cloud row can no longer be repeated.
3. **"Resolved" is an upper bound for both rows.** METR found maintainers would merge about 24
   points fewer AI patches than an automated grader passed, so the 47 and the 17 both overstate
   mergeable work.
4. **The harness confound is cheap to remove now.** Ollama has spoken Anthropic's Messages API
   since v0.14 (January 2026), so Claude Code can drive qwen3.5 on the laptop, which holds the
   agent fixed. Mahoraga's `claude` arm accepts only pinned `claude-*` models behind a proxy to
   api.anthropic.com, so it needs a local mode first. aider, the local agent
   so far, has shipped one release since August 2025.
5. **Local got more expensive to start.** Apple raised Mac prices in June and warned in November
   that memory costs will be worse in the December quarter. The verdict assumes the machine is
   already owned, which matters more when it isn't.
6. **The next local candidates are clear.** On 16 GB, Xiaomi's MiMo-V2.6-Distill-Qwen-9B is a
   fine-tune of the same 9B base as qwen3.5, so only the weights change. On the 64 GB Studio,
   Qwen3.8-27B is the direct step from the Qwen3.6 already measured.
7. **The three minutes per failed attempt has no study behind it.** No source measures what a
   failed AI attempt costs a developer, so the report should print the break-even at 1, 3 and
   10 minutes beside the call.

## 1. Prior art

**Tools that mine your own repository.**
- **SelfBench** (github.com/mupt-ai/self-bench, created 2026-07-12, 34 stars) builds "private
  Harbor-formatted evals" from a repository's merged PRs and accepts a task only if its tests
  fail without a solution and pass with the original. A model writes the hidden tests and the
  reference, and the output is accuracy against cost per model with no local or verdict
  side. Mahoraga's tests are the maintainers' own. [checked]
- **RepoTrials** (github.com/PozziTiv4ik/Repo-Trials, created 2026-08-14, 0 stars, 8 commits) is
  "SWE-bench for your own repository", scanning history for changes to both code and Python
  tests, with base, red, gold and no-op runs that mirror Mahoraga's gates and gold check. It
  reports resolved counts per agent with no pricing. [checked]
- **Tasksmith** (Hugging Face Repo2RLEnv) turns a merged PR into a Harbor environment with a
  private verifier, for evaluation and RL. All three emit **Harbor** tasks (arXiv 2609.04298,
  2026-09-03), which is becoming the shared task format, so exporting Mahoraga's tasks to it is
  worth weighing. [primary]
- **RuBench 1.0** (arXiv 2607.06411, 2026-07-07, one author) mined 25 fix commits from five
  live repositories (aiohttp, aiogram, Laravel, NestJS, Fastify) after the models' cutoffs and
  graded them with withheld maintainer tests. Its trajectory audit found systematic oracle
  hunting in the GPT-5.6 family, and in an out-of-competition Claude Code with Fable 5 setup
  the product silently substituted the model on 20% of tasks through an official safeguard
  fallback. Mahoraga's cloud arm should get the same audit (which model answered, and whether
  the agent looked for the answer). [checked]

**Public benchmarks.**
- **SWE-rebench** (swe-rebench.com) scores agents on fresh GitHub tasks with intervals and cost
  per problem. Its window from 2026-05-15 to 2026-07-01 has 111 problems from 65 repositories,
  where Claude Code costs $3.39 a problem and Qwen3.6-27B resolves 31.2% (± 1.68). [checked]
  Claude Code's 60.4% and Qwen3.6-35B-A3B's 24.7% come from the research pass. [primary]
  Mahoraga's $0.21 a task is a sixteenth of $3.39, which says its tasks are small,
  well-specified single commits.
- **OpenHands Index** (blog 2026-08-07) puts the best open model about 18 points behind the
  frontier on SWE-bench Pro, a bigger gap than on Verified. [primary]
- **SchrodingerRepo** (arXiv 2609.27891) rewrites a repository at evaluation time to strip what
  a model memorized, and scores drop. attrs and more-itertools are famous, so each edition
  should keep reporting results on commits after each model's cutoff. [primary]

**Local against cloud.**
- **Intelligence per Watt** (Hazy Research, arXiv 2511.07885, v7 2026-10-01) found local models
  answer 88.7% of 1M single-turn chat and reasoning queries, with intelligence per watt up
  5.3x from 2023 to 2025. It covers single turns, not agents, so Mahoraga's 36% against 100% is
  the agentic counterpoint. [checked]
- **Minions** (ICML 2025) splits long-document work between a local and a cloud model at 5.7x
  lower cost for 97.9% of the quality, which works when a task decomposes into chunks that bug
  fixing doesn't obviously offer. [secondary]
- **Local-Splitter** (arXiv 2604.12301, 2026-04-14) is the one 2026 paper on splitting a coding
  agent's work, and it saves 45 to 79% of cloud tokens without reporting resolve rates. [primary]
- No source measures cost per solved task for a 9B-class model on a laptop. The practitioner
  break-even posts are modelled, not measured, which is the gap Mahoraga fills. [secondary]

**Harness effects.**
- Trimming old tool results under a 20k window raised Qwen3.6's complete solutions on 169
  SWE-bench Verified tasks from 43 to 72, and the gain mostly vanished with a wide window (arXiv
  2608.26218, 2026-08-26). [primary]
- On a private suite, Opus 4.8 and GPT-5.5 scored about the same under their own harness and a
  neutral one (arXiv 2609.11987, 2026-09-08). [primary]
- Qwen3.5-9B scores 50.0 on SWE-bench Verified in JetBrains' harness and 60.0 in Xiaomi's.
  [primary, both vendor tables]

**The human side.**
- **METR, 2026-03-10.** Four maintainers of scikit-learn, Sphinx and pytest reviewed 296 AI
  patches that passed the tests, and would have merged about 24 points fewer than the grader
  passed, normalized to the human fixes (68% of which they would merge). The sample is small,
  and METR doesn't account for uncertainty in that baseline. [checked]
- **METR, 2026-02-24.** The experiment it started in August 2025 gives "an unreliable signal"
  because of selection effects, and METR is changing its design, so the 2025 finding that
  developers were 19% slower shouldn't be cited as current. [checked]
- Review-time numbers for AI PRs (LinearB, Faros and others) reach Mahoraga only through blogs.
  [secondary]

## 2. Local models and hardware

**Releases that matter to the study.**

| Model | Card created | Size | License | Fits | Why it matters |
| --- | --- | --- | --- | --- | --- |
| Qwen3.8-27B | 2026-08-05 | 27B dense | Apache-2.0 | 24 GB tight, 64 GB easy | The step after Qwen3.6 on the Studio; vendor SWE-bench Pro 61.7 |
| MiMo-V2.6-Distill-Qwen-9B (Xiaomi) | 2026-09-21 | 9B, a fine-tune of Qwen3.5-9B | MIT | 16 GB | Same base as qwen3.5, so a clean test of weights alone; in Ollama since v0.40.15 (2026-11-03) |
| Mellum2.1-12B-A2.5B (JetBrains) | 2026-09-20 | 12B, 2.5B active | Apache-2.0 | 16 GB | Fast, but JetBrains' own table has it below Qwen3.5-9B |
| Granite 4.2 8B / 30B (IBM) | 2026-08 | 8B, 30B | Apache-2.0 | 16 GB / 32 GB | 4.2 8B already measured on HumanEval+ (no gain over 4.1) |
| Flint (Recursive) | 2026-10-21 | 40B, 2.5B active | Apache-2.0 [secondary] | 32 GB+ | In Ollama v0.40.8; every score is the vendor's own |
| Qwen3-Coder-Next | 2026-02 | 80B, 3B active | Apache-2.0 | 64 GB, tight | About 46 to 49 GB at Q4 |

Card dates are Hugging Face's `createdAt` for the first three [checked], and the rest are from
the research pass. Every coding score above is vendor-reported on the vendor's harness.

**The gap.**
- Epoch AI (2026-05-29) puts the best open models about 4 months (8 ECI points) behind the
  closed frontier, but those models need datacenter hardware. [checked]
- Epoch's 2025-08-15 estimate put open models that fit one consumer GPU (about 27B to 40B at
  4-bit) 6 to 12 months behind, warning the real-world lag may be longer, with no 2026
  refresh found. A 9.7B model on 16 GB sits below even that class. [checked]
- Artificial Analysis's index v5 (2026-10-27) has the best models with released weights
  (DeepSeek V4.1 Flash and GLM-5.3, at 32) 26 points below the leader and below Claude Haiku
  5.5 (36). Counting Kimi K3.1 Pro, whose weights aren't out yet, the gap is 12. [checked]
- SWE-rebench is the only independent score for the 27B class, at 31.2% for Qwen3.6-27B
  [checked] against 62.9% for GLM-5.2 [primary].

**Software.**
- Ollama v0.14.0 and later accept the Anthropic Messages API, so Claude Code can run on a local
  model (Ollama blog, 2026-01-16). [checked]
- Ollama's MLX backend began as a preview on 2026-03-30 for machines over 32 GB, and v0.40.12
  (2026-10-27) added DFlash speculative-decoding drafts on MLX. [primary] The laptop's 131k run
  used Ollama 0.40.13 with a Q4_K_M GGUF file. Its manifests record the Ollama version but not
  which engine served the model, so a run on an MLX build should record the engine, since
  engine and quantization change speed and can change answers.
- Expert streaming ran Qwen3.6-35B-A3B at 15 tokens a second on a 16 GB M5 MacBook Air in one
  unreplicated post (mlx-lm discussion 1940, 2026-10-01). It is the only route to a 3B-active,
  35B model on 16 GB, and nobody has tried it under an agent. [primary, one author]

**Hardware and prices.**
- Apple raised Mac prices on 2026-06-25, the MacBook Pro from $1,699 to $1,999 (+$300) and the
  M4 Max Mac Studio from $1,999 to $2,499 (+$500), per MacRumors, which names no
  configuration. [checked]
- On 2026-11-02 Apple's CEO called memory pricing "unprecedented" and said the December
  quarter will be worse, and MacRumors reports Mac and iPad supply constrained in that quarter.
  [checked]
- TrendForce (2026-09-30) projects DRAM contract prices up 10 to 15% in the fourth quarter, a
  forecast rather than a measured change. [checked]
- The Mac Studio M5 Max (announced 2026-08-25) starts at $2,499 with 36 to 128 GB. No 64 GB price
  was found. [secondary]
- NVIDIA raised the DGX Spark Founders Edition from $3,999 to $4,699 on 2026-02-23, citing
  memory. RTX 5090 street prices are reported far above its $1,999 list. [primary / secondary]

## 3. Cloud prices and limits

- **Anthropic API.** Opus 5.5 lists at $4 and $20 per million input and output tokens, Sonnet
  5.5 at $2 and $10, and Haiku 5.5 at $0.10 and $0.50 for prompts up to 100,000 tokens.
  [checked] Opus 4.5 to 5 were $5 and $25, Opus 4 was $15 and $75, and US-only inference
  costs 1.1x. [primary]
- **Anthropic plans.** Pro is $20, Max $100 and Ultra $200, each with weekly caps whose token
  counts aren't published. [primary] On 2026-03-26 an Anthropic engineer announced that
  five-hour session limits on Free, Pro and Max would run out faster during peak hours (05:00
  to 11:00 PT), and that about 7% of users would newly hit them. [checked]
- **OpenAI.** Codex comes with every ChatGPT plan from Free to Pro ($100 to $500), with
  allowances given as message ranges per five hours. [primary]
- **GitHub Copilot** moved every plan to token-priced "GitHub AI Credits" from 2026-06-01, at
  the listed API rates for each model (announced 2026-04-27). [checked]
- **Cursor** now bills third-party models (Claude, GPT, Gemini) at API list prices outside its
  flat pools. [primary]
- **Per-token prices fall while spend per developer rises.** Epoch's 2025 analysis found the
  price of a fixed capability falling by a median 50x a year. Gartner, quoted 2026-06-24,
  described per-developer AI coding bills rising from $20 to $100 a month to $2,000 to $5,000
  under consumption pricing. Both can be true, since agents use far more tokens per task.
  [secondary]
- **Whether prices last.** Anthropic filed an S-1 on 2026-10-26 and an amendment on 2026-11-02
  (EDGAR). CNBC reports revenue of $16.29B for the first half of 2026, up from $1.16B a year
  earlier, $4.59B for 2025, and a first-half loss of $80.7B. [checked, via CNBC; the S-1 itself
  wasn't read] One analyst derives an operating profit for Q2 and an 85% gross margin from
  the same filing, and others dispute the margin, so it settles nothing yet. [secondary]
  OpenAI is reported far from profit. [secondary] No lab discloses per-subscriber margins, so
  the size of any subsidy is unknown, and the observable pattern in 2026 is that flat plans
  were metered or tightened.

## 4. Control, privacy and law

- **Silent regressions.** Anthropic's 2026-04-23 postmortem traced weaker Claude Code results
  between 4 March and 20 April to three changes: the default reasoning effort lowered from
  high to medium, a bug that cleared older reasoning every turn instead of once, and a system
  prompt to cut verbosity. The API and inference layer were unaffected. [checked]
- **Retirements.** Anthropic promises at least 60 days' notice for publicly released models,
  retired Opus 4 and Sonnet 4 on 2026-06-15 (notice 2026-04-14), and lists Opus 5.5 as "not
  sooner than" 2027-09-22. Bedrock and Vertex set their own schedules. A local model file can
  be kept and rerun indefinitely. [checked]
- **Access rules.** Anthropic restricted subscription tokens to its own client in early 2026,
  so a flat plan may only work inside the vendor's harness. [secondary]
- **Consumer data.** Since the 2025-08-28 terms, Free, Pro and Max users (Claude Code included)
  who allow training have their data kept five years, against 30 days otherwise, and API and
  commercial accounts are excluded. [checked]
- **EU AI Act.** The Digital Omnibus (Regulation (EU) 2026/1744, in force 2026-07-27) moved
  high-risk deadlines to late 2027 and 2028, while the general-purpose model duties that began
  2025-08-02 stand. Those fall on model providers, so a team that fine-tunes or hosts its own
  model takes on duties a vendor's customer doesn't. [secondary]
- **Open weights and China.** Chinese models were the plurality of Hugging Face downloads over
  the year to February 2026, at 41% ("State of Open Source: Spring 2026", 2026-03-17). The US
  share is about 36.5% only by reading the post's chart. [checked] The US has no ban, but
  federal procurement reportedly moved against some Chinese models in October 2026, and China
  drafted export controls on model weights. A developer can run Qwen freely today, and anyone
  selling to the US government may not be able to later. [secondary]

## 5. Energy

- Google's 2025 figure for a median Gemini text prompt is 0.24 Wh, which doesn't scale to an
  agent task of many thousands of tokens. No 2026 update was found. [primary]
- Local-against-datacenter energy per token is unsettled. One 2026 preprint claims 30 to 40x
  better per token on an M4 Pro for 3B to 9B models, and another finds batching erases the
  advantage. Nobody publishes energy per solved task, which Mahoraga's attempt logs could
  compute, since a 36% local rate means about three attempts per task the cloud solves once.
  [primary preprint, weak method]
- The IEA expects datacenter electricity to roughly double to about 950 TWh by 2030. [secondary]

## 6. Developers and society

- **Stack Overflow's 2026 survey** (press release 2026-10-06, 30,000+ responses) says 66% use
  coding assistants or agents, and only 6.6% would trust AI with important decisions. The
  results page adds that 48% trust it when they can easily validate the answers. That
  conditional trust is what grading with the repository's own tests serves, and the survey
  publishes nothing on local models. [checked]
- **Open-source maintainers.** A Linux Foundation survey (released 2026-10-20) estimates about
  25% of incoming pull requests are low-quality AI submissions, 31% at large projects. A tool
  that makes patches cheap raises review costs on either side, and AI-written commits may
  start to appear in mined tasks. [checked]
- **Junior developers.** Stanford's 2026-08-12 update puts employment of 22 to 25 year olds in
  highly AI-exposed jobs about 19% below where it would be had it kept pace with the same age
  group in less-exposed jobs, up from 15% a year earlier. The authors call it descriptive, not
  causal. [checked]
- **Access.** The cheapest 16 GB Mac now costs about $1,299 after the June increases, and
  Anthropic began rupee pricing in India in July 2026. A student weighing a $100 to $200 plan
  against a 24 to 64 GB machine faces a rising price on both sides. [secondary]

## Open questions for the study

1. Should each edition carry the break-even at 1, 3 and 10 minutes of triage, since no study
   settles the three?
2. Does the cloud arm need a trajectory audit like RuBench's (which model answered, whether it
   looked for the answer) before the 47 of 47 is cited again?
3. Is a same-agent comparison (Claude Code on qwen3.5 through Ollama) the next laptop night,
   ahead of new models?
4. Should the verdict take an optional hardware cost, amortized over the machine's life, for a
   reader who would buy a machine to go local?
5. Should the tasks export to Harbor, so other tools can run Mahoraga's mined tasks and the
   other way round?
6. Energy per solved task is unmeasured anywhere, and Mahoraga logs what it needs. Is it worth a
   log entry?
