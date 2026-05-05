.PHONY: seed demo test clean

seed:
	uv run python -m src.generate.run_all
	uv run python -m src.anonymize.pipeline
	uv run python -m src.db.seed

demo: seed
	uv run streamlit run src/dashboard/app.py

test:
	uv run pytest

clean:
	rm -rf data/raw data/anon data/*.db
	mkdir -p data/raw data/anon/tenant data/anon/lake
