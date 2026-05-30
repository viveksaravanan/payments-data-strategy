"""v4 engine orchestrator. Drives the 8 layers top-down.

Reads the config tree at ``src/generate/config/``; threads one
seeded RNG (``np.random.default_rng(cfg.global_['seed'])``) through
every layer; emits Parquet to ``data/raw/`` (tenant tables, per the
SPEC §5 contract) and ``data/eval/`` (the A1-A3 ``anomalies_
groundtruth`` answer key).

Stage 3 ships a docstring stub. Stage 4.1+ fills it in as each
layer module lands. Pilot mode (``--scale`` flag, Wave 1 plan
amendment) runs ~5-10k cards through the entire pipeline for fast
T11/T17 feedback before committing to full-scale generation.
"""

# Stage 4 fills this in layer by layer.
