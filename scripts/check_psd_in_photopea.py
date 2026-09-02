"""Open an exported PSD in Photopea and check what it actually renders.

The PSD export can only be trusted against a reader that did not write it, and
the reader that matters is a real editor rather than a parsing library. This
drives Photopea — a full PSD implementation, and the one people reach for when
they do not own Photoshop — through its live-messaging API in a headless
Chromium, and reports what it makes of each layer.

    python scripts/check_psd_in_photopea.py page.psd
    python scripts/check_psd_in_photopea.py a.psd b.psd --out /tmp/report
    python scripts/check_psd_in_photopea.py page.psd --compare page.png

What it checks, per file:

* the layer tree Photopea parses — names, nesting and order
* each layer's bounds, visibility and kind
* **whether each layer renders anything on its own**, by hiding every other
  layer and exporting that one. This is the check that matters: a PSD whose
  merged image is perfect can still have nothing in any layer, and looking at
  the composite alone cannot tell the two apart.
* **text layers are soloed too, not just pixel layers.** Skipping them is how
  this script once passed a page whose translated text was invisible:
  PhotoshopAPI writes a type layer with bounds (0,0,0,0) and no colour
  channels, Photopea composites type layers from their cached raster, and so
  it drew nothing while still reporting the layer as present and editable.
  A layer Photopea sizes at nothing is now a failure in its own right.
* whether a text layer is still *editable* — that Photopea reads its string
  back out, not merely that a layer with that name exists
* alpha is not uniformly zero anywhere it should not be
* the full composite, exported as PNG and optionally compared with the page
  ComicTranslate rendered

Exit status is 0 only when every file passed every check.

Deliberately not part of `pytest` by default: it needs a browser and reaches a
third-party website, and a CI gate that depends on either is worse than no gate.
`tests/test_psd_in_photopea.py` runs it behind the `photopea` marker.
"""

from __future__ import annotations

import argparse
import base64
import http.server
import json
import socketserver
import sys
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Photopea only enters API mode when the URL carries a config hash. Loaded bare
# it shows its marketing page, never posts "done", and every wait times out.
PHOTOPEA_URL = "https://www.photopea.com#" + urllib.parse.quote(json.dumps({"files": []}))

# Photopea answers every message with the string "done", so one wait works for
# loading a file and for running a script.
DONE = "done"

