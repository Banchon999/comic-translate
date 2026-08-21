from typing import Any

from .gpt_ocr import GPTOCR


class OpenRouterOCR(GPTOCR):
    """OCR through OpenRouter, billed to OpenRouter credit.

    OpenRouter (https://openrouter.ai) fronts many hosted models behind one
    OpenAI-compatible endpoint and one balance, so the same vision model can be
    reached without holding an account with the provider that runs it — for
    instance `google/gemini-2.5-flash-lite` without a Google API key.

    Only three things differ from talking to OpenAI directly, which is why this
    subclasses GPTOCR rather than repeating the request: the endpoint, the
    max-tokens parameter (OpenRouter kept the original name), and where the
    model id comes from. The model is whatever the user typed or picked in
    Settings > Credentials > OpenRouter — a routing id, not one of this app's
    own model names, so it is used verbatim rather than through MODEL_MAP.
    """

    # OpenRouter expects the standard OpenAI "max_tokens" parameter.
    MAX_TOKENS_PARAM = "max_tokens"

    #: Sent for attribution on OpenRouter's public model rankings. Optional to
    #: them, and no part of authentication.
    REFERER = "https://github.com/ogkalu2/comic-translate"
    TITLE = "Comic Translate"

    def __init__(self):
        super().__init__()
        self.api_base_url = "https://openrouter.ai/api/v1/chat/completions"
        # A routed model can be slower to first token than a first-party API.
        self.timeout = 60

    def initialize(self, settings: Any, model: str = '', expansion_percentage: int = 0) -> None:
        """Read the OpenRouter key and OCR model id out of the credentials page.

        OCR and translation take separate model fields, because they want
        different things from a model: OCR runs a cheap vision model once per
        text block, translation a stronger one once per page, and paying for
        the stronger one per block is the expensive way to read a page. The OCR
        field falls back to the translation model when left empty, so a single
        model still works with nothing to configure.

        `model` is accepted so the factory can call every OCR engine the same
        way, but it is ignored: which model runs is the user's choice in the
        credentials page, not a name from the OCR dropdown.
        """
        credentials = settings.get_credentials(settings.ui.tr("OpenRouter"))
        self.api_key = credentials.get('api_key', '') or ''
        # Stripped before the choice, not after: a field holding only spaces
        # is truthy, so picking first and stripping second would take the
        # blank one and never reach the fallback.
        ocr_model = (credentials.get('ocr_model') or '').strip()
        self.model = ocr_model or (credentials.get('model') or '').strip()
        self.expansion_percentage = expansion_percentage

    def request_headers(self) -> dict:
        headers = super().request_headers()
        headers["HTTP-Referer"] = self.REFERER
        headers["X-Title"] = self.TITLE
        return headers

    def _get_gpt_ocr(self, base64_image: str) -> str:
        if not self.model:
            raise ValueError(
                "OpenRouter needs a model id. Set one in "
                "Settings > Credentials > OpenRouter (for example "
                "'google/gemini-2.5-flash-lite')."
            )
        return super()._get_gpt_ocr(base64_image)
