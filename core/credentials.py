"""Access to the signed-in user's tokens, without depending on how they are stored.

The desktop app keeps them in the OS keyring plus `QSettings`, which is Qt. The
OCR and translation factories need to know whether a token exists in order to
choose the hosted engine over a local one, and they have to import without Qt.

So the lookup goes through a provider. The default one asks the app's token
storage if it can be imported, and answers None otherwise — which is the right
answer for a process with nobody signed in. A sidecar that *does* have
credentials calls `set_token_provider` with its own.
"""

from __future__ import annotations

from typing import Callable, Optional

TokenProvider = Callable[[str], Optional[str]]

_provider: Optional[TokenProvider] = None


def set_token_provider(provider: Optional[TokenProvider]) -> None:
    """Install the token lookup for this process. None restores the default."""
    global _provider
    _provider = provider


def get_token(name: str) -> Optional[str]:
    """The named token, or None if there is none and nowhere to look."""
    if _provider is not None:
        return _provider(name)
    return _default_get_token(name)


def _default_get_token(name: str) -> Optional[str]:
    try:
        from app.account.auth.token_storage import get_token as app_get_token
    except ImportError:
        # No Qt, so no QSettings-backed storage and nobody signed in here.
        return None
    return app_get_token(name)
