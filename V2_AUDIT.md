# V2 Audit — Payments Data Strategy Demo

Branch: `v2`. Working tree clean. Most-recent commit: `369a070 html report`.
Generated: 2026-05-06.

This audit is structured for prioritization. Severity is rated against the
demo's credibility and a hypothetical near-term production push.

---

## Section 1 — Repo & code quality

### 1.1 Project structure & packaging

| Item | Finding | Severity | Effort |
| --- | --- | --- | --- |
| `pyproject.toml` is missing a `[build-system]` and `[tool.setuptools|hatch].packages` block | The package isn't installable. `uv run` works because uv adds the project root to `sys.path` via a virtualenv shim, but external tooling (mypy, ruff in package mode, `pip install -e .`) won't find `src.*`. | **High** | 15 min |
| Two `sys.path.insert(...)` hacks: `src/dashboard/app.py:17` and `scripts/generate_report_data.py:26` | Both exist because `src.*` isn't on the path when Streamlit / a bare script runs. Replacing with proper package install would delete both. | High | 15 min (after fix above) |
| `src/config.py` exists but is empty (0 bytes) | Dead file from Block-0 scaffolding. Confused with `src/generate/parameters.py` (the real config). Delete or repurpose. | Low | 1 min |
| No `[project.scripts]` entry points | Could expose `payments-seed`, `payments-report` as console scripts after package install. Polish. | Low | 10 min |
| Layout otherwise idiomatic | `src/<package>/` layout, `tests/` parallel, `scripts/` for one-shot tooling, `docs/` for static deliverables. ✓ | — | — |

**Verdict:** the packaging gap is the only structural issue, but it's the *cause* of every `sys.path` hack in the repo. Fixing it once removes both hacks.

### 1.2 Test coverage gaps

`uv run pytest --collect-only` reports **69 tests across 4 files**. Distribution:

