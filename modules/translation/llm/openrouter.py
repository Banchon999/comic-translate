from typing import Any
from .gpt import GPTTranslation


class OpenRouterTranslation(GPTTranslation):
    """Translation engine using OpenRouter's OpenAI-compatible API.

    OpenRouter (https://openrouter.ai) routes a single API key to many
    hosted models; the user picks the model id (e.g. "openai/gpt-4o",
    "anthropic/claude-sonnet-4.5") in the credentials settings.
    """

    # OpenRouter expects the standard OpenAI "max_tokens" parameter
    MAX_TOKENS_PARAM = "max_tokens"

    def __init__(self):
        super().__init__()
        self.api_base_url = "https://openrouter.ai/api/v1"

    def initialize(self, settings: Any, source_lang: str, target_lang: str, tr_key: str = "OpenRouter", **kwargs) -> None:
        """
        Initialize OpenRouter translation engine.

        Args:
            settings: Settings object with credentials
            source_lang: Source language name
            target_lang: Target language name
            tr_key: Key identifying the translator ("OpenRouter")
        """
        # Call BaseLLMTranslation's initialize, not GPTTranslation's,
        # to avoid the GPT-specific credential loading
        super(GPTTranslation, self).initialize(settings, source_lang, target_lang, **kwargs)

        credentials = settings.get_credentials(settings.ui.tr("OpenRouter"))
        self.api_key = credentials.get('api_key', '')
        self.model = credentials.get('model', '')
        self.timeout = 120  # Routed models can be slower than first-party APIs
