"""Make a working path real on disk before something reads it.

A page in an open project may exist only as a blob inside the project file, and
a page from an archive only as an entry that has not been extracted yet. Both
resolve to a working path that nothing has written yet. Anything about to open
one by name calls this first.

The project-blob half lives in `app.projects.project_state`, which pulls in the
Qt-aware project parsers. That import is deferred to call time so the pipeline
can import this module headlessly: a headless process has no open project, so
the branch that needs it never runs, and when the app *is* running the import
is a cache hit.
"""

from __future__ import annotations

from modules.utils.file_handler import ensure_prepared_path_materialized


def ensure_path_materialized(path: str) -> bool:
    """True if `path` now exists on disk because of, or before, this call."""
    if _ensure_project_blob_materialized(path):
        return True
    return ensure_prepared_path_materialized(path)


def _ensure_project_blob_materialized(path: str) -> bool:
    try:
        from app.projects.project_state import ensure_project_blob_materialized
    except ImportError:
        # No Qt, so no open project to materialise a blob out of.
        return False
    return ensure_project_blob_materialized(path)
