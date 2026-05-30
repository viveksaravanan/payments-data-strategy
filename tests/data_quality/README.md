# Data-quality acceptance tests (Wave 1)

This directory holds the Wave 1 realism battery (T1–T18 in
`docs/SPEC_wave1_data_generation.md` §6). Each test is a
distribution-level band check against the generated Parquet
dataset, not a point check.

Tests land here progressively as the generation pipeline lands
(Stages 4 and 6 of the Wave 1 build). A test stub or sanity
collector runs from Stage 1 so pytest can discover this directory
even before the realism battery is implemented.

Pattern for each Tn:
- Load the relevant Parquet table via `src/storage/duckdb_io.py`.
- Compute the measured number in DuckDB SQL or pandas.
- Assert the band stated in the SPEC. Bands, not point values.
- Emit the measured number into the data-quality report
  (`scripts/build_dq_report.py`) so an exec can read T-by-T
  what was checked and what came back.

Pause-and-ask triggers from SPEC §0 apply: if a realism test
fails and the fix would change a ratified D2–D20 number, stop
and surface the conflict rather than silently retuning.