| File | Tests | What it covers | What it does NOT cover |
| --- | ---: | --- | --- |
| `test_generation.py` | 14 | counts, PII presence, foreign keys, EBT rule, cross-merchant PAN invariant, determinism | basket-shape correctness, anomaly presence, affinity-pair conditional probabilities, day-of-week / pay-cycle multipliers actually applied |
| `test_anonymize.py` | 14 | column drops, hash format, k-anonymity invariant, lake collapse to category, suppression > 0 | leak paths through CSV intermediaries, l-diversity (deferred but no test placeholder), DP (claimed stub doesn't exist) |
| `test_db.py` | 14 | schema applies, indexes, FKs declared, row counts match, no PII columns | actual cross-merchant query results vs expected ranges, query-plan sanity for the canned questions, the canned `queries.py` functions |
| `test_agents.py` | 27 | SQL guard (DROP/INSERT/UPDATE/etc.), tenant isolation predicate, MAX_TURNS termination, mock mode returns *something*, tool-surface composition | end-to-end loop with real or fake-Claude tool execution, response-shape on zero-row queries, what the agent says when asked an unanswerable question, mock-response quality (only checks `len(sql) >= 1`), Network-Analyst language leakage, the actual SQL the agent typically writes |

**Untested entirely:**
- `src/dashboard/app.py` — no tests. Streamlit is hard to unit-test, but the `_get_agent` factory and `_render_response` pure helper could be extracted and tested.
- `scripts/generate_report_data.py` — no test. Numeric drift here breaks the report silently.
- `src/db/queries.py` — five canned-query functions, none exercised by tests.
- `src/anonymize/generalize.py` — both helpers are tested only indirectly through lake CSVs.

**Severity:** Medium — the missing tests aren't security-critical, but the "does mock mode actually answer the question" gap is what bites at demo time.

### 1.3 Type hints & static analysis

Type-hint coverage is good in `src/generate/base.py`, `src/agents/tools.py`, `src/agents/advisor.py` (return types and parameter types throughout). Gaps:

| Function | Missing hint | Risk |
| --- | --- | --- |
| `src/generate/kroger.py::generate` (and twins in `taco_bell.py`/`tjmaxx.py`) | No return type — actually returns a 4-tuple `(catalog, stores, txns, items)`, all DataFrames. Calling code in `run_all.py` unpacks positionally; a column rename anywhere would break silently. | Medium |
| `src/anonymize/lake.py::_apply_k_anonymity` | Returns `tuple[pd.DataFrame, int]` annotated but the inner `transform("size")` result is operated on with `.copy()` after a boolean mask — easy to refactor wrong. | Low |
| `src/agents/advisor.py::_dispatch` | Takes `args: dict[str, Any]` and dispatches by `name: str`; no validation that `args` matches the tool's input_schema. mypy in strict mode would flag the `args["query"]` access. | Medium |
| `src/agents/tools.py::_exec_select` | `db_path: Path` but callers may pass `None`. Typed correctly upstream but worth a one-line `assert`. | Low |
| `scripts/generate_report_data.py::_*` private helpers | Most return `dict` or `list[dict]` with no explicit shape. A misspelled key in the JSON would break the HTML silently. | Medium |

Adding **mypy** with a relaxed config (`--ignore-missing-imports`, no strict mode) would surface ≈10–20 issues, mostly small. **pyright** in basic mode is faster but pickier. ~30 min to add either + first-pass cleanup.

### 1.4 Code duplication

| Duplication | Location | Note |
| --- | --- | --- |
| Agent loop body | `advisor.py::_run_loop` and `analyst.py::_run_loop` share ~95% of their structure | CLAUDE.md explicitly says "duplicated rather than abstracted ... keeps both files legible." Defensible decision, but a small `_BaseAgent` mixin would now save edit cost when (e.g.) adding token-usage tracking. **Low** severity. |
| Store-builder boilerplate | `kroger.py::build_stores`, `taco_bell.py::build_stores`, `tjmaxx.py::build_stores` are near-identical | Adding a 4th merchant would make this hurt. **Medium** — fix when adding the 4th merchant. |
| `merchant_id` → friendly-name lookup | Built inline in `advisor.py:23`, again in `dashboard/app.py:30`, again in `generate_report_data.py:31` | A single helper would centralize. **Low**. |
| Schema-column lists | Hard-coded twice: in `tenant.py`/`lake.py` (CSV writers) and in `seed.py` (DB loaders) | If a column is added to the schema, three files change. **Medium**. |

### 1.5 Configuration sprawl

Configuration is **mostly** centralized in `src/generate/parameters.py` (`MERCHANT_CONFIGS`, `RANDOM_SEED`, `K_ANONYMITY_THRESHOLD`, `HASH_SECRET`). Leaks:

- `MAX_TURNS = 6` in `src/agents/advisor.py:25` — agent-specific knob, fine to live in agents/, but should be re-exported from a single constants module.
- `MODEL = "claude-opus-4-7"` in `src/agents/advisor.py:26` — hard-coded model name. Should be reading from env or a constant.
- `DB_PATH` / `SCHEMA_PATH` recomputed in `tools.py`, `seed.py`, `dashboard/app.py`, `generate_report_data.py`. Each reaches up `parents[N]` — fragile if a file moves.
- Dashboard's `CANNED_QUESTIONS` dict is a 60-line literal inside `app.py` — would be cleaner in `src/dashboard/questions.py` or referenced from the `prompts/` markdown.
- Mock-mode canned answers live as inline strings inside `_mock_response` methods — fine for now, but `src/agents/CLAUDE.md` says "canned responses live as constants in each agent file" — they're inline, not constants. Documentation drift.

**Severity:** Low — works correctly, just creeps when adding a fourth merchant or a fourth agent.

### 1.6 Error handling

| Site | Behavior | Verdict |
| --- | --- | --- |
| `advisor.py:137`, `analyst.py:104` — bare `except Exception as exc` inside the tool loop | Catches any tool exception (including the SQL-guard `ValueError`) and feeds it back to the model as `tool_result.is_error: true`. ✓ correct. | OK |
| `advisor.py:176`, `analyst.py:141` — bare `except Exception:` inside `_mock_response` | Falls back to a hand-rolled empty result if the live SQL probe fails. Silent. Should at least log to stderr. | Low |
| `dashboard/app.py:206` — bare `except Exception as e` around `agent.ask(...)` | Renders the message via `st.error`. Reasonable. ✓ | OK |
| `dashboard/app.py:190` — bare `except Exception` around chart rendering | Renders a small `st.caption("could not render chart: ...")`. Fine. | OK |
| Anthropic API errors specifically | Not differentiated. A 401 / 429 / 5xx will all surface as "Agent run failed: ..." with the SDK's raw message. Could be friendlier. | Medium (only at demo time) |
| Generator on bad config | Asserts on catalog size, but `MERCHANT_CONFIGS["payment_mix"]` is not validated to sum to 1.0 — `numpy.random.choice` will silently renormalize | Low |

No `except:` (bare) anywhere outside tests. Good.

### 1.7 CI / automation

- `.github/` directory does **not exist**. Zero CI.
- A minimal workflow would: install uv, `uv sync`, `uv run pytest`. Total: ~15 lines of YAML, runs in <60s.
- No pre-commit hooks. `ruff` is in `dev` deps but never invoked.

**Severity:** Medium — the demo runs locally and will keep running locally, but you have no automated guard against PRs that break the suite.

### 1.8 Dependency management

- `uv.lock` is **committed** (`git ls-files` confirms). ✓
- `pyproject.toml` deps: 6 runtime, 2 dev. All pinned to lower bounds only (`>=`). Lock file gives reproducibility. Acceptable.
- No `requirements.txt` fallback for non-uv users. README says uv-only. Fine for the audience but limits drive-by contribution.
- Dependencies are minimal — no cruft (no unused packages installed for one feature). ✓
- No transitive vulnerability scan in CI.

**Severity:** Low.

### 1.9 Dead code, missing stubs

- **`src/config.py` is empty** (0 bytes). Created by Block-0 scaffolding, never used. Delete.
- **The "differential privacy stub" referenced by `CLAUDE.md`, `PLAN.md` §7.2, `ARCHITECTURE.md` §8.2 does not exist as a file.** No `dp.py`, no docstring-only module under `src/anonymize/`. The doc claim is stale — see §3 below.
- `src/generate/CLAUDE.md` describes a `forecaster.py` (stretch) that doesn't exist. Documented as stretch in `PLAN.md`, so this is fine.
- `tests/__init__.py` is empty (intentional — pytest doesn't require it). OK.

**Severity:** Low for the empty file; **Medium** for the DP-stub claim because it's a *truthfulness* issue in user-facing docs.

---

## Section 2 — Logic & correctness

### 2.1 Synthetic data realism

Diagnostics run live against `data/payments.db`:

#### Per-SKU revenue leaderboard (Kroger)

```
Top 12 SKUs by 90-day revenue at Kroger:
  PET   KRG-PET-0013    $33,254  (680 units)
  PET   KRG-PET-0047    $31,526  (633)
  PET   KRG-PET-0016    $31,435  (658)
  PET   KRG-PET-0006    $31,212  (662)
  PET   KRG-PET-0024    $31,129  (651)
  PET   KRG-PET-0041    $30,990  (643)
  BABY  KRG-BABY-0010   $30,336  (1,049)   <- Infant formula
  BABY  KRG-BABY-0071   $29,854  (780)
  PET   KRG-PET-0012    $29,843  (630)
  BABY  KRG-BABY-0024   $29,822  (750)
  BABY  KRG-BABY-0015   $29,632  (769)
  PET   KRG-PET-0019    $29,166  (664)
```

**Per-SKU revenue by category:**

```
PET        50 SKUs   $20,188 / SKU   <- 4× DAIRY
BABY       80 SKUs   $16,107 / SKU   <- 3.5× DAIRY
MEAT      150 SKUs   $10,957 / SKU
HOUSEHOLD 120 SKUs    $7,164 / SKU
PANTRY    220 SKUs    $6,527 / SKU
BEVERAGES 160 SKUs    $6,306 / SKU
FROZEN    140 SKUs    $5,943 / SKU
PERSONAL  100 SKUs    $5,247 / SKU
PRODUCE   120 SKUs    $5,173 / SKU
DAIRY     140 SKUs    $4,453 / SKU
BAKERY     80 SKUs    $4,011 / SKU
SNACKS    140 SKUs    $3,717 / SKU
```

**Issue:** Pet is the smallest catalog (50 SKUs) with the highest base prices (range $4.99–$49.99) and uniform basket sampling, so each Pet SKU is sampled disproportionately often vs. (e.g.) Pantry (220 SKUs). This is **visible in canned Q1** ("top categories by revenue last week") — the answer features Pet at the top, which is implausible for a real grocery basket. **Severity: Medium-High** (the demo's most-frequently-run question lands on a slightly silly answer). **Effort to fix:** ~30 min to weight basket sampling by category-popularity priors — a simple `category_weight` dict in `parameters.py`.

#### Avg ticket: spec vs. actual

```
                spec       actual    delta
Kroger          $65.00     $156.80   +141%
Taco Bell       $9.00      $17.45    +94%
TJ Maxx         $55.00     $266.76   +385%
```

**Issue:** `MERCHANT_CONFIGS["avg_ticket"]` is set in `parameters.py` but **the generator never reads it** (`grep "avg_ticket"` shows it appearing only in the config dict and in DATA.md spec tables). The actual ticket comes out of `base_price × bimodal_basket_size × price_noise`. TJ Maxx's catalog has prices up to $199.99 with avg basket 5.11 — yields $266 avg ticket vs. claimed $55. Severity: **High** — the merchant comparison table in the dashboard and report shows ticket sizes that contradict the documented spec. Effort: 1–2 hours to either (a) drop `avg_ticket` from `parameters.py` and update DATA.md to describe what's actually generated, or (b) calibrate basket sizes so ticket lands near the spec.

#### Avg basket size

```
                spec    actual
Kroger          12      15.86
Taco Bell        3       4.05
TJ Maxx          4       5.11
```

Roughly +25–30% across the board — consistent with the bimodal-basket sampling running slightly hot. Within tolerance for demo, but pairs with the avg-ticket issue.

#### EBT correctness

```
EBT transactions total: 6,726
EBT baskets containing BAKERY items: 3,084 (45.9%)
EBT lines with PET / HOUSEHOLD / PERSONAL: 0   <- generator filter works
```

The EBT-eligibility filter **is** enforced (no Pet/Household/Personal items in EBT baskets). The 45.9% figure for "EBT baskets with bakery" is *correct* — only the last 30% of bakery SKUs are flagged as prepared/ineligible; the eligible 70% (bread, buns, tortillas) is legitimate EBT-eligible food. ✓ **No action needed.**

#### Affinity pairs — actual vs. target conditional probability

```
Diapers   -> Formula           target 0.45  actual 0.438  Δ -0.012  ✓
Spaghetti -> Marinara          target 0.55  actual 0.560  Δ +0.010  ✓
Tortillas -> Ground beef       target 0.40  actual 0.423  Δ +0.023  ✓
Tortillas -> Shredded cheese   target 0.45  actual 0.439  Δ -0.011  ✓
Whole milk-> Cereal            target 0.30  actual 0.293  Δ -0.007  ✓
Coffee    -> Half & half       target 0.40  actual 0.405  Δ +0.005  ✓
```

All within 3 percentage points of target. **No action.**

#### Day-of-week peak

```
            Mon-Thu   Sat-Sun     ratio    target ~1.4
Kroger        8,324    12,991     1.56     ✓
Taco Bell     3,777     4,779     1.27     low (TBL peak is Fri/Sat, comparison underweights Friday)
TJ Maxx       1,083     1,718     1.59     ✓
```

Within tolerance. ✓

#### Pay-cycle bumps

```
            other/day   1-3/day   bump-1-3   15-17/day   bump-15-17   target ~1.15
Kroger        728.1      898.4    1.23×       850.8       1.17×        ✓
Taco Bell     328.3      402.8    1.23×       361.6       1.10×        ✓
TJ Maxx        95.2      116.7    1.23×       109.8       1.15×        ✓
```

Within tolerance. ✓

#### Planted anomalies — all three present

```
Anomaly 1: KRG-PRODUCE-0042 max unit_price by date
  2026-05-02   max $27.45   avg $24.89   n=11    <- 5× spike day, base price $5.49
  Other dates: max ~$5.49 (within ±10% noise)
```

```
Anomaly 2: KRG-OH-0011 transaction count
  prior 7 days: 244 transactions
  last  7 days: 173 transactions
  drop: 29.1%   target ~30%   ✓
```

```
Anomaly 3: new baby-aisle buyers in last 21 days at Kroger
  Customers who never bought BABY before but bought in last 21d: 66
  Target: ~50    ✓ (slightly higher because the cohort surge appends to natural baby buyers)
```

All three anomalies detectable. ✓

### 2.2 Anonymization correctness

**PII leak scan:** ran a column-name audit across `data/anon/tenant/*.csv`, `data/anon/lake/*.csv`, and every table in `payments.db`:

```
data/anon/tenant/customers.csv         cols-of-concern=none
data/anon/tenant/transactions.csv      cols-of-concern=none
data/anon/lake/customers.csv           cols-of-concern=none
... (all 9 anon CSVs clean)
SQLite tables: no `customer_name`, `customer_email`, or `customer_pan` column anywhere.
```

✓ No PII path into anonymized output.

**Quasi-identifier set for k=5:** `lake.py::_apply_k_anonymity` uses `["age_band", "income_band", "home_zip3"]` (correct per spec).

**`customer_id` stability across merchants:** sampled 5 multi-merchant customers, all show identical `customer_id` across `KRG`, `TBL`, `TJX` rows in `tenant_transactions`. ✓

**SKU recoverability through lake aggregation** (potential side-channel):

```
50,000 rows of lake_transaction_items sampled
distinct unit_price values per category: 648 (SNACKS) – 2,235 (BABY)
distinct (sku_category, unit_price) tuples: 15,163 / 50,000 rows = 30.3%
```

Since `unit_price = round(line_total / sum(qty), 2)`, prices are essentially continuous. The lake retains a per-line-item floating-point unit_price, so a cell is rarely globally unique to one underlying SKU in practice — but it *is* possible in principle to construct an attack that joins on near-equal prices to recover a small SKU's identity. Severity: **Low** for the demo (synthetic data anyway), **Medium** for production. Mitigation in roadmap: bin `unit_price` to dollar buckets in lake, or drop it entirely and keep only `line_total`.

### 2.3 Agent behavior gaps

| Question | Current behavior | Severity |
| --- | --- | --- |
| What if the user asks something **not in the data** (e.g., weather, store hours, employee count)? | Agent calls `schema_info`, sees no relevant table, then either invents a query that returns empty rows or hits MAX_TURNS. Final answer typically hedges ("I don't have data for X") but not always. **Untested.** | Medium |
| What if a SQL query returns **zero rows**? | The system prompt instructs the agent to say "no rows returned" — but enforcement is by the model, not the runner. Periodic hallucination risk on this path. **Untested.** | Medium |
| What if the agent hits `MAX_TURNS = 6`? | `AgentResponse(converged=False, answer="(agent reached MAX_TURNS without a final answer)")` is returned. Dashboard renders the warning banner correctly. ✓ | OK |
| Mock mode for **all 4 roles × 3-5 questions = up to 19 paths** | Mock just returns one canned response per role (live SQL fallback at `advisor.py:174`). Same answer regardless of question asked. Dashboard makes this look real. **Severity: Medium** — at a demo where the API is down, every Kroger question returns the same paragraph. | Medium |
| Network Analyst leaking tenant-flavored language | The system prompt explicitly forbids "your stores" and references "the panel" / "merchants in the panel" instead. **Not tested.** Manual scan of `analyst.md` looks correct. | Low |
| Agent emits malformed tool input (missing `query` key) | `_dispatch` would `KeyError`; `except Exception as exc` catches it, returns error to model. Recoverable. ✓ | OK |
| Agent emits SQL with the right `merchant_id` but a wrong table reference | Falls through to SQLite, which raises; same `except` path. ✓ | OK |

### 2.4 Demo gap analysis

| Gap | Visibility from canned questions | Severity |
| --- | --- | --- |
| **Only one merchant per segment** — Kroger Q4 ("compare to other merchants in the panel") and the prompt's "grocery peers" example query produce a 1-row "peer" comparison (just Kroger). | **Highly visible.** The canned Q4 lands on a single-row table. Agent hedges, but the demo's "industry benchmark" framing collapses. | **High** |
| **Pet/Baby leaderboard** at Kroger Q1. | Visible on every run of Q1 (the first canned question). | Medium-High |
| **Avg ticket inflated** (TJ Maxx $266 vs. claimed $55). | Visible in the dashboard's merchant comparison table and the HTML report's sortable table. Anyone reading the table will spot the discrepancy if they know retail averages. | High |
| **Seven-agents claim** | Built two; report's `agents-grid` correctly labels the other five as "Roadmap" with §10.2 references. ✓ | OK |
| **k=5 vs. k=50** | Disclosed in the report's amber callout, the README, and ARCHITECTURE.md. ✓ | OK |
| **DP "stub"** referenced in 3 docs but file doesn't exist | If a careful reader follows the breadcrumb they hit a dead claim. | Medium |
| **Anomalies** are findable but the agent has to be asked the right question — no proactive surfacing | Demo script handles this. | OK |

---

## Section 3 — Documentation drift

For each doc, claims that no longer match reality:

### `CLAUDE.md`

| Line | Claim | Reality | Severity |
| --- | --- | --- | --- |
| 58 | "Differential privacy beyond a documented stub" | No DP file exists in `src/anonymize/`. | Medium |
| 43 | "Agents NEVER mutate the DB. `MAX_TURNS = 6`." | Both correct. ✓ | OK |
| 19 | "Streamlit reruns the whole script on every interaction. Cache agent clients with `@st.cache_resource`." | `dashboard/app.py:142` does cache. ✓ | OK |

### `PLAN.md`

| Section | Claim | Reality |
| --- | --- | --- |
| §7.2 step 4 | "**Differential privacy stub.** A no-op module with a docstring documenting where ε-bounded Laplacian noise would live." | Module not created. **Drift.** |
| §3 mapping table line | "Privacy techniques ... differential privacy stubbed only" | No stub exists. **Drift.** |
| §9.3 Q1 (after earlier rewrite) | "Top categories by revenue last week, and which subcategories drove each" | Matches advisor.md and the dashboard. ✓ |
| §15 demo script step 5 | "Run question 1 (top categories by revenue)" | Matches. ✓ |
| §13 Block 5 | "Stretch agents" | Forecaster not built — declared as stretch up front. ✓ |
| §6 customer-overlap math | "All three: ~1,080 / Exactly two: ~1,860 / Exactly one: ~1,860 / Zero: ~200" | Actual: 1,035 / 2,537 / 1,309 / 119. **All-three close (-4%); two-merchant +36%; one-merchant -30%.** The simplified independent-event math is wrong; actual distribution is shifted toward "two merchants." Worth a note in DATA.md but not urgent. | Low |

### `DATA.md`

| Section | Claim | Reality |
| --- | --- | --- |
| §3 `MERCHANT_CONFIGS` | `avg_ticket: 65.00 / 9.00 / 55.00` | Generator never reads it; actual avg ticket: $156.80 / $17.45 / $266.76. **Significant drift.** | High |
| §6 customer-overlap | See above | Drift. | Low |
| §10 EBT rule | "EBT transactions exclude prepared foods. Implemented by filtering ... when payment_type is EBT, exclude SKUs with category `bakery` (some prepared) and explicitly mark certain SKUs as EBT-ineligible" | Generator marks **last 30% of bakery + all PET/HOUSEHOLD/PERSONAL** as ebt_eligible=0. Eligible bakery (bread, buns, tortillas) does appear in EBT baskets, which is correct. The DATA.md wording is ambiguous on whether "all bakery" or "some bakery" is excluded — could be tightened. | Low |
| §4 volume math | "~109,300" total transactions | Actual: 107,581. ✓ within rounding |

### `ARCHITECTURE.md`

| Section | Claim | Reality |
| --- | --- | --- |
| §8.2 row | "Differential privacy is a documented stub." | No stub. **Drift.** | Medium |
| §10.2 row | Anomaly Detection "(not built as real-time — anomalies are findable on-demand by the advisor when asked)" | True for the three planted anomalies. ✓ | OK |

### `README.md`

| Line | Claim | Reality |
| --- | --- | --- |
| Question list ✓ updated to "top categories" wording. | | ✓ |
| Cold-start time "~25 seconds" | Verified in earlier clean-checkout run: gen 9s + anon 2s + seed 10s ≈ 21s. ✓ | OK |
| `make report` is referenced indirectly via `scripts/demo.sh`; primary text doesn't mention it. | Minor — not a discrepancy. | OK |

### `docs/report.html` (interactive report)

| Section | Claim | Reality |
| --- | --- | --- |
| Anonymization-section amber callout | "PAN tokenization, ZIP3 + hour-bucket generalization, and k-anonymity. ... Not yet implemented: transaction amount binning, l-diversity, ε-DP, low-volume entity suppression." | Honest — matches code. ✓ | OK |
| Avg-ticket numbers in the merchant comparison table | Pulled live from DB → shows $156 / $17 / $266. **Inconsistent with DATA.md spec values $65 / $9 / $55** but at least internally honest about what was actually generated. | Medium (depends on which doc the viewer cross-references) |
| Cross-merchant headline numbers ($880/$521/$64 at 769 customers) | Pulled live from DB. ✓ | OK |
| Pay-cycle chart legend | Renders 4 day-of-month buckets. ✓ | OK |
| Generated-at timestamp | Pulled from the JSON's `generated_at`. ✓ | OK |

### `src/generate/CLAUDE.md` and `src/agents/CLAUDE.md`

| Module | Claim | Reality |
| --- | --- | --- |
| generate/CLAUDE.md | "Anomalies (price spike, store dropout, cohort surge) are intentional ... All three are at Kroger" | ✓ | OK |
| agents/CLAUDE.md | "The canned responses live as constants in each agent file. Update them when you update the demo questions." | They live as **inline string literals inside `_mock_response`**, not constants. Minor drift — fix is to extract to module-level. | Low |
| agents/CLAUDE.md | "The check lives in `tools.run_tenant_query`." | The function is actually `tools.query_tenant`; `run_tenant_query` does not exist. **Drift.** | Low |

---

## Section 4 — v2 opportunity scan

### 4.1 Add a fourth merchant (a second grocer)

| Aspect | Detail |
| --- | --- |
| What | Add a second grocery merchant — e.g., "Aldi" (smaller basket / lower ticket) or "Whole Foods" (smaller basket / higher ticket / higher organic share). |
| Effort | **2-4 hours.** New `catalog_<m>.py` (~120 lines), `<m>.py` wrapper (~50), entry in `MERCHANT_CONFIGS`, new merchants row, regenerate, re-anonymize, re-seed. Test changes minimal. |
| Demo impact | **High.** Fixes Q4 ("compare to other merchants in the panel") having only one peer. Makes the lake's segment-aggregation queries genuinely informative instead of trivial. The canned "compare basket size to grocery peers" finally has peers. |
| Strategic value | High — addresses the single highest-friction credibility gap at demo time. |

### 4.2 Add a third agent

Highest-impact options from the strategy-doc seven (`§10.2`):

| Agent | Effort | Demo impact | Notes |
| --- | --- | --- | --- |
| **Demand Forecasting** | ~3-4 hours | Medium | Was the documented stretch. Real value comes from explanation, not the model — a 7-day rolling mean per SKU + day-of-week adjustment is trivial; the agent narrates the forecast (recent trend, seasonality cues, anomalies). New tool: `forecast_sku(sku, days_ahead)`. |
| **Anomaly Detection** (proactive) | ~2-3 hours | **High** — surfaces the three planted anomalies *without being asked*. Agent runs a small set of detectors (price-spike, store-dropout, cohort-emergence) on every `make seed` and emits a digest. | Best demo punchline per hour of work. |
| **Payment Optimization** | ~4-5 hours | Medium | Card-mix vs. ticket / acceptance-cost analysis. Useful but visually dry. |
| Dynamic Pricing / Location Intelligence / Customer Segmentation | 4-6 hours each | Variable | Larger pieces, narrower demo lift. |

**Recommendation: Anomaly Detection** for the next agent — it directly leverages the three planted anomalies you already have, and the "the network spotted what the merchant missed" framing is a strong second cross-merchant punchline alongside the existing 769-customer finding.

### 4.3 Implement one deferred anonymization technique

| Technique | Effort | Demo / credibility impact |
| --- | --- | --- |
| **Transaction amount binning** (round `txn_total` and `unit_price` to e.g. $1 buckets in the lake) | **~30 min.** | Medium — closes the SKU-recoverability side-channel from §2.2 and lets the report's amber callout strike one item off the "not yet implemented" list. |
| **l-diversity within k-anonymous groups** (require ≥ ℓ distinct values of a sensitive attribute per quasi-identifier group) | ~3-4 hours | Medium — a real privacy win, lands a third bullet in the anonymization section. |
| **Differential privacy** (ε-bounded Laplace noise on aggregate releases) | ~half day to one day | High — the largest privacy buzz-word on the strategy doc. But the *visible* output is just slightly noisy aggregate counts; hard to demo unless you also build a "release this aggregate" UX. |

**Recommendation: amount binning first** (highest impact-to-effort), l-diversity second.

### 4.4 Real-time / streaming nod

| Approach | Effort | Demo impact |
| --- | --- | --- |
| **"Live mode" toggle** in the dashboard that calls a small refresh endpoint every 30s and updates a counter | ~1 hour | **Medium-High** — visible nod to the streaming story without building Kafka. The counter could read `SELECT COUNT(*) FROM tenant_transactions` plus a fake "+N events / s" simulation. |
| **"Last refresh" timestamp** on every chart in the dashboard and report | ~30 min | Low — feels like deployment hygiene. |
| **A streaming demo script** that incrementally inserts one transaction every 2s for 60s while a `tail -f`-style chart updates | ~3 hours | High but expensive |

**Recommendation: live-mode toggle.** ~1 hour for a credible streaming nod.

### 4.5 Annual seasonality

`DAYS = 730` would 8× the data volume (~860k transactions) and runtime. Adding seasonal multipliers per category (pumpkin spike Oct, holiday baking Nov-Dec, etc.) is ~half day of work and adds a new chart dimension. **Strategic value: Medium** — opens new question types ("how does my November look vs. last November?") but isn't the demo's main argument.

### 4.6 Better visualizations in the dashboard

Highest-impact missing chart: **a customer-cohort flow over time.** The Sankey or stacked-area for "where do customers move between segments month over month" would be the single most striking visual. Effort: 2-3 hours with Streamlit + Altair / Plotly. Strategic value: Medium-High.

---

## Section 5 — Recommended priorities

### Quick wins (do all of these, ≤ 30 min each, ~3 hours total)

| Item | Category | Effort | Impact | Dependencies |
| --- | --- | --- | --- | --- |
| Make the project pip-installable: add `[build-system]` + `[tool.hatch.build.targets.wheel]` (or setuptools equivalent) to `pyproject.toml`; remove both `sys.path.insert` hacks | fix | 20 min | High (cleans up two hacks; enables mypy/CI) | none |
| Delete empty `src/config.py` | fix | 1 min | Low | none |
| Remove (or actually create) the differential-privacy stub: choose to either delete the doc claims or add a 10-line `src/anonymize/dp.py` with a docstring that says "no-op stub for ε-bounded Laplace noise; see §8.2 of strategy doc" | fix | 10 min | Medium | none |
| Fix `src/agents/CLAUDE.md` reference: rename `tools.run_tenant_query` → `tools.query_tenant` | fix | 1 min | Low | none |
| Add a minimal `.github/workflows/test.yml` (uv install, sync, pytest) | fix | 15 min | Medium | none |
| Update DATA.md §3 to remove the unused `avg_ticket` field OR add a note that it's a target the generator currently overshoots | fix | 5 min | High (kills a documentation lie) | none |
| Tighten DATA.md §10 EBT wording to clarify "last 30% of bakery flagged as prepared/ineligible" | fix | 5 min | Low | none |
| Add a single regression test asserting all three anomalies are present in `data/payments.db` | fix | 20 min | Medium | none |

### Targeted investments (1-3 hours each — pick 2-3)

| Item | Category | Effort | Impact | Dependencies |
| --- | --- | --- | --- | --- |
| **Calibrate basket sizes & avg ticket to the spec** — either reduce the bimodal upper-mode multiplier or introduce a category popularity weighting (also fixes Pet/Baby leaderboard) | fix | 1.5 h | **High** — kills the two most-visible data realism gaps (avg ticket + Pet leaderboard) in one pass | none |
| **Mock-mode response quality** — wire each canned question to its own canned answer (currently every Kroger question returns the same response) | fix | 1.5 h | Medium-High (offline-demo failover gets real) | none |
| **Live-mode toggle** in the dashboard (poll every 30s, animated counter + last-refresh timestamp) | addition | 1 h | Medium-High (the streaming nod the strategy doc deserves) | none |
| **Anomaly-detection agent** (3rd agent, runs the three detectors on every `make seed` and emits a digest) | addition | 2.5 h | **High** — surfaces planted anomalies without being asked; a second cross-merchant punchline | the agent loop already exists |
| **Transaction amount binning in the lake** (round `unit_price` to $1 buckets, drop or coarsen `line_total`) | addition | 30 min | Medium (closes SKU-recoverability side-channel; ticks one box off the report's amber callout) | none |
| **CI workflow + ruff + minimal mypy run on every push** | addition | 1.5 h | Medium (builds toward production hygiene) | depends on packaging fix |
| **Tests for `src/db/queries.py`** + an end-to-end test that runs `MerchantAdvisor("KRG")` against a stub-Anthropic and asserts the SQL for one canned question | fix | 2 h | Medium | mock-mode item helps |

### Major additions (half-day or more — pick at most 1)

| Item | Category | Effort | Impact | Dependencies |
| --- | --- | --- | --- | --- |
| **Add a fourth merchant (second grocer)** so peer comparison is real, not single-row | addition | 3-4 h | **Highest** — fixes Q4 entirely; gives the lake a real "industry benchmark" to compute against | none, but should follow the basket-size calibration above |
| **Annual seasonality** (`DAYS = 730`, per-category seasonal multipliers, regenerate) | addition | 4-6 h | Medium-High | parameter cleanup; storage doubles |
| **Implement l-diversity** in `lake.py` (require ≥ ℓ distinct sensitive values per k-anonymous group) | addition | 3-4 h | Medium | k-anonymity is already there |
| **Demand-Forecasting agent** with explanation layer | addition | 3-4 h | Medium | none |

---

## Top three findings (one-paragraph summary)

1. **Avg ticket is silently 2-5× the documented spec** ($156 / $17 / $266 vs. $65 / $9 / $55) because the generator never reads `MERCHANT_CONFIGS["avg_ticket"]` — ticket emerges from `base_price × bimodal_basket_size` instead. This is the single most-visible data realism issue: it shows up in the dashboard merchant comparison, the report's sortable table, and is contradicted by DATA.md §3. Fix is half a day at most (calibrate basket multipliers or update spec).
2. **Peer comparison Q4 has no peers** because each segment has exactly one merchant in the panel. Adding a second grocer (3-4 hours) would turn the demo's "industry benchmark" framing from one-row trivial into a genuine cross-merchant lake query, and is by far the highest-leverage v2 addition.
3. **Documentation drift around the differential-privacy "stub"** — three different docs (PLAN.md, CLAUDE.md, ARCHITECTURE.md) reference a DP stub file that does not exist. A 10-minute fix (either create the stub or delete the references) closes a small but real truthfulness gap. The same applies more cosmetically to a `tools.run_tenant_query` reference in `src/agents/CLAUDE.md` (the function is `tools.query_tenant`).

Quick-win cluster (~3 hours total) closes most documentation drift and packaging cruft. The single highest-impact larger investment is **the fourth merchant**, which directly addresses the demo's structural weakness on segment-peer comparison.
