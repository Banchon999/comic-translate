"""Text segmentation runs on OpenVINO's CPU plugin, and falls back safely.

ONNX Runtime's CPU ConvTranspose took 37-40 s per call on the real
comic-text-detector model; OpenVINO runs it in ~0.5 s with an identical mask.
These tests use a tiny model with the same input/output contract (an
``images`` input of 1x3x1024x1024, a ``seg`` output, a ConvTranspose inside),
so they need no model download.
"""

import re
from pathlib import Path

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
pytest.importorskip("openvino")

from onnx import TensorProto, helper, numpy_helper  # noqa: E402

from modules.detection import text_seg_onnx  # noqa: E402
from modules.detection.text_seg_onnx import ComicTextSegmenter, openvino_self_test  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.fixture
def tiny_model(tmp_path, monkeypatch):
    rng = np.random.default_rng(0)
    w1 = numpy_helper.from_array((rng.standard_normal((8, 3, 3, 3)) * 0.3).astype(np.float32), "w1")
    w2 = numpy_helper.from_array((rng.standard_normal((8, 1, 4, 4)) * 0.3).astype(np.float32), "w2")
    nodes = [
        helper.make_node("Conv", ["images", "w1"], ["c"], strides=[2, 2], pads=[1, 1, 1, 1]),
        helper.make_node("Relu", ["c"], ["r"]),
        helper.make_node("ConvTranspose", ["r", "w2"], ["t"], strides=[2, 2], pads=[1, 1, 1, 1]),
        helper.make_node("Sigmoid", ["t"], ["seg"]),
    ]
    graph = helper.make_graph(
        nodes, "tiny_seg",
        [helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 1024, 1024])],
        [helper.make_tensor_value_info("seg", TensorProto.FLOAT, [1, 1, 1024, 1024])],
        initializer=[w1, w2],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    path = tmp_path / "tiny-seg.onnx"
    onnx.save(model, str(path))
    monkeypatch.setattr(text_seg_onnx.ModelDownloader, "get_file_path", staticmethod(lambda *a, **k: str(path)))
    monkeypatch.delenv("CT_TEXT_SEG_BACKEND", raising=False)
    return path


def _page():
    rng = np.random.default_rng(1)
    page = np.full((1800, 700, 3), 255, np.uint8)
    page[200:400, 100:600] = rng.integers(0, 255, (200, 500, 3), dtype=np.uint8)
    return page


def test_cpu_segmentation_uses_openvino(tiny_model):
    seg = ComicTextSegmenter()
    seg.initialize("cpu")
    assert seg.backend == "openvino"


def test_openvino_and_onnxruntime_give_the_same_mask(tiny_model, monkeypatch):
    page = _page()
    fast = ComicTextSegmenter(); fast.initialize("cpu")
    monkeypatch.setenv("CT_TEXT_SEG_BACKEND", "onnxruntime")
    slow = ComicTextSegmenter(); slow.initialize("cpu")
    assert (fast.backend, slow.backend) == ("openvino", "onnxruntime")
    a, b = fast.segment(page), slow.segment(page)
    assert a.shape == b.shape == page.shape[:2]
    # 0-255 probabilities; float rounding may move a value by one step.
    assert int(np.abs(a.astype(int) - b.astype(int)).max()) <= 1


def test_the_environment_variable_forces_onnxruntime(tiny_model, monkeypatch):
    monkeypatch.setenv("CT_TEXT_SEG_BACKEND", "onnxruntime")
    seg = ComicTextSegmenter(); seg.initialize("cpu")
    assert seg.backend == "onnxruntime"


def test_an_enabled_gpu_stays_on_onnxruntime(tiny_model):
    # ConvTranspose is fast on a CUDA GPU; OpenVINO's CPU plugin would be a step down.
    seg = ComicTextSegmenter(); seg.initialize("cuda")
    assert seg.backend == "onnxruntime"


def test_missing_openvino_falls_back(tiny_model, monkeypatch):
    def missing(*_a, **_k):
        raise ImportError("no openvino")
    monkeypatch.setattr(text_seg_onnx._OpenVINORunner, "__init__", missing)
    seg = ComicTextSegmenter(); seg.initialize("cpu")
    assert seg.backend == "onnxruntime"
    assert seg.segment(_page()).shape == (1800, 700)


def test_a_model_openvino_cannot_load_falls_back(tiny_model, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("the graph is not acyclic")
    monkeypatch.setattr(text_seg_onnx._OpenVINORunner, "__init__", broken)
    seg = ComicTextSegmenter(); seg.initialize("cpu")
    assert seg.backend == "onnxruntime"


def test_an_inference_failure_switches_to_onnxruntime_and_still_answers(tiny_model, monkeypatch):
    seg = ComicTextSegmenter(); seg.initialize("cpu")
    assert seg.backend == "openvino"

    def boom(_self, _tensor):
        raise RuntimeError("device lost")
    monkeypatch.setattr(text_seg_onnx._OpenVINORunner, "run", boom)
    assert seg.segment(_page()).shape == (1800, 700)
    assert seg.backend == "onnxruntime"


def test_self_test_passes_here():
    assert openvino_self_test() is None


@pytest.mark.parametrize("name", [
    "build-windows.yml", "build-windows-full.yml", "main full.yml", "build-linux.yml",
])
def test_every_build_bundles_openvino_and_proves_it(name):
    """PyInstaller can ship openvino without the plugins it loads by path;
    the frozen app must run the self-test, not just import the package."""
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert re.search(r"--collect-all\s+openvino", text), name
    assert "--openvino-self-test" in text, name


def test_the_macos_build_leaves_openvino_out_on_purpose():
    """Every dylib in openvino's macOS wheel has a __LINKEDIT that
    install_name_tool refuses to edit, which aborts PyInstaller; the DMG falls
    back to ONNX Runtime rather than failing to build."""
    text = (WORKFLOWS / "build-macos-dmg.yml").read_text(encoding="utf-8")
    assert "pip uninstall -y openvino" in text
    assert "--collect-all openvino" not in text
