# A1: Semantic-Augmented Routing

**Status:** Design Spec (pre-implementation)
**Author:** Design review with Opus 4.6
**Scope:** Replace keyword-overlap episodic retrieval with semantic embedding retrieval; preserve the 9-dim handcrafted LinUCB context vector unchanged.
**Priority:** Foundational — A2 (confidence-aware escalation), A3 (learned quality scoring), and A4 (brain integration) all become stronger after this lands.

---

## 1. Problem Statement

### 1.1 The Invisible Ceiling

The 9-dimensional handcrafted context vector in `routing/context.py` collapses semantic meaning into keyword counts. Two tasks that share surface-level tokens but diverge in actual intent produce nearly identical context vectors:

```
Task A: "Fix the database race condition in the connection pool"
Task B: "Fix the typo in the README header"

Both produce:
  has_error_keywords  = 0.0   (no "error"/"exception"/"traceback")
  has_creation_keywords = 0.0
  code_keyword_density ≈ 0.15  (both mention code-adjacent words)
  complexity_tier     = 0.33   (both parse as "simple" — short, imperative)
  word_count_norm     ≈ 0.08   (both ~8-10 words)
```

The bandit sees these as the same task. Episodic memory, which retrieves the k=10 nearest neighbours in the same 9-dim space, also sees them as the same task. The reward bias it injects is therefore drawn from irrelevant history.

This isn't a hypothetical. In the Phase 2 benchmark, the `code` bucket contains prompts ranging from "write a Python hello world" to "implement a concurrent LRU cache with TTL eviction." The 9-dim vector cannot separate these. The bandit must explore its way to the right agent every time, learning nothing transferable between semantically similar but keyword-different tasks.

### 1.2 What We Lose Today

Three concrete failure modes, all traceable to the shallow context vector:

**Failure mode 1 — Episodic memory retrieves irrelevant history.** A task about "summarise the OAuth2 spec" retrieves episodes from "summarise the meeting notes" because both have `has_research_keywords=1.0` and similar `word_count_norm`. The reward bias from the meeting-notes episodes is noise for the OAuth2 task. At α=0.20, that noise directly shifts the bandit's arm selection.

**Failure mode 2 — The bandit cannot generalise across semantically related tasks.** A task about "explain how B-trees handle page splits" and a task about "explain how LSM-trees compact SSTables" are both research-bucket, both questions, both ~15 words. The bandit treats them identically. But the user's history shows that Gemini CLI consistently nails data-structure explanations while Goose is better at high-level architecture overviews. That signal exists in the episode log but is invisible to the retrieval system.

**Failure mode 3 — The keyword classifier and the context vector disagree.** The keyword gate correctly puts "refactor the auth middleware to use dependency injection" in the `refactor` bucket. But the context vector's `has_creation_keywords` fires on "use" → false positive. `code_keyword_density` is moderate. The vector looks like a generic code task, not a refactoring task. The bandit's per-bucket routing doesn't get the within-bucket discrimination it needs.

### 1.3 What We Want

After A1 lands:

- Episodic memory retrieval is **semantic**. "Fix the database race condition" retrieves episodes about concurrency bugs, connection pools, and database locking — not episodes about typo fixes that happen to share the word "fix."
- The bandit receives a **semantically-informed reward bias** through the existing α=0.20 pathway. No changes to LinUCB's dimensionality, covariance matrices, or update rule.
- The handcrafted 9-dim features are **preserved**. They capture structural signals (file count, complexity tier, question-ness) that embeddings don't naturally encode. The two representations are complementary.
- Encoding latency is **≤5ms cold, <1ms cached**, which is invisible relative to the current ~10ms routing decision and 5–120s agent execution.

---

## 2. Architecture: Two-Tower Design

### 2.1 Why Not Replace the 9-dim Vector

The obvious move is: embed the task, PCA-project to d≈25, feed it to LinUCB. This is wrong for three reasons:

**Covariance explosion.** LinUCB maintains a d×d matrix **A** per arm. At d=9, that's 81 floats per agent — trivial. At d=384 (raw embedding), it's 147,456 floats × N agents, and the matrix inversion in the UCB calculation becomes the bottleneck. Even at d=25 (PCA-projected), it's 625 floats per agent and the exploration coefficient α needs retuning because the geometry of the confidence ellipsoid changes. This is solvable but it's a different project.

**Handcrafted features are good.** `file_count`, `complexity_tier`, and `is_question` encode structural properties of the task that a sentence embedding doesn't naturally capture. A 384-dim embedding of "refactor these 3 files" doesn't reliably encode "3 files" as a retrievable feature — it encodes the semantic meaning of refactoring. Both signals matter. Concatenation loses the clean interpretability of the handcrafted features; the bandit's learned θ weights become uninterpretable.

**The existing pathway is free.** Episodic memory already injects a reward bias into the bandit via α=0.20. Upgrading the retrieval quality of that bias is a surgical change. The bandit doesn't need to know that memory got smarter — it just receives better-quality reward priors.

### 2.2 The Two-Tower Split

```
              Task description (raw text)
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
   Handcrafted      Semantic     (both cached
    9-dim vec       384-dim vec   after first
                                  computation)
          │            │
          ▼            ▼
   LinUCB context   HNSW index key
   (bandit input)   (episodic memory)
                       │
                       ▼
                  k=10 nearest
                  neighbour rewards
                       │
                       ▼
                  Reward bias (α=0.20)
                       │
                       ▼
                  Injected into
                  bandit UCB score
                  (existing pathway,
                   no changes needed)
```

The semantic embedding is **never seen by LinUCB**. It flows into the bandit exclusively through episodic memory's reward-shaping bias. This means:

- LinUCB's A matrices stay 9×9. No retuning of α.
- The dLinUCB discount factor (γ=0.98) is unaffected.
- The reward learner's OLS weights remain per-bucket, operating on the same reward signal.
- Warm-start logic in `warm_start.py` is unaffected — it injects pseudo-observations into A/b, which are still 9-dim.

### 2.3 Data Flow — Concrete

When a task arrives:

1. `context.py` computes the 9-dim handcrafted vector (unchanged).
2. `embeddings.py` computes the 384-dim semantic vector (new). Checks LRU cache → SQLite cache → cold encode.
3. `bandit_router.py` passes both vectors forward.
4. `episodic_memory.py` queries HNSW with the 384-dim vector. Returns k=10 nearest episodes with their rewards and agent IDs.
5. The bandit computes UCB scores per arm using the 9-dim vector, then applies the memory bias (α=0.20) from step 4.
6. Agent is selected. Task executes. Reward is computed.
7. Episode is stored: `{384-dim embedding, 9-dim context, agent_id, reward, bucket, timestamp, task_hash}`.
8. LinUCB updates A/b with the 9-dim vector and the observed reward (unchanged).

---

## 3. Embedding Model Selection

### 3.1 Primary Choice: `all-MiniLM-L6-v2`

