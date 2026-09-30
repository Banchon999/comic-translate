"""Write the Toon Studio line icons into resources/static/.

One table, one look: 24-unit grid, 1.75 stroke, round caps and joins. Every
icon strokes (or fills) with ``#555555`` because that is the colour
``MIcon`` swaps for the theme's icon, hover and accent colours when it
renders an SVG (app/ui/dayu_widgets/qt/__init__.py); an icon drawn in any
other colour stops following the theme. File names are the ones the code
already asks for, so replacing an icon needs no code change.

    python scripts/build_icons.py            # rewrite every icon in the table
    python scripts/build_icons.py --check    # exit 1 if a file differs from the table
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "resources" / "static"
THEME_COLOR = "#555555"

# name -> path data (stroked). Several shapes join into one path with M.
STROKED = {
    # Canvas tools
    "pan_tool": "M8 13V6.5a1.5 1.5 0 0 1 3 0V12M11 11V5a1.5 1.5 0 0 1 3 0v6M14 11.5V7a1.5 1.5 0 0 1 3 0v6.5c0 4-2.6 7-6.2 7-2.4 0-4-1.1-5.4-3.2L3.6 14a1.5 1.5 0 0 1 2.5-1.7L8 14.5",
    "select": "M4 7V4h3M10 4h4M17 4h3v3M20 10v2M4 10v3M4 16v4h3M10 20h2M14 14l7 2.6-3 1.2-1.2 3z",
    "type-text": "M5 7V5h14v2M12 5v14M9 19h6",
    "brush-fill": "M15 4l5 5-8.5 8.5-5-5zM6.5 12.5C4 12.5 3 14.5 3 16.5c0 1.6-.6 2.6-1 3.2 3.4 0 7.3-.9 7.3-4.4",
    "eraser_fill": "M15.5 4.5l4.9 4.9-9.4 9.4H6.5L3.6 15.9zM9.5 10.5l5 5M6.5 18.8H20",
    "wand": "M4 20l10.5-10.5M13 8l3 3M17 3v2.5M20.5 6.5L19 8M21 11h-2.5M11.5 4.5L13 6",
    "paint-brush": "M14.5 4.5l5 5L11 18c-1 1-2.5 1.2-3.8.6L5 20.5l.9-2.2C5.3 17 5.5 15.5 6.5 14.5zM12.5 6.5l5 5",
    "restore-brush": "M4 12a8 8 0 1 0 2.4-5.7M4 4.5v4h4M9.5 12.5l2 2 3.5-4",
    "eyedropper": "M15 4.5a2.5 2.5 0 0 1 3.5 3.5L16 10.5 13.5 8zM13.5 8l-8 8V19h3l8-8M16 10.5L13.5 8",
    "fill-bucket": "M5 11.5L11.5 5l7 7-6.5 6.5a1.5 1.5 0 0 1-2 0L5 13.5a1.4 1.4 0 0 1 0-2zM5 11.5h13.5M19.5 15.5s1.5 2 1.5 3a1.5 1.5 0 0 1-3 0c0-1 1.5-3 1.5-3z",
    "ai-brush": "M13.5 5.5l5 5L10 19c-1 1-2.5 1.2-3.8.6L4 20.5l.9-2.2C4.3 17 4.5 15.5 5.5 14.5zM18 3v2.5M20.5 4.2H15.5M21 8.5v2M22 9.5h-2",
    "pen-pressure": "M14.5 4.5l5 5L9 20H4v-5zM12.5 6.5l5 5M3.5 3.5c1.5.5 2.5 1.5 3 3M7 3c.8.4 1.3 1 1.6 1.8",
    "clean-balloons": "M12 4.5c4.4 0 8 2.7 8 6s-3.6 6-8 6c-.9 0-1.8-.1-2.6-.3L5.5 18l1.1-3.1C5 13.8 4 12.2 4 10.5c0-3.3 3.6-6 8-6zM19 2v3M20.5 3.5h-3M9 10.5h6",
    "clone-stamp": "M9.5 10V7.5a2.5 2.5 0 1 1 5 0V10M8.5 10h7l1 3.5h-9zM4.5 13.5h15V17h-15zM6.5 20h11",
    "heal-brush": "M8.3 3.7l12 12a2.8 2.8 0 0 1 0 4l-.6.6a2.8 2.8 0 0 1-4 0l-12-12a2.8 2.8 0 0 1 0-4l.6-.6a2.8 2.8 0 0 1 4 0zM9.3 13.3l4-4M10.7 10.7h.01M13.3 13.3h.01",
    "select-rect": "M4 7V5a1 1 0 0 1 1-1h2M10 4h4M17 4h2a1 1 0 0 1 1 1v2M20 10v4M20 17v2a1 1 0 0 1-1 1h-2M14 20h-4M7 20H5a1 1 0 0 1-1-1v-2M4 14v-4",
    "select-balloon": "M12 4c4.7 0 8.5 2.9 8.5 6.5S16.7 17 12 17c-.9 0-1.8-.1-2.6-.3L5 19.5l1.2-3.6C4.6 14.7 3.5 12.7 3.5 10.5 3.5 6.9 7.3 4 12 4zM8.5 10.5h7",
    "lasso": "M12 4.5c4.7 0 8.5 2.1 8.5 4.7s-3.8 4.8-8.5 4.8-8.5-2.1-8.5-4.8 3.8-4.7 8.5-4.7zM7.5 13.3c-1.7 1-2.2 2.7-1 3.8 1.3 1.2 3.4.4 3.4 2.9",
    "trash_line": "M4 7h16M9.5 7V4.5h5V7M6 7l1 13h10l1-13M10 11v5.5M14 11v5.5",
    "clear-outlined": "M19.5 4.5l-7 7M9.5 10l4.5 4.5-2 5c-3-.6-6.2-3-8-6.5zM7.8 15.8l2.6-2.6",
    "gridicons--create": "M4 7V4h3M17 4h3v3M20 17v3h-3M7 20H4v-3M12 8.5v7M8.5 12h7",
    "add_line": "M12 5v14M5 12h14",
    "minus_line": "M5 12h14",
    "close_line": "M6 6l12 12M18 6L6 18",
    # History, panels, views
    "undo": "M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
    "redo": "M15 14l5-5-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13",
    "layers": "M12 3l9 5-9 5-9-5zM3 12.5l9 5 9-5M3 17l9 5 9-5",
    "folder-open": "M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2h7.5A1.5 1.5 0 0 1 20 9.5V11M3 7.5V18l2.6-6.1A1.5 1.5 0 0 1 7 11h13.3a1 1 0 0 1 .9 1.4L18.6 18H3",
    "webtoon-toggle": "M8 2.5h8v19H8zM8 8.5h8M8 14.5h8",
    "eye": "M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12zM12 9.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
    "eye-off": "M3 3l18 18M10.6 5.6c.5-.1.9-.1 1.4-.1 6 0 9.5 6.5 9.5 6.5a16 16 0 0 1-2.9 3.7M6.6 6.6C4 8.3 2.5 12 2.5 12s3.5 6.5 9.5 6.5c1.9 0 3.5-.6 4.9-1.5M9.9 9.9a3 3 0 0 0 4.2 4.2",
    "lock": "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
    "lock-open": "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 6.8-1.2",
    "search_line": "M11 5a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM15.5 15.5L20 20",
    "replace": "M4 7h11l-3-3M20 17H9l3 3",
    "replace-all": "M4 6h11l-3-3M20 14H9l3 3M4 20h16",
    "more": "M6 12h.01M12 12h.01M18 12h.01",
    # Text formatting
    "bold": "M7 5h6a3.5 3.5 0 0 1 0 7H7zM7 12h7a3.5 3.5 0 0 1 0 7H7z",
    "italic": "M11 5h7M6 19h7M14 5l-4 14",
    "underline": "M7 4v7a5 5 0 0 0 10 0V4M5 20h14",
    "tabler--align-left": "M4 6h16M4 10h10M4 14h16M4 18h10",
    "tabler--align-center": "M4 6h16M7 10h10M4 14h16M7 18h10",
    "tabler--align-right": "M4 6h16M10 10h10M4 14h16M10 18h10",
    # Navigation rail
    "startup_line": "M4 11l8-6.5 8 6.5M6 9.5V20h12V9.5M10 20v-5h4v5",
    "home_line": "M5 4h9l5 5v11H5zM14 4v5h5M9 16.5l5-5 2 2-5 5H9z",
    "settings": "M18.69 9.95 L21.03 10.24 L21.03 13.76 L18.69 14.05 L18.18 15.29 L19.63 17.14 L17.14 19.63 L15.29 18.18 L14.05 18.69 L13.76 21.03 L10.24 21.03 L9.95 18.69 L8.71 18.18 L6.86 19.63 L4.37 17.14 L5.82 15.29 L5.31 14.05 L2.97 13.76 L2.97 10.24 L5.31 9.95 L5.82 8.71 L4.37 6.86 L6.86 4.37 L8.71 5.82 L9.95 5.31 L10.24 2.97 L13.76 2.97 L14.05 5.31 L15.29 5.82 L17.14 4.37 L19.63 6.86 L18.18 8.71zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "list_view": "M5 4.5h10.5A2.5 2.5 0 0 1 18 7v12.5H7.5A2.5 2.5 0 0 1 5 17zM5 17a2.5 2.5 0 0 1 2.5-2.5H18M9 8.5h5",
    "file": "M6 3h8l4 4v14H6zM14 3v4h4M12 11v6M9 14h6",
    "file-plus": "M4 6h11v11H4zM4 14l3-3 3 3 2-2 3 3M18.5 8v6M15.5 11h6",
    "save": "M4 5h11v11H4zM4 13l3-3 3 3 2-2 3 3M12 20l3-3 3 3M15 17v5",
    "fluent--save-16-regular": "M5 4h11l3 3v13H5zM8 4v5h7V4M8 20v-6h8v6",
    "fluent--save-as-24-regular": "M5 4h11l3 3v4M5 4v16h6M8 4v5h7V4M15.5 20.5l5-5-2-2-5 5v2z",
    "tabler--file-export": "M12 15V4M7.5 8.5L12 4l4.5 4.5M5 14v5h14v-5",
    "attachment_line": "M12 16V5M7.5 9.5L12 5l4.5 4.5M4 15v4h16v-4",
    # Open menu
    "ion--image-outline": "M4 5h16v14H4zM4 16l4.5-4.5 3 3 2.5-2.5L20 18M15.5 8.5h.01",
    "mingcute--document-line": "M6 3h8l4 4v14H6zM14 3v4h4M9 12h6M9 16h6",
    "flowbite--file-zip-outline": "M6 3h12v18H6zM11 3v2M13 5v2M11 7v2M13 9v2M11 11v2M10.5 15h3v3h-3z",
    "mdi--comic-thought-bubble-outline": "M4 5h16v11H10l-5 4v-4H4zM8 9h8M8 12h5",
    # Settings sections
    "nav-personalize": "M12 3.5a8.5 8.5 0 1 0 0 17c1.2 0 1.8-.8 1.8-1.7 0-1.3-1.1-1.6-1.1-2.7 0-.9.7-1.6 1.6-1.6h2.2A4 4 0 0 0 20.5 10.5 8.5 8.5 0 0 0 12 3.5zM7.5 11.5h.01M9.5 7.5h.01M14.5 7.5h.01",
    "nav-account": "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20a7.5 7.5 0 0 1 15 0",
    "nav-tools": "M14.7 6.3a4 4 0 0 0-5.4 5.4L4 17l3 3 5.3-5.3a4 4 0 0 0 5.4-5.4l-2.5 2.5-2.1-.4-.4-2.1z",
    "nav-llm": "M12 3.5l1.7 4 4 1.7-4 1.7-1.7 4-1.7-4-4-1.7 4-1.7zM18 14.5l.8 1.9 1.9.8-1.9.8-.8 1.9-.8-1.9-1.9-.8 1.9-.8z",
    "nav-text": "M5 7V5h14v2M12 5v14M9 19h6",
    "nav-project": "M3 6.5A1.5 1.5 0 0 1 4.5 5H9l2 2h8.5A1.5 1.5 0 0 1 21 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 17.5z",
    "nav-export": "M12 15V4M7.5 8.5L12 4l4.5 4.5M5 14v5h14v-5",
    "nav-shortcuts": "M3.5 7h17v10h-17zM7 10.5h.01M10.5 10.5h.01M14 10.5h.01M17 10.5h.01M8 14h8",
    "nav-advanced": "M4 7h9M17 7h3M4 17h3M11 17h9M15 5v4M9 15v4",
    "nav-about": "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM12 11v5M12 8h.01",
    # Stitch page (main nav rail): two slices with a dashed cut between them
    "nav-stitch": "M5 3.5h14v6H5zM5 14.5h14v6H5zM3 12h2M8 12h2M13 12h2M18 12h3",
    # Pipeline steps (the editor's step bar)
    "detect": "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M8.5 8.5h7A1.5 1.5 0 0 1 17 10v3a1.5 1.5 0 0 1-1.5 1.5H12l-2.5 2v-2h-1A1.5 1.5 0 0 1 7 13v-3a1.5 1.5 0 0 1 1.5-1.5z",
    "ocr": "M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3M8 9h8M12 9v7M10 16h4",
    "translate": "M3.5 5.5h9M8 3.5v2M10.5 5.5c-.8 4-3.4 7.2-6.9 9M5.8 9.2c1.4 2.4 3.6 4.4 6.2 5.3M13 20.5l4-9.5 4 9.5M14.4 17.5h5.2",
    "segment": "M12 4a8 8 0 1 0 0 16M12 4a8 8 0 0 1 0 16M12 4v16M8 7.5l4 3M8 12l4 3",
    "clean": "M12 3.5l1.7 4 4 1.7-4 1.7-1.7 4-1.7-4-4-1.7 4-1.7zM18 14.5l.8 1.9 1.9.8-1.9.8-.8 1.9-.8-1.9-1.9-.8 1.9-.8z",
    "render": "M4 5h16v11H10l-5 4v-4H4zM8 9h8M8 12h5",
    "step-done": "M5 12.5l4.5 4.5L19 7.5",
    "step-warning": "M12 4l9 16H3zM12 10v4.5M12 17.2v.3",
}

# name -> path data (filled).
FILLED = {
    "run": "M7 4.5l12.5 7.5L7 19.5z",
}


def _svg(d: str, filled: bool) -> str:
    paint = (
        f'fill="{THEME_COLOR}" stroke="none"' if filled else
        f'fill="none" stroke="{THEME_COLOR}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"'
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'{paint}><path d="{d}"/></svg>\n'
    )


def icons() -> dict[str, str]:
    out = {f"{name}.svg": _svg(d, False) for name, d in STROKED.items()}
    out.update({f"{name}.svg": _svg(d, True) for name, d in FILLED.items()})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report differences instead of writing")
    args = parser.parse_args(argv)
    stale = []
    for name, text in icons().items():
        path = STATIC / name
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                stale.append(name)
        else:
            path.write_text(text, encoding="utf-8")
    if args.check and stale:
        print("out of date:", ", ".join(sorted(stale)))
        return 1
    if not args.check:
        print(f"wrote {len(icons())} icons to {STATIC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
