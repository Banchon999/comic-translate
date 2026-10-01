"""The release workflow publishes what the build workflows build, for the
version the app reports. These checks keep the pieces from drifting apart:
a tag that fails release.yml's own check only fails after it is pushed."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _version() -> str:
    text = (ROOT / "app" / "version.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def test_the_current_version_has_release_notes():
    notes = ROOT / ".github" / "release-notes" / f"v{_version()}.md"
    assert notes.is_file() and notes.read_text(encoding="utf-8").strip(), (
        f"release.yml refuses a tag without {notes.relative_to(ROOT)}"
    )


def test_the_version_is_a_plain_release_number():
    # The update checker compares versions numerically; a suffix it cannot
    # parse would make every release look older than the one installed.
    assert re.fullmatch(r"\d+\.\d+\.\d+", _version())


def test_every_workflow_the_release_calls_can_be_called():
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    called = re.findall(r"uses:\s*\./\.github/workflows/([\w.-]+\.yml)", release)
    assert set(called) == {"test.yml", "build-windows.yml", "build-linux.yml"}
    for name in called:
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        assert re.search(r"^\s+workflow_call:", text, re.M), f"{name} cannot be called by release.yml"
        # The manual button stays.
        assert "workflow_dispatch" in text, name


def test_the_release_publishes_exactly_what_the_builds_upload():
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    published = set(re.findall(r"dist/(ToonStudio-[\w.-]+)", release))
    built = set()
    for name in ("build-windows.yml", "build-linux.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        upload = text[text.index("upload-artifact"):]
        built |= set(re.findall(r"path:\s*dist/(ToonStudio-[\w.-]+)", upload))
    assert published == built and len(built) == 2


def test_the_release_runs_only_on_a_version_tag():
    release = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    on_block = release[release.index("\non:"):release.index("\npermissions:")]
    assert "tags: ['v*']" in on_block
    assert "branches" not in on_block and "pull_request" not in on_block