| Property | Value |
|----------|-------|
| Dimensions | 384 |
| Model size | ~80 MB on disk, ~90 MB in RAM |
| Encode latency | ~5ms per sentence on M-series CPU (no GPU) |
| Max sequence length | 256 tokens (sufficient — task descriptions are typically 10–50 tokens) |
| Library | `sentence-transformers` (PyTorch backend) |
| License | Apache 2.0 |
| Quality | MTEB sentence similarity: 0.789 avg. Battle-tested, well-understood failure modes. |

### 3.2 Why This Model

The selection criteria, in priority order:

1. **Local-only.** No API calls. Mahoraga's privacy posture is that task descriptions never leave the machine. This eliminates Ollama-hosted embedding models (network hop to Ollama server adds latency and a failure mode) and any cloud embedding API.
2. **Small and fast.** The encode happens on every task arrival. 5ms is noise. 50ms is not.
3. **Good enough for routing.** We're not doing retrieval-augmented generation or document search. We need to discriminate "database race condition" from "README typo" — a low bar for any reasonable sentence embedding model. MiniLM clears it easily.
4. **Stable.** sentence-transformers is maintained, widely deployed, and unlikely to break. The model weights are frozen; we're not fine-tuning.

### 3.3 Alternatives Considered

| Model | Dims | Size | Why Not (for now) |
|-------|------|------|-------------------|
| `BGE-small-en-v1.5` | 384 | ~130 MB | Slightly better quality on MTEB. Worth testing if MiniLM underperforms in the adversarial benchmark. Same API — swap is a one-line change. |
| `nomic-embed-text` via Ollama | 768 | ~274 MB | Doubles HNSW RAM. Adds Ollama server as a dependency for embedding (currently only used for inference). 768-dim HNSW at 10K episodes ≈ 100 MB vs 50 MB. The quality uplift doesn't justify the coupling. |
| Ollama model hidden states | varies | — | Ollama API doesn't expose intermediate hidden states. Would require forking Ollama or using a custom GGUF loader. Not worth the complexity. |
| `gte-small` | 384 | ~67 MB | Comparable quality to MiniLM, slightly smaller. Viable alternative. Less community adoption means less battle-testing. |
| OpenAI `text-embedding-3-small` | 1536 | cloud | Violates local-only constraint. Non-starter. |

### 3.4 Model Loading Strategy

The embedding model is **not** loaded at import time. It's loaded lazily on first call to `encode()`, because:

- Many code paths (e.g., `orch benchmark simulate` with synthetic data) don't need embeddings.
- The ~90 MB RAM footprint matters on 16 GB hardware where Ollama is also running.
- Startup time for `orch serve` should not include a PyTorch model load.

The model is loaded once into a module-level singleton. Subsequent calls reuse it. There is no unload mechanism — the model stays in memory for the process lifetime. On 16 GB hardware with Ollama running Qwen3 4B (~2.5 GB), the embedding model's 90 MB is acceptable.

**If `sentence-transformers` is not installed**, the embedding service degrades gracefully: `encode()` returns `None`, episodic memory falls back to the 9-dim handcrafted vector for retrieval (current behaviour), and a warning is logged once. The rest of the system runs normally. This preserves the "episodic memory degrades gracefully if hnswlib is not installed" contract from the README, extending it to the embedding model.

---

## 4. Embedding Service (`routing/embeddings.py`)

### 4.1 Public Interface

```python
class EmbeddingService:
    """Semantic embedding service for task descriptions.
    
    Provides a 384-dim dense vector for any text input.
    Two-layer cache: LRU in-memory (hot) + SQLite on disk (warm).
    Graceful degradation if sentence-transformers is unavailable.
    """

    def encode(self, text: str) -> np.ndarray | None:
        """Encode text to a 384-dim unit vector.
        
        Returns None if the embedding model is unavailable.
        The returned vector is L2-normalised (unit length).
        
        Cache lookup order:
          1. LRU in-memory cache (hit: <0.01ms)
          2. SQLite disk cache (hit: <1ms)
          3. Model inference (cold: ~5ms)
        
        Thread-safe. The model and caches are protected by a lock.
        """

    def encode_batch(self, texts: list[str]) -> list[np.ndarray | None]:
        """Batch encode. Used by the backfill script.
        
        Sentences are batched to the model in chunks of 64.
        Cache is checked per-text; only cache misses go to the model.
        """

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Cosine similarity between two unit vectors.
        
        Since vectors are L2-normalised, this is just np.dot(a, b).
        """

    @property
    def available(self) -> bool:
        """True if the embedding model loaded successfully."""

    @property
    def model_id(self) -> str:
        """Returns the model identifier string.
        
        Used for cache keying and HNSW index versioning.
        Currently: 'all-MiniLM-L6-v2'
        """

    @property
    def dim(self) -> int:
        """Embedding dimensionality. Currently 384."""
```

### 4.2 Normalisation

All embeddings are **L2-normalised before storage**. This is critical because:

- HNSW with `space='cosine'` in hnswlib internally normalises and uses inner product. If we pre-normalise, we can use `space='ip'` (inner product) which is faster and avoids the redundant normalisation on every query.
- Cosine similarity between two unit vectors is just `np.dot(a, b)` — no division needed.
- The existing quality evaluator uses `cosine_similarity` between prompt and output embeddings via nomic-embed-text. If we ever unify the embedding model, pre-normalisation keeps the interface consistent.

**Implementation detail:** `sentence-transformers`' `encode()` returns un-normalised vectors by default. Pass `normalize_embeddings=True` or manually normalise: `v / np.linalg.norm(v)`. Verify with an assertion in the test suite: `assert abs(np.linalg.norm(v) - 1.0) < 1e-6`.

### 4.3 Caching Architecture

Two layers, both keyed by `sha256(text.strip().lower())`:

**Layer 1 — LRU in-memory cache.**
- `functools.lru_cache` or a simple dict with manual eviction.
- Size: 1,000 entries. At 384 floats × 4 bytes = 1.5 KB per entry → 1.5 MB total. Negligible.
- Why lower-case + strip for the key: "Fix the bug" and "fix the bug" and " Fix the bug " should hit the same cache entry. The embedding model is case-sensitive, but for routing purposes the semantic difference between cased variants is noise.
- **Caveat:** The actual embedding is computed on the *original* text (preserving case), not the normalised key. Only the cache lookup uses the normalised key. This means "Fix" and "fix" produce slightly different embeddings but share a cache slot. The error is negligible for routing — MiniLM's cosine similarity between cased variants of the same sentence is typically >0.98.

**Layer 2 — SQLite disk cache (`~/.mahoraga-v2/embedding_cache.sqlite`).**
- Schema:

```sql
CREATE TABLE IF NOT EXISTS embeddings (
    text_hash   TEXT PRIMARY KEY,   -- sha256 hex
    model_id    TEXT NOT NULL,      -- 'all-MiniLM-L6-v2'
    embedding   BLOB NOT NULL,      -- np.float32 array, 384 × 4 = 1536 bytes
    created_at  TEXT NOT NULL        -- ISO 8601
);
CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model_id);
```

