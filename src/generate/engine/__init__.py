"""v4 generation engine — segment-agnostic, config-driven (D12).

One module per layer of the D11 causal chain. Each module reads from
the config tree under ``src/generate/config/`` and emits Parquet
through ``src/storage/duckdb_io.py``.

Stage 3 (Wave 1) ships these as docstring stubs. Each Stage 4
sub-stage implements one module in causal order:

  4.1 geography → D13 (Layer 1)
  4.2 population → D14 (Layer 2)
  4.3 customers → D16 (Layer 3, durable state)
  4.4 trips → D15 + D15b (Layer 4, temporal + store resolution)
  4.5 baskets → D17 (Layer 5)
  4.6 payment → D18 (Layer 6)
  4.7 pricing → D19 (Layer 7)
  4.8 events  → D20 (Layer 8 — promos + planted anomalies)

``run_all.py`` orchestrates the layers in order and produces the
Wave 1 output contract (SPEC §5) under ``data/raw/`` plus the
answer key under ``data/eval/``.
"""
