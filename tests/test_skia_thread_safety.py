"""Skia's shared objects must not be shared across threads.

`FontCollection` and `SkUnicode` are not documented as thread-safe, and this
application reaches them from two threads at once as a matter of course: the
Render button runs `manual_wrap` — and so the measurer — on a `QThreadPool`
worker, while the canvas paints text items through the same objects on the GUI
thread.

That is a real race rather than a theoretical one, because skia-python releases
the GIL during its calls: both threads genuinely execute inside Skia at the
same time. A data race there surfaces as a native access violation with no
Python traceback, which is the shape of the crash reported from a frozen
Windows build.

These tests pin the isolation. They cannot prove the absence of a race — a
passing stress run on one platform proves very little — so the load-bearing
assertion is the structural one: no two threads may be handed the same object.
"""

import threading

import pytest

from core import skia_text
from core.skia_render import OutlineLayer, SkiaTextRenderer, TextRenderSpec
from core.text_measure import TextStyle

requires_skia = pytest.mark.skipif(
    skia_text.skia is None, reason="skia-python unavailable"
)

STYLE = TextStyle(font_family="", font_size=18.0, line_spacing=1.2)


@requires_skia
def test_each_thread_gets_its_own_skia_objects():
    """The structural guarantee: nothing is shared, so nothing can race.

    All threads are held at a barrier while the check runs. Letting them exit
    first would compare ids of freed objects, and CPython reuses those
    addresses — a test written that way reports sharing that is not there.
    """
    thread_count = 4
    barrier = threading.Barrier(thread_count + 1)
    held = {}

    def grab(name):
        held[name] = (skia_text._font_collection(), skia_text._unicode())
        barrier.wait()      # everyone alive, holding a reference
        barrier.wait()      # released only once the assertions are done

    threads = [
        threading.Thread(target=grab, args=(f"t{i}",)) for i in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    barrier.wait()

    try:
        held["main"] = (skia_text._font_collection(), skia_text._unicode())

        collections = [id(pair[0]) for pair in held.values()]
        unicodes = [id(pair[1]) for pair in held.values()]

        assert len(set(collections)) == len(collections), (
            "two threads were handed the same FontCollection"
        )
        assert len(set(unicodes)) == len(unicodes), (
            "two threads were handed the same SkUnicode"
        )
    finally:
        barrier.wait()
        for thread in threads:
            thread.join()


@requires_skia
def test_the_same_thread_reuses_its_objects():
    """Per-thread, not per-call: building a FontCollection is not cheap."""
    assert skia_text._font_collection() is skia_text._font_collection()
    assert skia_text._unicode() is skia_text._unicode()


@requires_skia
def test_measuring_and_rendering_concurrently_stays_correct():
    """The app's real shape: a worker measuring while the GUI thread paints.

    Asserts results rather than just survival — a race can corrupt an answer
    without crashing, and a wrong measurement lays text out wrongly on a page.
    """
    expected = SkiaTextMeasurer_measure("the quick brown fox")
    errors = []
    mismatches = []

    def measure_worker():
        try:
            for _ in range(120):
                if SkiaTextMeasurer_measure("the quick brown fox") != expected:
                    mismatches.append("measurement changed under concurrency")
        except Exception as exc:  # pragma: no cover - only on a real failure
            errors.append(exc)

    def render_worker():
        try:
            renderer = SkiaTextRenderer()
            for index in range(60):
                renderer.render(TextRenderSpec(
                    text=f"render {index}\nsecond line",
                    style=STYLE,
                    outlines=(OutlineLayer(width=2.0, color="#ffffff"),),
                    soft_wrapped=False,
                ))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=measure_worker) for _ in range(2)]
    threads += [threading.Thread(target=render_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent Skia use raised: {errors[0]!r}"
    assert not mismatches, mismatches[0]


def SkiaTextMeasurer_measure(text):
    """One measurement, on whichever thread calls it."""
    return skia_text.SkiaTextMeasurer().measure(text, STYLE)