HOST_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>psd check</title>
<style>html,body{{margin:0;height:100%;background:#1e1e1e}}iframe{{border:0;width:100vw;height:100vh}}</style>
<script>
// Photopea replies with strings (script results, and "done") and with
// ArrayBuffers (exported images). Base64 the binaries here rather than handing
// a multi-megabyte array across the automation bridge one element at a time.
window.__msgs = [];
window.addEventListener("message", function (e) {{
    if (typeof e.data === "string") {{
        window.__msgs.push({{kind: "text", value: e.data}});
        return;
    }}
    var bytes = new Uint8Array(e.data);
    var chunk = 0x8000, parts = [];
    for (var i = 0; i < bytes.length; i += chunk) {{
        parts.push(String.fromCharCode.apply(null, bytes.subarray(i, i + chunk)));
    }}
    window.__msgs.push({{kind: "binary", value: btoa(parts.join("")), length: bytes.length}});
}});
window.__send = function (payload) {{
    document.getElementById("pp").contentWindow.postMessage(payload, "*");
}};
window.__sendBytes = function (b64) {{
    var raw = atob(b64), bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    window.__send(bytes.buffer);
}};
</script>
<iframe id="pp" src="{url}"></iframe>
"""

# Photopea implements Photoshop's scripting object model, so the layer walk is
# ordinary ExtendScript -- but it is Photopea's own interpreter, and three of its
# behaviours will quietly ruin a script written against real JavaScript:
#
#   1. `return` inside a `try` block yields **undefined**. Every defensive
#      `try { return x } catch { return null }` helper therefore returns nothing
#      at all. Assign inside the try and return afterwards.
#   2. `x === undefined` is **false** even for an undefined variable.
#   3. Reading any property off a length value (`.n`, `.value`) **kills the
#      script outright** -- no reply, no "done", so a harness waiting for one
#      just hangs. `&&` does not short-circuit either, so guarding the access
#      with a `typeof` test still triggers it.
#
# All three fail silently and produce a report saying every layer is empty --
# indistinguishable from the bug this script exists to find.
READ_TREE = """
// Lengths behave like numbers here: `typeof` reports "number" and arithmetic
// works, but they stringify as "[object Object]" and Number() gives NaN.
// Adding zero is the only safe way to read one.
function num(v) {
    var r = null;
    try { r = v + 0; } catch (e) { r = null; }
    return r;
}
function walk(layers) {
    var out = [];
    for (var i = 0; i < layers.length; i++) {
        var l = layers[i];
        var o = {name: l.name, typename: l.typename, visible: null, bounds: null,
                 kind: null, is_text: false, text: null, opacity: null};
        try { o.visible = l.visible; } catch (e) { o.visible = "ERR " + e; }
        try { o.opacity = num(l.opacity); } catch (e) {}
        try { o.bounds = [num(l.bounds[0]), num(l.bounds[1]), num(l.bounds[2]), num(l.bounds[3])]; }
        catch (e) { o.bounds = "ERR " + e; }
        try { o.kind = num(l.kind); } catch (e) {}
        try {
            o.is_text = (l.kind == LayerKind.TEXT);
            if (o.is_text) o.text = l.textItem.contents;
        } catch (e) { o.text = "ERR " + e; }
        if (l.typename == "LayerSet") { o.children = walk(l.layers); }
        out.push(o);
    }
    return out;
}
var d = app.activeDocument;
app.echoToOE(JSON.stringify({width: num(d.width), height: num(d.height), layers: walk(d.layers)}));
"""


# Hide everything, show one layer by its path through the tree, export, restore.
# Photopea has no "render this layer" call, so soloing is how a layer's own
# pixels are made visible to the export.
SOLO_AND_EXPORT = """
function each(layers, fn) {
    for (var i = 0; i < layers.length; i++) {
        fn(layers[i]);
        if (layers[i].typename == "LayerSet") each(layers[i].layers, fn);
    }
}
var d = app.activeDocument;
var saved = [];
each(d.layers, function (l) { saved.push([l, l.visible]); });
each(d.layers, function (l) { try { l.visible = false; } catch (e) {} });
var target = d;
var path = %s;
for (var i = 0; i < path.length; i++) target = target.layers[path[i]];
var node = target;
while (node && node.typename) {
    try { node.visible = true; } catch (e) {}
    node = node.parent && node.parent.typename == "LayerSet" ? node.parent : null;
}
d.saveToOE("png");
"""

RESTORE = """
function each(layers, fn) {
    for (var i = 0; i < layers.length; i++) {
        fn(layers[i]);
        if (layers[i].typename == "LayerSet") each(layers[i].layers, fn);
    }
}
each(app.activeDocument.layers, function (l) { try { l.visible = true; } catch (e) {} });
app.echoToOE("restored");
"""


@dataclass
class Finding:
    ok: bool
    label: str
    detail: str = ""

    def line(self) -> str:
        return f"  [{'ok' if self.ok else 'FAIL'}] {self.label}{(' — ' + self.detail) if self.detail else ''}"


@dataclass
class Report:
    path: Path
    findings: list[Finding] = field(default_factory=list)
    tree: dict | None = None

    def add(self, ok: bool, label: str, detail: str = "") -> None:
        self.findings.append(Finding(ok, label, detail))

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings)


class _Server(threading.Thread):
    """Serves the host page. Photopea has to be reached over http, not file://."""

    def __init__(self, html: bytes) -> None:
        super().__init__(daemon=True)
        payload = html

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - the stdlib spells it this way
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self._srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self.port = self._srv.server_address[1]

    def run(self) -> None:
        self._srv.serve_forever()

    def stop(self) -> None:
        self._srv.shutdown()


class Photopea:
    """One Photopea document, driven over the live-messaging API."""

    def __init__(self, page, timeout: int) -> None:
        self.page = page
        self.timeout = timeout

    def _wait_done(self, since: int) -> list[dict]:
        self.page.wait_for_function(
            "n => window.__msgs.slice(n).some(m => m.kind === 'text' && m.value === 'done')",
            arg=since,
            timeout=self.timeout,
        )
        msgs = self.page.evaluate("n => window.__msgs.slice(n)", since)
        return [m for m in msgs if not (m["kind"] == "text" and m["value"] == DONE)]

    def _count(self) -> int:
        return self.page.evaluate("window.__msgs.length")

    def open_bytes(self, data: bytes) -> None:
        since = self._count()
        self.page.evaluate("b64 => window.__sendBytes(b64)", base64.b64encode(data).decode())
        self._wait_done(since)

    def script(self, source: str) -> list[dict]:
        since = self._count()
        self.page.evaluate("s => window.__send(s)", source)
        return self._wait_done(since)

    def echo(self, source: str):
        replies = [m for m in self.script(source) if m["kind"] == "text"]
        if not replies:
            raise RuntimeError("Photopea returned nothing for the script")
        return json.loads(replies[-1]["value"])

    def export_png(self, source: str) -> bytes:
        binaries = [m for m in self.script(source) if m["kind"] == "binary"]
        if not binaries:
            raise RuntimeError("Photopea returned no image")
        return base64.b64decode(binaries[-1]["value"])


def _flatten(nodes: list[dict], prefix: tuple[int, ...] = ()) -> list[tuple[tuple[int, ...], dict]]:
    out: list[tuple[tuple[int, ...], dict]] = []
    for index, node in enumerate(nodes):
        path = prefix + (index,)
        out.append((path, node))
        out.extend(_flatten(node.get("children") or [], path))
    return out


def _png_stats(data: bytes) -> dict:
    import io

    import numpy as np
    from PIL import Image

    image = np.array(Image.open(io.BytesIO(data)).convert("RGBA"))
    alpha = image[:, :, 3]
    return {
        "size": (int(image.shape[1]), int(image.shape[0])),
        "opaque_pixels": int((alpha > 0).sum()),
        "alpha_max": int(alpha.max()),
        "rgb_min": int(image[:, :, :3].min()),
        "rgb_max": int(image[:, :, :3].max()),
        "flat": bool(image[:, :, :3].min() == image[:, :, :3].max()),
    }


def _is_leaf(node: dict) -> bool:
    """A layer rather than a group — something that should draw something."""
    return node.get("typename") != "LayerSet"


def _has_extent(node: dict) -> bool:
    """Whether Photopea reports a non-empty box for this layer."""
    bounds = node.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 4:
        return False
    left, top, right, bottom = bounds
    return (right - left) > 0 and (bottom - top) > 0


def check_file(pp: Photopea, path: Path, out_dir: Path, compare: Path | None) -> Report:
    report = Report(path)
    pp.open_bytes(path.read_bytes())

    tree = pp.echo(READ_TREE)
    report.tree = tree
    nodes = _flatten(tree["layers"])
    report.add(bool(nodes), "Photopea parsed a layer tree", f"{len(nodes)} nodes")

    for _, node in nodes:
        bounds = node.get("bounds")
        if isinstance(bounds, str):
            report.add(False, f"bounds readable: {node['name']!r}", bounds)
        if node.get("visible") is False:
            report.add(False, f"layer arrives visible: {node['name']!r}", "Photopea reports it hidden")

    text_layers = [n for _, n in nodes if n.get("is_text")]
    for node in text_layers:
        contents = node.get("text")
        report.add(
            isinstance(contents, str) and bool(contents) and not contents.startswith("ERR"),
            f"text layer is editable: {node['name']!r}",
            repr(contents)[:80],
        )

    # Every leaf layer is soloed, text layers included. An earlier version
    # skipped any layer Photopea reported with an empty box, which is exactly
    # the shape of the bug this script exists to find: PhotoshopAPI writes type
    # layers with bounds (0,0,0,0) and no colour channels, so Photopea draws
    # nothing for them — and the check that would have caught it was the one
    # the empty box excluded. It reported PASS on a page with invisible text.
    leaves = [(p, n) for p, n in nodes if _is_leaf(n)]
    report.add(bool(leaves), "there is at least one layer to render", f"{len(leaves)} found")

    for path_indices, node in leaves:
        if not _has_extent(node):
            # Soloing a layer Photopea sizes at nothing can only export an
            # empty page, so say why rather than exporting a blank and
            # blaming the pixels.
            report.add(
                False,
                f"layer has a non-empty box: {node['name']!r}",
                f"Photopea reports bounds {node.get('bounds')}",
            )
            continue
        png = pp.export_png(SOLO_AND_EXPORT % json.dumps(list(path_indices)))
        pp.script(RESTORE)
        stats = _png_stats(png)
        name = "".join(c if c.isalnum() else "_" for c in node["name"])
        (out_dir / f"{path.stem}.layer-{name}.png").write_bytes(png)
        report.add(
            stats["opaque_pixels"] > 0,
            f"layer renders its own pixels: {node['name']!r}",
            f"{stats['opaque_pixels']} opaque px, alpha_max={stats['alpha_max']}",
        )

    composite = pp.export_png('app.activeDocument.saveToOE("png");')
    (out_dir / f"{path.stem}.composite.png").write_bytes(composite)
    stats = _png_stats(composite)
    report.add(stats["opaque_pixels"] > 0, "the composite is not blank", f"{stats['opaque_pixels']} opaque px")
    report.add(not stats["flat"], "the composite is not one flat colour")
    report.add(
        stats["size"] == (int(tree["width"]), int(tree["height"])),
        "the composite is the document size",
        f"{stats['size']} vs {(tree['width'], tree['height'])}",
    )

    if compare is not None:
        import io

        import numpy as np
        from PIL import Image

        theirs = np.array(Image.open(io.BytesIO(composite)).convert("RGB"), dtype=np.int16)
        ours = np.array(Image.open(compare).convert("RGB"), dtype=np.int16)
        if theirs.shape != ours.shape:
            report.add(False, "composite matches ComicTranslate's render", f"{theirs.shape} vs {ours.shape}")
        else:
            mean_error = float(np.abs(theirs - ours).mean())
            report.add(mean_error < 8.0, "composite matches ComicTranslate's render", f"mean abs error {mean_error:.2f}")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("psd", nargs="+", type=Path, help="PSD files to open in Photopea")
    parser.add_argument("--out", type=Path, default=Path("photopea-report"), help="where to write PNGs and screenshots")
    parser.add_argument("--compare", type=Path, help="a PNG of the same page, to compare the composite against")
    parser.add_argument("--timeout", type=int, default=120_000, help="per-step timeout in ms")
    parser.add_argument("--json", type=Path, help="also write the findings as JSON")
    args = parser.parse_args(argv)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright is not installed. It is in requirements-dev.txt; install with\n"
            "    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 pip install playwright\n"
            "and use the browser already on the machine — do not run `playwright install`.",
            file=sys.stderr,
        )
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    server = _Server(HOST_PAGE.format(url=PHOTOPEA_URL).encode())
    server.start()

    reports: list[Report] = []
    with sync_playwright() as play:
        browser = play.chromium.launch(**_launch_options())
        try:
            for psd in args.psd:
                page = browser.new_page(viewport={"width": 1400, "height": 900})
                page.goto(f"http://127.0.0.1:{server.port}/", wait_until="load")
                page.wait_for_function(
                    "() => window.__msgs.some(m => m.kind === 'text' && m.value === 'done')",
                    timeout=args.timeout,
                )
                pp = Photopea(page, args.timeout)
                try:
                    report = check_file(pp, psd, args.out, args.compare)
                except Exception as exc:  # a failure to drive Photopea is a result, not a crash
                    report = Report(psd)
                    report.add(False, "Photopea could be driven at all", f"{type(exc).__name__}: {exc}")
                page.screenshot(path=str(args.out / f"{psd.stem}.photopea.png"))
                reports.append(report)
                page.close()
        finally:
            browser.close()
    server.stop()

    for report in reports:
        print(f"\n{report.path}: {'PASS' if report.ok else 'FAIL'}")
        for finding in report.findings:
            print(finding.line())

    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        "path": str(r.path),
                        "ok": r.ok,
                        "tree": r.tree,
                        "findings": [{"ok": f.ok, "label": f.label, "detail": f.detail} for f in r.findings],
                    }
                    for r in reports
                ],
                indent=2,
            )
        )

    print(f"\nartefacts in {args.out}")
    return 0 if all(r.ok for r in reports) else 1


def _launch_options() -> dict:
    """Chromium arguments this environment needs, with why each is here.

    Kept in one place because getting any of them wrong looks like Photopea
    being broken rather than the browser never reaching it.
    """
    import os

    options: dict = {"args": ["--no-sandbox"]}

    executable = "/opt/pw-browsers/chromium"
    if os.path.exists(executable):
        # The image ships a Chromium that may not match the Playwright version's
        # expected build number; use the one that is actually installed.
        options["executable_path"] = executable

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        options["proxy"] = {"server": proxy, "bypass": "127.0.0.1,localhost"}
        # The sandbox's TLS-terminating proxy resets Chromium's TLS 1.3
        # handshake; every request then fails as ERR_CONNECTION_RESET, which
        # reads like the site being down. Capping the version fixes it and
        # leaves certificate verification fully on.
        options["args"].append("--ssl-version-max=tls1.2")
        options["args"].append("--proxy-bypass-list=127.0.0.1;localhost")

    return options


if __name__ == "__main__":
    raise SystemExit(main())
