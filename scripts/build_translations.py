#!/usr/bin/env python3
"""Regenerate the Qt translation catalogues under resources/translations.

Two things happen here that plain lupdate/lrelease do not do:

1. `--update` re-scans the sources into `ct_<lang>.ts`, keeping existing
   translations and adding any new strings as unfinished.

2. `--compile` writes `compiled/ct_<lang>.qm` from a copy of the .ts that has a
   catch-all `@fallback` context appended. Qt looks a string up under the name of
   the most-derived class, while lupdate files it under whatever it sees at the
   call site, so strings defined in a base class or a mixin (and everything
   written as `self.main.tr(...)`, which lupdate files under the literal context
   "self.main") never match at runtime. `ContextFallbackTranslator` in comic.py
   consults `@fallback` when the real context misses.

The committed .ts stays exactly as lupdate writes it; the synthetic context only
ever exists in the compiled .qm.

Usage:
    python scripts/build_translations.py --update --compile th
    python scripts/build_translations.py --compile          # every language
"""

from __future__ import annotations

import argparse
import copy
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = REPO_ROOT / "resources" / "translations"
COMPILED_DIR = TRANSLATIONS_DIR / "compiled"
FALLBACK_CONTEXT = "@fallback"

# Vendored upstream code carries its own strings and is not part of our UI.
SOURCE_EXCLUDES = ("*/pororo/pororo/*",)
SOURCE_DIRS = ("app", "modules", "pipeline")
SOURCE_FILES = ("comic.py", "controller.py")


def tool(name: str) -> str:
    """Locate a Qt tool, preferring the PySide6 wrappers."""
    for candidate in (f"pyside6-{name}", name):
        found = shutil.which(candidate)
        if found:
            return found
    sys.exit(f"Could not find {name}. Install pyside6-essentials, or put it on PATH.")


def source_arguments() -> list[str]:
    excluded = []
    for directory in SOURCE_DIRS:
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            relative = path.relative_to(REPO_ROOT).as_posix()
            if any(path.match(pattern) for pattern in SOURCE_EXCLUDES):
                continue
            excluded.append(relative)
    return excluded + list(SOURCE_FILES)


def update(lang: str) -> None:
    ts_path = TRANSLATIONS_DIR / f"ct_{lang}.ts"
    subprocess.run(
        [tool("lupdate"), *source_arguments(), "-ts", str(ts_path)],
        cwd=REPO_ROOT,
        check=True,
    )


def add_fallback_context(tree: ET.ElementTree) -> int:
    """Append a context holding every translated string in the catalogue.

    Where the same source text is translated differently per context, the first
    one in document order wins; the per-context entries still take precedence at
    lookup time, so this only decides what an otherwise-missing lookup returns.
    """
    root = tree.getroot()
    for context in root.findall("context"):
        if (context.find("name").text or "") == FALLBACK_CONTEXT:
            root.remove(context)

    fallback = ET.Element("context")
    ET.SubElement(fallback, "name").text = FALLBACK_CONTEXT

    seen: set[str] = set()
    for context in root.findall("context"):
        for message in context.findall("message"):
            source = message.find("source").text
            translation = message.find("translation")
            if source in seen or translation is None or not translation.text:
                continue
            if translation.get("type") in ("unfinished", "vanished", "obsolete"):
                continue
            seen.add(source)
            entry = copy.deepcopy(message)
            for location in entry.findall("location"):
                entry.remove(location)
            fallback.append(entry)

    root.append(fallback)
    return len(seen)


def compile_language(lang: str) -> None:
    ts_path = TRANSLATIONS_DIR / f"ct_{lang}.ts"
    if not ts_path.exists():
        sys.exit(f"No catalogue at {ts_path}")

    tree = ET.parse(ts_path)
    count = add_fallback_context(tree)

    COMPILED_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", suffix=".ts", delete=False) as handle:
        handle.write(b'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n')
        tree.write(handle, encoding="utf-8", xml_declaration=False)
        staged = Path(handle.name)

    try:
        subprocess.run(
            [tool("lrelease"), str(staged), "-qm", str(COMPILED_DIR / f"ct_{lang}.qm")],
            check=True,
        )
    finally:
        staged.unlink(missing_ok=True)

    print(f"ct_{lang}.qm: {count} strings also reachable via {FALLBACK_CONTEXT}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("languages", nargs="*", help="language codes, e.g. th ko fr")
    parser.add_argument("--update", action="store_true", help="re-scan sources into the .ts")
    parser.add_argument("--compile", action="store_true", help="write the .qm")
    args = parser.parse_args()

    languages = args.languages or [
        path.stem.removeprefix("ct_") for path in sorted(TRANSLATIONS_DIR.glob("ct_*.ts"))
    ]
    if not (args.update or args.compile):
        parser.error("pass --update, --compile, or both")

    for lang in languages:
        if args.update:
            update(lang)
        if args.compile:
            compile_language(lang)


if __name__ == "__main__":
    main()
