.PHONY: seed demo test clean report

seed:
	uv run python -m src.generate.run_all
	uv run python -m src.anonymize.pipeline
	uv run python -m src.db.seed

report:
	uv run python scripts/generate_report_data.py
	@echo
	@echo "Interactive report: file://$(PWD)/docs/report.html"

demo: seed report
	uv run streamlit run src/dashboard/app.py

test:
	uv run pytest

clean:
	rm -rf data/raw data/anon data/*.db
	mkdir -p data/raw data/anon/tenant data/anon/lake
