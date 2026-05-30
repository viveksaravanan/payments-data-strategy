"""Config-driven generation (D12).

The whole generator is parameterized by these YAML files. Adding a
6th merchant or a new segment is a config addition; no engine code
changes. The loader (`load_config`) reads the tree and validates
the D12 invariants enumerated in SPEC §3.
"""
from src.generate.config.loader import (
    Config,
    ConfigInvariantError,
    load_config,
)

__all__ = ["Config", "ConfigInvariantError", "load_config"]
