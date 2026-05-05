"""Anonymization pipeline CLI.

    uv run python -m src.anonymize.pipeline

Runs Stage 1 (tenant) then Stage 2 (lake).
"""
from . import lake, tenant


def main() -> None:
    tenant.run()
    lake.run()


if __name__ == "__main__":
    main()
