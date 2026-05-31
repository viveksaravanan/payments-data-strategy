.PHONY: seed seed-pilot demo test test-quick clean dq-report

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

# v4 demo path is rebuilt in Wave 4 (dashboard adapts to Parquet via
# DuckDB). For the working v3 demo, check out tag v3-final.
demo:
	@echo "v4 demo path is rebuilt in a later wave; check out tag v3-final for v3."

clean:
	rm -rf data/raw data/eval data/*.db data/parquet
	mkdir -p data/raw data/eval
