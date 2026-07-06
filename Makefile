.PHONY: seed seed-pilot demo test test-quick clean dq-report lake-items agent-preview catalog push-data

# datamodel-v2: author the static committed catalog. Emits the
# observable data/catalog/products.csv (committed) + the hidden
# data/eval/canonical_map.csv (answer key). Deterministic; re-run and
# re-commit to change the item master.
catalog:
	uv run python scripts/build_catalog.py

# v4: generate the tenant census Parquet at full scale.
seed:
	uv run python -m src.generate.engine.run_all

# Pilot mode (5k cards, ~5 min). Used for fast iteration.
seed-pilot:
	uv run python -m src.generate.engine.run_all --scale 5000

# Full quality battery (T1-T18) — runs against a fresh pilot rebuild.
test:
	uv run pytest

# Engine unit tests only — skip the slow data-quality fixture rebuild.
test-quick:
	uv run pytest --ignore=tests/data_quality

# Regenerate the Markdown DQ report from the current Parquet output.
dq-report:
	uv run python scripts/build_dq_report.py

# Wave 3.5: build the per-viewer line-item peer lake (5 pairs +
# routing metadata) from data/raw/ to data/lake/items/<VIEWER>/.
# (The Wave 2 aggregate-lake builders were removed in Stage E, §11.)
lake-items:
	uv run python scripts/build_line_items.py

# Wave 3: generate docs/AGENT_PREVIEW.html — the human-review surface
# for the L1-L12 agents over the suggested-question registry. Needs
# ANTHROPIC_API_KEY in the environment for the live LLM calls.
# Override --merchant on the command line if you want a different viewer.
agent-preview:
	uv run python scripts/preview_agent.py --merchant KRG --batch

# v4 demo path is rebuilt in Wave 4 (dashboard adapts to Parquet via
# DuckDB). For the working v3 demo, check out tag v3-final.
demo:
	@echo "v4 demo path is rebuilt in a later wave; check out tag v3-final for v3."

clean:
	rm -rf data/raw data/eval data/*.db data/parquet
	mkdir -p data/raw data/eval

# Sync the full-scale data/ tree to the companion HF Dataset repo that the
# Space downloads on boot. Run this after every `make seed` + `make
# lake-items` regen, then redeploy — otherwise the Space serves stale data
# against new pills. ALLOWLIST only (data/raw + data/lake/items); the eval
# answer key is never uploaded. Requires a logged-in `huggingface-cli`.
push-data:
	huggingface-cli upload viveks2862/payments-data-strategy-data ./data/raw raw --repo-type dataset
	huggingface-cli upload viveks2862/payments-data-strategy-data ./data/lake/items lake/items --repo-type dataset