- On cache hit: deserialise with `np.frombuffer(blob, dtype=np.float32)`. Verify length = 384. Store in LRU cache for next hit.
- On cache miss: encode, normalise, store in both LRU and SQLite.
- **Model-scoped:** The `model_id` column ensures that if the embedding model changes, old cache entries are ignored (cache miss). A future `orch cache clear` command can delete entries by model_id.
- **WAL mode:** The SQLite connection should use `PRAGMA journal_mode=WAL` for concurrent read/write safety. The embedding service may be called from the main routing thread and the backfill script simultaneously (unlikely but possible if someone runs `orch memory backfill` while the server is up).

**Cache invalidation:** There is none, deliberately. Task descriptions are immutable — once a task is logged, its text doesn't change. The only invalidation event is a model swap, which is handled by the model_id column.

### 4.4 Error Handling

| Failure | Behaviour |
|---------|-----------|
| `sentence-transformers` not installed | `available` returns False. `encode()` returns None. Warning logged once at first call. System runs with 9-dim fallback. |
| Model download fails (first run, no internet) | Same as above. The model auto-downloads on first `SentenceTransformer()` init. If this fails, the service is unavailable. |
| SQLite cache file corrupted | Catch `sqlite3.DatabaseError`, log, delete the file, create fresh. Lose cache, not correctness. |
| Encoding produces NaN/inf | Check `np.isfinite(v).all()` after encoding. If false, log the input text (truncated) and return None for that input. Don't store in cache. |
| Input text is empty string | Return a zero vector? No — return None. An empty task description is a bug upstream; don't paper over it. |
| Input text exceeds 256 tokens | MiniLM truncates silently. This is fine — task descriptions that exceed 256 tokens are rare, and the first 256 tokens capture the intent. Log a debug-level message if `len(text.split()) > 200` (rough proxy). |

### 4.5 Thread Safety

The embedding service is a singleton. It may be called from:

- The main routing thread (task arrival → encode → memory query → bandit select).
- The backfill script (batch encode from CLI).
- Potentially from a background thread if async routing is added later.

The model itself is thread-safe for inference (PyTorch in eval mode, no gradient computation). The LRU cache needs a lock if using a plain dict; `functools.lru_cache` is thread-safe in CPython due to the GIL but not guaranteed in other implementations. Use a `threading.Lock` around cache read/write for safety.

