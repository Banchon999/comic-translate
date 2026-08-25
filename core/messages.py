"""User-facing message text built off the UI thread.

`app/ui/messages.py`'s `Messages` is a widget layer — every `show_*` builds a
dialog. Two of its members are not: they only assemble localised strings, and
the pipeline calls them from worker threads precisely because they touch
nothing. Those live here, where a headless process can reach them.

`Messages` keeps its methods as thin delegates so existing call sites and the
UI keep working unchanged.
"""

from __future__ import annotations

from core.i18n import translate


def server_error_text(status_code: int = 500, context: str | None = None) -> str:
    """Localised text for a 5xx from the hosted backend.

    Touches no UI. Call it from a worker thread, then hand the result to the
    GUI thread through a signal.

    Args:
        status_code: HTTP status code (500, 501, 502, 503, 504).
        context: 'ocr', 'translation', or None for the generic wording.
    """
    if status_code == 501:
        if context == 'ocr':
            return translate("Messages", "The selected text recognition tool is not supported.\nPlease select a different tool in Settings.")
        elif context == 'translation':
            return translate("Messages", "The selected translator is not supported.\nPlease select a different tool in Settings.")
        else:
            return translate("Messages", "The selected tool is not supported.\nPlease select a different tool in Settings.")

    messages = {
        500: translate("Messages", "We encountered an unexpected server error.\nPlease try again in a few moments."),
        502: translate("Messages", "The external service provider is having trouble.\nPlease try again later."),
        503: translate("Messages", "The server is currently busy or under maintenance.\nPlease try again shortly."),
        504: translate("Messages", "The server took too long to respond.\nPlease check your connection or try again later."),
    }
    return messages.get(status_code, messages[500])


def content_flagged_text(details: str | None = None, context: str = "Operation") -> str:
    """Localised text for a provider refusing content as flagged.

    `details` is accepted and ignored, as it always has been — the provider's
    own explanation is not shown to the user. Kept in the signature so call
    sites do not have to change.
    """
    if context == "OCR":
        return translate(
            "Messages",
            "Text Recognition blocked: The AI provider flagged this content.\nPlease try a different Text Recognition tool."
        )
    elif context in ("Translator", "Translation"):
        return translate(
            "Messages",
            "Translation blocked: The AI provider flagged this content.\nPlease try a different translator."
        )
    return translate(
        "Messages",
        "Operation blocked: The AI provider flagged this content.\nPlease try a different tool."
    )
