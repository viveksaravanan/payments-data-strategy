.PHONY: seed demo test clean report

# Wave 1 (v4): seed/demo/report are being rebuilt against the new
# DuckDB+Parquet engine at src/generate/engine/. Until Stage 4 of the
# Wave 1 build lands, these targets are stubs that point at the v3
# generator (CSV output) only; src/db/seed.py is quarantined.
# The v3 demo continues to work on the `main` branch and the
# `v3-final` tag.
seed:
	uv run python -m src.generate.run_all

report:
	uv run python scripts/generate_report_data.py
	uv run python scripts/build_report_html.py
	@echo
	@echo "Interactive report: file://$(PWD)/docs/report.html"

demo: seed
	@echo "v4 demo path is being rebuilt; check out tag v3-final for the working v3 demo."

test:
	uv run pytest

clean:
	rm -rf data/raw data/*.db data/parquet
	mkdir -p data/raw
