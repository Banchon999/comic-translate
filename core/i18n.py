"""Translation lookup that does not require Qt to be importable.

The catalogues and the lookup itself belong to Qt — `comic.py` installs
`QTranslator`s and `QCoreApplication.translate` consults them. But the strings
being translated are not all in the UI layer: the pipeline builds user-facing
error text on worker threads, and that code has to import in a headless
process.

So the import is deferred to call time. With Qt present this is exactly
`QCoreApplication.translate`; without it, the source string is returned
untranslated, which is the correct answer for a process that has no UI to
show it in.

Keep passing literal strings at the call site. `lupdate` scans source text, so
a computed string is invisible to it and never reaches a catalogue — see the
`@fallback` note in CLAUDE.md for how contexts are resolved.
"""

from __future__ import annotations


def translate(context: str, source_text: str, disambiguation: str | None = None,
              n: int = -1) -> str:
    """`QCoreApplication.translate`, or the source string when Qt is absent."""
    try:
        from PySide6.QtCore import QCoreApplication
    except ImportError:
        return source_text
    return QCoreApplication.translate(context, source_text, disambiguation, n)
