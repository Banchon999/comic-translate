from __future__ import annotations

from typing import TYPE_CHECKING
from modules.inpainting.lama import LaMa
from modules.inpainting.mi_gan import MIGAN
from modules.inpainting.aot import AOT
from modules.inpainting.smart_fill import SmartFill
from modules.inpainting.remote import RemoteInpainter
from modules.inpainting.schema import Config

if TYPE_CHECKING:
    from app.ui.settings.settings_page import SettingsPage

inpaint_map = {
    "LaMa": LaMa,
    "MI-GAN": MIGAN,
    "AOT": AOT,
    "Smart Fill": SmartFill,
    # Runs on a GPU you rent rather than this machine. Unlike the others it
    # downloads nothing and needs an endpoint and key from the Credentials page.
    "Cloud Cleaner": RemoteInpainter,
}

# Engines that reach a paid endpoint instead of a local model. The pipeline has
# to hand these their credentials, and the UI has to warn that pages leave the
# machine, so it is worth naming the set rather than string-matching a label.
REMOTE_INPAINTERS = {"Cloud Cleaner"}


def get_inpainter_backend(inpainter_key: str) -> str:
    inpainter_cls = inpaint_map[inpainter_key]
    return getattr(inpainter_cls, "preferred_backend", "onnx")

def get_config(settings_page: SettingsPage):
    strategy_settings = settings_page.get_hd_strategy_settings()
    if strategy_settings['strategy'] == settings_page.ui.tr("Resize"):
        config = Config(hd_strategy="Resize", hd_strategy_resize_limit = strategy_settings['resize_limit'])
    elif strategy_settings['strategy'] == settings_page.ui.tr("Crop"):
        config = Config(hd_strategy="Crop", hd_strategy_crop_margin = strategy_settings['crop_margin'],
                        hd_strategy_crop_trigger_size = strategy_settings['crop_trigger_size'])
    else:
        config = Config(hd_strategy="Original")

    return config
