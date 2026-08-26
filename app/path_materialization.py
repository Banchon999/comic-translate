"""Backwards-compatible re-export.

The implementation moved to `core.path_materialization` so the pipeline can
import it without reaching into `app`. Existing Qt-side callers keep this name.
"""

from core.path_materialization import ensure_path_materialized

__all__ = ["ensure_path_materialized"]