The SQLite connection is **per-thread** (sqlite3 module's default). Don't share a connection across threads. Use a connection factory or `check_same_thread=False` with manual locking.

---

## 5. Episodic Memory Upgrade (`routing/episodic_memory.py`)

### 5.1 Current State

Today, episodic memory:

- Stores episodes as `{9-dim context vector, agent_id, reward, bucket, timestamp}`.
- HNSW index is built over the 9-dim vectors with `dim=9, space='l2'`.
- On query: find k=10 nearest neighbours in 9-dim space, aggregate their rewards per agent, inject as bias into bandit UCB scores at α=0.20.
- FIFO cap at 10,000 episodes.
- Persistence: `~/.mahoraga-v2/episodic_memory.bin` (hnswlib serialised index) + metadata in SQLite or sidecar JSON.

### 5.2 What Changes

| Aspect | Before | After |
|--------|--------|-------|
| HNSW index dimension | 9 | 384 |
| HNSW distance space | `l2` | `ip` (inner product on L2-normalised vectors = cosine similarity) |
| Episode storage | 9-dim vector + metadata | 384-dim embedding + 9-dim vector + metadata |
| Retrieval key | 9-dim handcrafted vector | 384-dim semantic embedding |
| Reward shaping logic | Unchanged | Unchanged |
| α parameter | 0.20 | 0.20 (unchanged; retune in benchmark if needed) |
| FIFO cap | 10,000 | 10,000 (unchanged) |
| Index file | `episodic_memory.bin` | `episodic_memory_v2.bin` (new filename to avoid silent corruption) |

### 5.3 Index Versioning

The HNSW index file must be versioned because a dim=9 index and a dim=384 index are incompatible binary formats. hnswlib will segfault or return garbage if you load a dim=9 index and query with a dim=384 vector.

**Metadata sidecar file:** `~/.mahoraga-v2/episodic_memory_meta.json`

```json
{
    "version": 2,
    "dim": 384,
    "space": "ip",
    "model_id": "all-MiniLM-L6-v2",
    "max_elements": 10000,
    "ef_construction": 200,
    "M": 16,
    "episode_count": 4217,
    "created_at": "2026-05-06T12:00:00Z",
    "last_updated_at": "2026-05-06T15:30:00Z"
}
```

**On startup:**
1. Check if `episodic_memory_meta.json` exists.
2. If it exists, validate `version`, `dim`, `model_id`. If any mismatch → rebuild (see §5.5).
3. If it doesn't exist but `episodic_memory.bin` exists → this is a v1 (dim=9) index. Log a message: "Legacy episodic memory index detected. Run `orch memory backfill` to upgrade to semantic retrieval, or the system will use 9-dim fallback."
4. If neither exists → fresh start. Create empty index when first episode is stored.

### 5.4 Dual Storage

Each episode stores **both** vectors:

```python
@dataclass
class Episode:
    embedding: np.ndarray       # 384-dim, L2-normalised. The HNSW key.
    context_vector: np.ndarray  # 9-dim handcrafted. Preserved for:
                                #   - Fallback retrieval if embeddings unavailable
                                #   - Future phase-2 PCA fusion experiments
                                #   - Debugging / interpretability
    agent_id: str               # e.g., "ollama:qwen3-4b"
    reward: float               # Composite reward from the reward function
    bucket: str                 # Capability bucket at time of routing
    task_hash: str              # sha256 of task description (for dedup / lookup)
    timestamp: float            # time.time() at episode creation
```

Storage overhead per episode: 384×4 + 9×4 + metadata ≈ 1,600 bytes. At 10,000 episodes: ~16 MB. The HNSW index itself at dim=384, M=16, ef_construction=200 is ~50 MB for 10K elements. Total: ~66 MB. Acceptable on 16 GB hardware.

### 5.5 Rebuild / Migration Logic

The HNSW index must be rebuilt when:

- `model_id` in metadata doesn't match `EmbeddingService.model_id` (model was swapped).
- `dim` in metadata doesn't match `EmbeddingService.dim`.
- `version` is missing or < 2 (legacy v1 index).
- The index file is corrupted (hnswlib throws on load).

**Rebuild procedure:**

1. Load all episodes from the metadata store (SQLite sidecar or JSON — whichever the current implementation uses).
2. For episodes that have a `task_hash`: look up the original task description from `~/.mahoraga-v2/routing_decisions.db` (the decision log). Re-encode via `EmbeddingService.encode_batch()`.
3. For episodes without a recoverable task description: discard. They can't be embedded. Log the count of discarded episodes.
4. Build a new HNSW index from the re-encoded vectors.
5. Write new index file + metadata sidecar.
6. Delete the old index file.

This is effectively what the backfill script does (§8), but triggered automatically on startup with a mismatch. The auto-rebuild should be **opt-in** (behind a flag or a y/n prompt in the CLI) because it takes ~50 seconds for 10K episodes and blocks the server startup. Default behaviour on mismatch: log a warning, fall back to 9-dim retrieval, suggest running `orch memory backfill`.

### 5.6 Fallback Behaviour

If `EmbeddingService.available` is False (sentence-transformers not installed, model failed to load):

- New episodes are stored with `embedding=None` and the 9-dim context vector only.
- Retrieval falls back to a dim=9 HNSW index (the current behaviour, effectively).
- This maintains the README's contract: "Episodic memory degrades gracefully."
- When the embedding service becomes available later (user installs sentence-transformers), the backfill script can retroactively embed all episodes that have `embedding=None`.

### 5.7 HNSW Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `dim` | 384 | MiniLM output dimensionality |
| `space` | `ip` | Inner product on L2-normalised vectors = cosine similarity. Faster than `cosine` space (avoids redundant normalisation). |
| `M` | 16 | Default for hnswlib. Controls graph connectivity. Higher = better recall, more RAM. 16 is standard for <100K elements. |
| `ef_construction` | 200 | Index build quality. Higher = slower build, better recall. 200 is standard. Only matters during backfill; incremental adds are fast regardless. |
| `ef` (query-time) | 50 | Search quality. Higher = slower query, better recall. 50 is conservative for k=10. Can tune down to 30 if latency is a concern, but at 384-dim with 10K elements, query time is <1ms regardless. |
| `max_elements` | 10,000 | FIFO cap, unchanged. hnswlib requires this at index creation. Can be set to 12,000 for headroom (avoids rebuild when approaching cap). |

### 5.8 FIFO Eviction with HNSW

hnswlib does not natively support element deletion. The current implementation presumably handles this via one of:

- **Rebuild on overflow:** When episode count hits max_elements, rebuild the index from the most recent 10,000 episodes. Expensive but infrequent (once per 10K tasks).
- **Mark-and-skip:** Use hnswlib's `mark_deleted()` API (available since v0.6.0). Mark the oldest episode as deleted when inserting a new one. Periodically rebuild to reclaim space.
- **Overwrite:** hnswlib allows replacing an element at a given index. Maintain a ring buffer of indices; overwrite the oldest.

**Recommendation:** Use `mark_deleted()` for online operation. Schedule a periodic rebuild (every 1,000 new episodes, or on startup if >20% of elements are marked deleted). The rebuild is fast — 10K elements at dim=384 takes ~2 seconds.

**Critical:** When evicting, also remove the episode from the metadata store (SQLite sidecar). The HNSW index and the metadata store must stay in sync. If they diverge (e.g., crash during eviction), the rebuild logic (§5.5) is the recovery path.

---

## 6. Changes to Existing Files

### 6.1 `routing/context.py` — NO CHANGES

The `TaskContext` dataclass and the `build_context_vector()` function are untouched. The 9-dim vector continues to be the sole input to LinUCB. This is the whole point of the two-tower design.

### 6.2 `routing/bandit_router.py` — Minimal Changes

The bandit router needs to:

1. Accept an optional `embedding: np.ndarray | None` parameter alongside the existing `context_vector`.
2. Pass the embedding to `episodic_memory.query()` instead of (or in addition to) the context vector.
3. If embedding is None, fall back to querying memory with the context vector (current behaviour).

**That's it.** The UCB computation, arm selection, A/b update, dLinUCB discount — all unchanged.

```python
# Pseudocode for the change in select_agent():

# Before:
memory_bias = self.episodic_memory.query(context_vector, k=10)

# After:
if embedding is not None:
    memory_bias = self.episodic_memory.query_semantic(embedding, k=10)
else:
    memory_bias = self.episodic_memory.query(context_vector, k=10)

# Rest of UCB computation unchanged.
```

### 6.3 `routing/episodic_memory.py` — Significant Changes

See §5 for the full spec. Summary of API changes:

```python
# New method:
def query_semantic(self, embedding: np.ndarray, k: int = 10) -> dict[str, float]:
    """Query episodic memory using semantic embedding.
    
    Returns a dict of {agent_id: reward_bias} for the k nearest episodes.
    Same return format as the existing query() method.
    """

# Modified method:
def add_episode(self, episode: Episode) -> None:
    """Store a new episode. Now accepts both embedding and context_vector."""

# Existing method preserved:
def query(self, context_vector: np.ndarray, k: int = 10) -> dict[str, float]:
    """Original 9-dim query. Preserved as fallback."""
```

### 6.4 `routing/reward_learner.py` — NO CHANGES

The reward learner fits OLS weights on per-bucket reward signals. It doesn't interact with the context vector or embeddings. Unchanged.

### 6.5 `routing/warm_start.py` — NO CHANGES

Warm start injects pseudo-observations into LinUCB's A/b matrices, which are 9-dim. The embedding doesn't participate in warm start. Unchanged.

### 6.6 Quality evaluator — POTENTIAL FUTURE ALIGNMENT

The quality evaluator currently uses `nomic-embed-text` (via Ollama) for prompt-output similarity. This is a different embedding model than what A1 introduces. There's a minor inconsistency: two different embedding models in the same system. For now, this is fine — they serve different purposes (routing retrieval vs. quality scoring) and operate independently. A future cleanup could unify on MiniLM for both, removing the Ollama embedding dependency.

---

## 7. Caching and Performance Budget

### 7.1 Latency Breakdown — Current (Pre-A1)

| Step | Time |
|------|------|
| Task classification (keyword gate) | <1ms |
| Context vector construction (9-dim) | <1ms |
| Episodic memory query (HNSW, dim=9, 10K elements) | <1ms |
| LinUCB UCB computation (9×9 inversion × N agents) | <1ms |
| **Total routing decision** | **~2-5ms** |
| Agent execution (Ollama / cloud) | 5,000-120,000ms |

### 7.2 Latency Budget — Post-A1

| Step | Time | Notes |
|------|------|-------|
| Task classification | <1ms | Unchanged |
| Context vector construction (9-dim) | <1ms | Unchanged |
| **Semantic encoding** | **<1ms (cached) / ~5ms (cold)** | **New** |
| Episodic memory query (HNSW, dim=384, 10K elements) | <1ms | Marginally slower than dim=9 but still <1ms |
| LinUCB UCB computation | <1ms | Unchanged (still 9-dim) |
| **Total routing decision** | **~3-8ms** | |
| Agent execution | 5,000-120,000ms | Unchanged |

The routing overhead is 1,000× smaller than the cheapest agent execution. Even the cold-encode path (5ms) is invisible. After warm-up, cache hit rate should be >95% for a typical user's task distribution (many tasks are rephrased variants of recurring patterns).

### 7.3 Memory Budget

| Component | RAM | Notes |
|-----------|-----|-------|
| Embedding model (MiniLM) | ~90 MB | Loaded lazily. Stays resident. |
| LRU cache (1,000 entries) | ~1.5 MB | 384 floats × 4 bytes × 1,000 |
| HNSW index (dim=384, 10K elements) | ~50 MB | hnswlib internal structures |
| Episode metadata (10K entries) | ~16 MB | SQLite or in-memory |
| **Total A1 overhead** | **~160 MB** | |

On 16 GB hardware with Ollama running Qwen3 4B (~2.5 GB VRAM/unified), the system has ~13 GB of headroom. 160 MB is ~1.2% of that. Acceptable.

**If memory is tight** (e.g., running DeepSeek-R1 at 8 GB alongside Ollama), the embedding model is the first thing to shed. The graceful degradation path (§4.4) means everything else keeps working.

---

## 8. Backfill Script (`orch memory backfill`)

### 8.1 Purpose

Retroactively embed all historical task descriptions from `~/.mahoraga-v2/routing_decisions.db` (the decision log) and rebuild the HNSW index at dim=384. This converts existing episodic memory from keyword-similar retrieval to semantic retrieval without losing historical data.

### 8.2 CLI Interface

```bash
orch memory backfill                    # Backfill from decision log, rebuild HNSW
orch memory backfill --dry-run          # Count tasks, estimate time, don't write
orch memory backfill --model bge-small  # Override embedding model (for A/B testing)
orch memory backfill --force            # Rebuild even if index is already v2
orch memory inspect                     # Print index metadata, episode count, model info
orch memory clear                       # Delete index + cache. Fresh start.
```

### 8.3 Backfill Procedure

1. **Read decision log.** Query `routing_decisions.db` for all unique `(task_description, task_hash)` pairs with associated `agent_id`, `reward`, `bucket`, `timestamp`.
2. **Deduplicate.** If the same task_hash appears multiple times (same task routed multiple times — e.g., retries), keep all episodes. Each routing decision is a separate episode with potentially different agent/reward.
3. **Batch encode.** Feed unique task descriptions to `EmbeddingService.encode_batch()` in chunks of 64. The batch method checks the SQLite cache per-text, so re-runs are fast.
4. **Build HNSW index.** Create a new hnswlib index at dim=384, insert all embeddings.
5. **Write metadata.** Save episode data (embedding + context_vector + agent_id + reward + bucket + timestamp + task_hash) to the metadata store.
6. **Write sidecar.** Save `episodic_memory_meta.json` with version, dim, model_id, counts.
7. **Report.** Print summary: episodes backfilled, episodes skipped (no task description), cache hits vs cold encodes, wall-clock time.

### 8.4 Performance Estimate

- 10,000 tasks × 5ms per encode (cold) = 50 seconds worst case.
- With SQLite cache populated from a prior run: ~5 seconds (cache reads at <1ms each).
- HNSW index build for 10K elements at dim=384: ~2 seconds.
- Total: under 60 seconds for a full rebuild from scratch.

### 8.5 Edge Cases

| Case | Handling |
|------|----------|
| Decision log doesn't exist | Print "No routing history found. Nothing to backfill." Exit 0. |
| Decision log exists but has no task descriptions (only hashes) | Print count of unrecoverable episodes. Backfill what's possible. |
| Backfill interrupted (Ctrl-C) | SQLite cache has all embeddings computed so far. Re-run resumes from cache. HNSW index is not written until step 4 completes — no partial index. |
| Server is running during backfill | Both can write to the SQLite embedding cache (WAL mode). The HNSW index file should not be overwritten while the server is using it. The backfill script writes to a temp file (`episodic_memory_v2.bin.tmp`) and atomically renames on completion. The server should detect the new file on next query (or on a timer — check file mtime every 60 seconds). |
| Task description contains PII or secrets | All processing is local. No network calls. Embeddings are opaque 384-dim floats — you cannot reconstruct the original text from the embedding (not invertible). The SQLite cache stores the sha256 hash of the text, not the text itself. |

---

## 9. Evaluation Plan

### 9.1 Hypothesis

Semantic episodic retrieval improves routing quality most when tasks share keyword-level features but diverge in semantic meaning. The expected improvement is concentrated in within-bucket agent selection, not in bucket classification (which is done by the keyword gate, untouched by A1).

### 9.2 Standard Benchmark

Use the existing 42-prompt benchmark across 7 buckets. Three conditions:

| Condition | Episodic Memory | Retrieval |
|-----------|----------------|-----------|
| Baseline | 9-dim HNSW | Keyword-similar |
| A1 | 384-dim HNSW | Semantic |
| Ablation | Disabled | None (α=0.0) |

Each condition runs with the same seeds (`MAHORAGA_BANDIT_SEED`, `MAHORAGA_PROMPT_SEED`), same agents, same compatibility matrix for warm start. Metrics:

- **Cumulative reward** over the 42 tasks.
- **Regret vs. oracle** (oracle = always pick the best agent per task, from the compatibility matrix).
- **Per-bucket mean quality score.**
- **Agent-selection entropy** — lower entropy in later tasks means the bandit is converging faster.
- **Memory retrieval precision@10** — for each query, what fraction of the k=10 retrieved episodes are from the same bucket? From a semantically related task? (Requires manual labelling of the 42 prompts into semantic clusters.)

### 9.3 Adversarial Benchmark

The standard benchmark may not stress the difference because the 42 prompts were designed to be bucket-distinct. We need a prompt set where the 9-dim vector actively misleads.

**Design: 30 adversarial prompts, 6 clusters of 5.**

Each cluster shares surface-level keyword features but requires different agents:

**Cluster 1 — "Fix" tasks (all have `has_error_keywords ≈ 0`):**
1. "Fix the database race condition in the connection pool" → concurrency bug, needs deep code reasoning
2. "Fix the typo in the README header" → trivial edit, any agent works
3. "Fix the CI pipeline to cache node_modules between runs" → DevOps/config, not a code bug
4. "Fix the memory leak in the WebSocket handler" → performance debugging, needs profiling awareness
5. "Fix the CORS headers for the staging environment" → infrastructure config, not application code

**Cluster 2 — "Explain" tasks (all have `has_research_keywords = 1.0`, `is_question = 0.0`):**
1. "Explain how B-tree page splits work during concurrent inserts" → data structures, deep CS
2. "Explain our deployment pipeline to a new team member" → project-specific, needs context
3. "Explain the GDPR implications of storing user IP addresses" → legal/compliance, not technical
4. "Explain why the test suite takes 40 minutes to run" → performance analysis, debugging
5. "Explain the tradeoffs between gRPC and REST for our internal APIs" → architecture, opinionated

**Cluster 3 — "Create" tasks (all have `has_creation_keywords = 1.0`):**
1. "Create a Python CLI that converts CSV to Parquet" → code generation, well-defined
2. "Create a project plan for migrating to Kubernetes" → planning, not coding
3. "Create a security review checklist for the auth service" → security, needs domain knowledge
4. "Create unit tests for the payment processing module" → testing, needs code understanding
5. "Create a one-pager comparing our pricing to competitors" → research + writing, not code

**Cluster 4 — Short imperative tasks (all have `word_count_norm ≈ 0.05`, `complexity_tier = 0.33`):**
1. "Add logging to the order service" → code, moderate complexity
2. "Summarise the RFC 7519 JWT spec" → research, high complexity
3. "Refactor the user model to use dataclasses" → refactoring, moderate
4. "Write a haiku about distributed systems" → creative, trivial
5. "Benchmark the hash map implementation" → testing/perf, needs tooling

**Cluster 5 — Multi-file references (all have `file_count ≥ 2`):**
1. "Update `auth.py` and `middleware.py` to use the new token format" → code refactoring, related files
2. "Compare the implementations in `sort_v1.py` and `sort_v2.py`" → code review, analytical
3. "Move the config from `settings.yaml` to `config.py` and update `main.py`" → refactoring, mechanical
4. "Write tests for `api.py` based on the spec in `openapi.yaml`" → test generation, spec-driven
5. "The errors in `build.log` are caused by `Dockerfile` — fix both" → debugging, cross-file

**Cluster 6 — Research-coded but computationally distinct:**
1. "Survey the state of WebAssembly support in Python" → broad research
2. "Compare the time complexity of merge sort vs timsort on nearly-sorted data" → specific CS analysis
3. "Summarise the key changes in Python 3.12 release notes" → factual extraction
4. "What are the security implications of running Ollama without auth?" → security analysis
5. "Review the literature on contextual bandits for recommendation systems" → academic survey

**Expected result:** The 9-dim baseline treats each cluster as nearly identical (same keyword features). Semantic retrieval should discriminate within clusters. If the A1 condition shows higher per-cluster agent diversity and higher per-task reward than baseline, the hypothesis is confirmed.

### 9.4 Metrics to Report

For both standard and adversarial benchmarks:

```
1. Cumulative reward: sum(rewards) over all tasks
2. Regret: sum(oracle_reward - actual_reward) over all tasks
3. Per-bucket mean quality
4. Convergence speed: regret in first 50% of tasks vs last 50%
5. Agent-selection entropy (Shannon) per bucket
6. Retrieval precision@10: fraction of retrieved episodes from the same semantic cluster (adversarial only)
7. Retrieval diversity: number of unique agents in the k=10 retrieved episodes (higher = memory isn't over-specialised)
```

### 9.5 Success Criteria

| Metric | Threshold | Rationale |
|--------|-----------|-----------|
| Cumulative reward improvement (A1 vs baseline) | ≥ 3% on standard, ≥ 8% on adversarial | Small on standard (prompts are well-separated), large on adversarial (prompts are designed to confuse 9-dim) |
| Retrieval precision@10 improvement | ≥ 20 percentage points on adversarial | The whole point: semantic retrieval finds relevant history |
| Latency regression | ≤ 5ms additional per routing decision | Must not visibly slow down routing |
| Memory overhead | ≤ 200 MB | Must fit on 16 GB hardware alongside Ollama |

If the standard benchmark shows no improvement but the adversarial benchmark shows clear gains, A1 is still worth shipping — the adversarial set represents real-world task diversity better than the standard set.

If both show no improvement, the semantic embedding isn't adding signal that the bandit can use through the α=0.20 pathway. In that case, consider phase 2 (PCA fusion into LinUCB context) or revisit whether α=0.20 is too small for the semantic signal to influence selection.

---

## 10. Risks and Mitigations

### 10.1 Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| MiniLM embedding quality insufficient for short task descriptions (5-10 words) | Medium | Low | Short texts are MiniLM's sweet spot (sentence-level). If quality is poor, swap to BGE-small (one-line change). |
| HNSW index corruption on crash during write | Low | Medium | Write to temp file, atomic rename. On corruption, auto-rebuild from SQLite cache + decision log. |
| sentence-transformers dependency bloat (pulls PyTorch) | High | Low | PyTorch is likely already installed for Ollama-adjacent workflows. If not, the graceful degradation path avoids it entirely. Document the optional dependency clearly. |
| Embedding model produces identical vectors for semantically different short texts | Low | Medium | Test with the adversarial prompt set. If clusters 4 (short imperatives) show low inter-prompt cosine distance, the embeddings aren't discriminating. Mitigation: prepend bucket label to text before encoding ("code: Add logging to the order service") to give the model a hint. |
| LRU cache key collision (different texts with same sha256 after lowercasing) | Negligible | Low | SHA-256 collision probability is ~2^-128 for any reasonable corpus. Not a practical concern. |

### 10.2 Architectural Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Two-tower approach limits how much semantic signal reaches the bandit | Medium | Medium | The α=0.20 pathway is a deliberate bottleneck. If the benchmark shows semantic retrieval is excellent but routing doesn't improve, increase α. If α > 0.40 is needed, the two-tower design may be insufficient — escalate to phase 2 (PCA fusion). |
| Memory-shaping bias creates feedback loops (bandit reinforces past mistakes) | Low | High | Already mitigated by dLinUCB's discount factor (γ=0.98) which decays old observations. Semantic retrieval doesn't change this dynamic — it just changes which old observations are retrieved. Monitor agent-selection entropy: if it drops to near-zero early, the bias is too strong. |
| Backfill from decision log produces low-quality episodes (early routing decisions were bad) | Medium | Low | The reward is recorded with each episode. Low-reward episodes are naturally weighted less in the reward-shaping bias. The concern is that early bad episodes cluster near new tasks in semantic space and inject noise. Mitigation: the backfill script could optionally filter to episodes with reward > threshold. Default: include all, because even negative signal is signal. |

### 10.3 Operational Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Users forget to run `orch memory backfill` after upgrading | High | Low | System works without backfill — just starts fresh with semantic retrieval. Log a one-time suggestion on startup if legacy index is detected. |
| Multiple Mahoraga processes on same machine conflict on HNSW index file | Low | Medium | Use file locking (fcntl / msvcrt) on the index file during writes. Reads don't need locks (hnswlib is thread-safe for reads). |
| Model download on first run fails silently | Medium | Medium | Check `EmbeddingService.available` during `orch serve` startup. Print clear message: "Semantic embeddings unavailable — install sentence-transformers and ensure internet for first model download. Falling back to keyword-based retrieval." |

---

## 11. Dependency Changes

### 11.1 New Dependencies

```
# requirements.txt additions
sentence-transformers>=2.2.0    # Embedding model (pulls torch, transformers, numpy)
```

**This is a heavy dependency.** `sentence-transformers` pulls PyTorch (~2 GB download, ~500 MB on disk after install) and Hugging Face transformers. On first run, it downloads the MiniLM model weights (~80 MB).

### 11.2 Making It Optional

The embedding service should be an **optional** dependency to avoid breaking existing installs:

```
# requirements.txt — base install (unchanged)
hnswlib>=0.7.0
numpy>=1.24.0
# ... existing deps ...

# requirements-semantic.txt — optional, for A1
sentence-transformers>=2.2.0
```

Or use extras in `pyproject.toml`:

```toml
[project.optional-dependencies]
semantic = ["sentence-transformers>=2.2.0"]
```

Install: `pip install -e ".[semantic]"` or `pip install -r requirements-semantic.txt`.

The embedding service's graceful degradation (§4.4) ensures the system runs without it.

### 11.3 Existing Dependencies Affected

- `hnswlib` — already a dependency. No version change needed. The dim=384 index uses the same API.
- `numpy` — already a dependency. No version change needed.
- `sqlite3` — stdlib. No change.

---

## 12. File Manifest

New and modified files for A1:

```
routing/embeddings.py           NEW    Embedding service, caching, model management
routing/episodic_memory.py      MOD    Dual-store, semantic retrieval, index versioning
routing/bandit_router.py        MOD    Pass embedding to memory query
cli/memory.py                   NEW    `orch memory backfill|inspect|clear` commands
tests/test_embeddings.py        NEW    Unit tests for embedding service
tests/test_episodic_semantic.py NEW    Integration tests for semantic retrieval
tests/test_backfill.py          NEW    Tests for backfill script
benchmarks/adversarial_prompts.json  NEW    30 adversarial prompts (§9.3)
requirements-semantic.txt       NEW    Optional dependency file
```

Files explicitly **not** modified: `routing/context.py`, `routing/reward_learner.py`, `routing/warm_start.py`, `routing/escalation.py`.

---

## 13. Implementation Phases

### Phase 1: Embedding Service + Cache

**Scope:** `routing/embeddings.py`, `tests/test_embeddings.py`, `requirements-semantic.txt`.

**Deliverables:**
- `EmbeddingService` class with `encode()`, `encode_batch()`, `similarity()`, `available`, `model_id`, `dim`.
- LRU in-memory cache (1,000 entries).
- SQLite disk cache at `~/.mahoraga-v2/embedding_cache.sqlite`.
- Graceful degradation when sentence-transformers is not installed.
- Unit tests: cache hit/miss, normalisation check, batch encoding, model unavailable fallback, corrupt SQLite recovery, empty input handling, thread safety under concurrent access.

**Acceptance criteria:**
- `encode("Fix the database race condition")` returns a 384-dim unit vector in <10ms cold, <1ms cached.
- `encode("Fix the database race condition")` and `encode("Fix the typo in the README")` have cosine similarity < 0.80.
- `encode("Fix the database race condition")` and `encode("Debug the race condition in the DB connection pool")` have cosine similarity > 0.85.
- `available` returns False and `encode()` returns None when sentence-transformers is not importable.

### Phase 2: Episodic Memory Upgrade

**Scope:** `routing/episodic_memory.py`, `tests/test_episodic_semantic.py`.

**Deliverables:**
- `query_semantic()` method using dim=384 HNSW index.
- Dual storage (384-dim embedding + 9-dim context per episode).
- Index versioning via `episodic_memory_meta.json`.
- FIFO eviction with `mark_deleted()` + periodic rebuild.
- Fallback to 9-dim retrieval when embeddings unavailable.
- Integration tests: store N episodes, query semantically, verify retrieval order matches semantic similarity (not keyword similarity). Test with adversarial pairs from §9.3.

**Acceptance criteria:**
- Given 100 stored episodes, `query_semantic(embed("Fix the database race condition"))` retrieves database/concurrency-related episodes before typo-fix episodes, even if both have identical 9-dim vectors.
- Index versioning: loading a v1 (dim=9) index with v2 code logs a warning and falls back to 9-dim retrieval without crashing.

### Phase 3: Router Integration + Backfill

**Scope:** `routing/bandit_router.py` (minimal change), `cli/memory.py`, `tests/test_backfill.py`.

**Deliverables:**
- Router passes embedding to `query_semantic()` when available.
- `orch memory backfill` CLI command.
- `orch memory inspect` and `orch memory clear` commands.
- Backfill tests: mock decision log with known tasks, verify embeddings are computed and HNSW index is built correctly.

**Acceptance criteria:**
- End-to-end: task arrives → encoded → routed with semantic memory → episode stored with embedding → next similar task retrieves it semantically.
- Backfill script completes in <60s for 10K episodes.
- `orch memory inspect` prints: index version, dim, model, episode count, oldest/newest episode timestamps.

### Phase 4: Benchmark + Adversarial Evaluation

**Scope:** `benchmarks/adversarial_prompts.json`, benchmark runner modifications.

**Deliverables:**
- Adversarial prompt set (30 prompts, 6 clusters per §9.3).
- Benchmark runner support for `--memory-mode={semantic,keyword,none}`.
- Comparison report: cumulative reward, regret, per-bucket quality, convergence speed, retrieval precision@10.
- Written analysis of results.

**Acceptance criteria:**
- Benchmark runs to completion under all three conditions with deterministic seeds.
- Results are reproducible across runs (same seeds → same routing decisions → same metrics, modulo floating-point non-determinism).

### Phase 5 (Deferred): PCA Fusion into LinUCB

**This is explicitly deferred.** Only pursue if Phase 4 shows that the two-tower approach plateaus — i.e., semantic retrieval is accurate but the α=0.20 pathway doesn't transfer enough signal to meaningfully improve routing.

If pursued: PCA-project the 384-dim embedding to ~16 dims, concatenate with the 9-dim handcrafted vector → d≈25 LinUCB context. Requires retuning α (exploration coefficient), recomputing A/b matrices for all agents, and potentially adjusting the dLinUCB discount factor. This is a non-trivial change to the bandit's geometry and should be treated as a separate spec.

---

## 14. Open Questions

### 14.1 Resolved

**Q: Should we use `space='cosine'` or `space='ip'` for the HNSW index?**
A: `space='ip'` with pre-normalised vectors. Equivalent to cosine similarity but avoids redundant normalisation in hnswlib. Faster.

**Q: Should the backfill be automatic on startup or manual via CLI?**
A: Manual via `orch memory backfill`. Auto-rebuild on startup blocks the server for up to 60 seconds, which is unacceptable for `orch serve`. Log a suggestion on startup if a legacy index is detected.

**Q: Should we store the raw text in the episode metadata?**
A: No. Store only the `task_hash` (sha256). The raw text is recoverable from `routing_decisions.db` via the hash. This avoids duplicating potentially sensitive task descriptions in the episodic memory store.

### 14.2 Unresolved (Decide During Implementation)

**Q: Should the adversarial prompt set be committed to the repo or generated programmatically?**
Argument for committing: reproducibility, human-curated quality, version-controlled.
Argument for generating: can scale to more clusters, can parametrically vary difficulty.
Recommendation: commit the 30 hand-crafted prompts. Add a generator later if needed.

**Q: Should `orch memory backfill` re-encode episodes that already have embeddings (from a prior run)?**
Argument for yes: ensures consistency if the model changed.
Argument for no: wastes time on re-runs.
Recommendation: default to skip (check SQLite cache), `--force` flag to re-encode all.

**Q: Should the embedding be computed asynchronously (non-blocking) or synchronously in the routing path?**
Argument for async: doesn't block routing if encoding is slow.
Argument for sync: simpler, encoding is <5ms, async adds complexity for no practical gain.
Recommendation: synchronous. The 5ms overhead is negligible. Revisit if the embedding model changes to something slower.

**Q: What should α (memory bias weight) be after A1?**
Currently 0.20. Semantic retrieval produces higher-quality neighbours, which might justify increasing α. But increasing α also increases the risk of feedback loops.
Recommendation: keep at 0.20 for Phase 4 benchmarks. If the results show semantic retrieval is accurate but routing doesn't improve, sweep α ∈ {0.15, 0.20, 0.25, 0.30} in a follow-up benchmark.

---

## 15. What A1 Unlocks

This section is forward-looking. None of these should be built as part of A1. But they become feasible or easier after A1 lands:

**A2 — Confidence-aware escalation.** LinUCB's posterior variance (`x' A⁻¹ x`) is already a measure of uncertainty. But it's only meaningful when the context vector is meaningful. With semantic memory providing better priors, the bandit's uncertainty estimate is calibrated against genuinely similar past tasks. "I'm uncertain about this task" now means "I haven't seen a task like this before," not "I haven't seen a task with these keyword counts before." This enables principled escalation: high variance → run two agents, or escalate to Claude, or verify aggressively.

**A3 — Learned quality scoring.** The same MiniLM embedding can be used as a feature in a quality prediction model. Instead of heuristic quality scoring (novelty ratio, structural checks, length fit), train a small regression model: `quality = f(task_embedding, output_embedding, structural_features)`. The training data comes from implicit signals (retry = bad, accept = good). This requires A1's embedding infrastructure to be in place.

**A4 — Brain/journal integration.** If the brain/journal stores past project decisions and their outcomes, those entries can be embedded into the same 384-dim space. The episodic memory then becomes a unified retrieval system: "given this task, what similar tasks have I routed before AND what project-level decisions are relevant?" The brain entries bias routing toward agents that historically performed well on tasks in similar project contexts.

**Unified embedding model.** The quality evaluator currently uses nomic-embed-text via Ollama for prompt-output similarity. After A1, both the routing system and the quality evaluator could use MiniLM, removing the Ollama embedding dependency. One model, one cache, one less thing to run.

---

## Appendix A: Embedding Space Sanity Checks

Before committing to MiniLM, verify these properties with a quick script (not part of the main implementation — just a validation step):

```python
# Expected cosine similarities (approximate, for MiniLM-L6-v2):

# Same-meaning, different words → high similarity
sim("Fix the database race condition", "Debug the concurrency bug in the DB pool")
# Expected: > 0.80

# Same keywords, different meaning → moderate similarity
sim("Fix the database race condition", "Fix the typo in the README")
# Expected: 0.40 - 0.65

# Completely different tasks → low similarity
sim("Fix the database race condition", "Summarise the OAuth2 spec")
# Expected: < 0.35

# Same bucket, different complexity → moderate-high similarity
sim("Write a hello world in Python", "Implement a concurrent LRU cache with TTL")
# Expected: 0.30 - 0.55

# Semantically identical, rephrased → very high similarity
sim("Explain how B-trees handle page splits", "How do B-tree page splits work?")
# Expected: > 0.90
```

If these ranges don't hold, reconsider the model choice. BGE-small-en-v1.5 is the fallback.

---

## Appendix B: SQLite Schema for Embedding Cache

```sql
-- ~/.mahoraga-v2/embedding_cache.sqlite

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;    -- WAL mode makes this safe; faster writes
PRAGMA cache_size = -2000;      -- 2 MB page cache

CREATE TABLE IF NOT EXISTS embeddings (
    text_hash   TEXT PRIMARY KEY,       -- sha256(text.strip().lower()) hex digest
    model_id    TEXT NOT NULL,          -- e.g., 'all-MiniLM-L6-v2'
    dim         INTEGER NOT NULL,       -- 384 (for validation on read)
    embedding   BLOB NOT NULL,          -- np.float32 array as bytes, length = dim * 4
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Index for cleanup queries: "delete all entries for old model"
CREATE INDEX IF NOT EXISTS idx_embeddings_model 
    ON embeddings(model_id);

-- Useful for cache stats
CREATE INDEX IF NOT EXISTS idx_embeddings_created 
    ON embeddings(created_at);
```

**Read path:**
```sql
SELECT embedding, dim FROM embeddings 
WHERE text_hash = ? AND model_id = ?;
```

**Write path:**
```sql
INSERT OR REPLACE INTO embeddings (text_hash, model_id, dim, embedding) 
VALUES (?, ?, ?, ?);
```

**Cache stats (for `orch memory inspect`):**
```sql
SELECT model_id, COUNT(*) as entries, 
       MIN(created_at) as oldest, 
       MAX(created_at) as newest,
       SUM(LENGTH(embedding)) as total_bytes
FROM embeddings 
GROUP BY model_id;
```

---

## Appendix C: HNSW Index Lifecycle

```
                  First run (no index)
                         │
                         ▼
              Create empty index (dim=384)
              Store in memory, don't persist
              until first episode is added
                         │
                         ▼
              ┌──── Normal operation ────┐
              │                          │
              │  add_episode():          │
              │    1. Add to HNSW        │
              │    2. Store metadata     │
              │    3. Persist index      │
              │       every 100 episodes │
              │       (batch persist)    │
              │                          │
              │  query_semantic():       │
              │    1. HNSW knn search    │
              │    2. Look up metadata   │
              │    3. Compute bias       │
              │                          │
              └──────────┬───────────────┘
                         │
              Episode count hits max_elements
                         │
                         ▼
              FIFO eviction:
                1. mark_deleted(oldest_id)
                2. Remove from metadata
                3. Insert new episode
                4. If >20% deleted:
                   rebuild index (async?)
                         │
                         ▼
              ┌──── Startup (index exists) ───┐
              │                                │
              │  Load meta.json                │
              │  Check version, dim, model_id  │
              │    ├── Match → load index      │
              │    └── Mismatch → warn,        │
              │        fall back to 9-dim,     │
              │        suggest backfill        │
              └────────────────────────────────┘
```

**Persistence strategy:** Don't persist the HNSW index to disk on every `add_episode()` call — hnswlib serialisation is O(n) and takes ~100ms for 10K elements. Instead, persist every 100 episodes and on graceful shutdown. On crash, at most 100 episodes are lost from the HNSW index; they're still in the metadata store and can be re-indexed on next startup.