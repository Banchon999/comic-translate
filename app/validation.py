"""Pre-flight checks that put a dialog in front of the user.

These moved out of `modules/utils/pipeline_config.py`, which the pipeline
imports and which therefore has to stand up without Qt. Everything here calls
`Messages`, so it belongs on this side of the line: the checks are a UI
gesture — refuse to start, say why — not pipeline logic.

Callers are `controller.py` (before a batch run) and
`app/controllers/manual_workflow.py` (before a manual step).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ui.messages import Messages
from core.i18n import translate

if TYPE_CHECKING:
    from controller import ComicTranslate


def validate_ocr(main: ComicTranslate):
    """Ensure either API credentials are set or the user is authenticated."""
    settings_page = main.settings_page
    settings = settings_page.get_all_settings()
    ocr_tool = settings['tools']['ocr']

    if not ocr_tool:
        Messages.show_missing_tool_error(main, translate("Messages", "Text Recognition model"))
        return False

    if not settings_page.is_logged_in():
        Messages.show_not_logged_in_error(main)
        return False

    return True


def validate_translator(main: ComicTranslate, target_lang: str):
    """Ensure either API credentials are set or the user is authenticated, plus check compatibility."""
    settings_page = main.settings_page
    tr = settings_page.ui.tr
    settings = settings_page.get_all_settings()
    credentials = settings.get('credentials', {})
    translator_tool = settings['tools']['translator']

    if not translator_tool:
        Messages.show_missing_tool_error(main, translate("Messages", "Translator"))
        return False

    if not settings_page.is_logged_in():
        Messages.show_not_logged_in_error(main)
        return False

    # Credential checks
    if "Custom" in translator_tool:
        # Custom requires api_key, api_url, and model to be configured LOCALLY
        service = tr('Custom')
        creds = credentials.get(service, {})
        # Check if all required fields are present and non-empty
        if not all([creds.get('api_key'), creds.get('api_url'), creds.get('model')]):
            Messages.show_custom_not_configured_error(main)
            return False
        return True

    return True


def font_selected(main: ComicTranslate):
    if not main.render_settings().font_family:
        Messages.select_font_error(main)
        return False
    return True


def validate_settings(main: ComicTranslate, target_lang: str):
    if not validate_ocr(main):
        return False
    if not validate_translator(main, target_lang):
        return False
    if not font_selected(main):
        return False

    return True
