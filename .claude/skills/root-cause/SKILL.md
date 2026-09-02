---
name: root-cause
description: How to find the actual cause of a bug in this repo instead of a plausible one, and what counts as proof that a fix works. Use this whenever you are investigating a crash, a regression, a report from the built/shipped app, output that "looks wrong", or anything a user reports that the test suite did not catch — and before you claim any bug is fixed. Also use it when a build works on Linux but fails on Windows, when an exported file (PSD, PDF, CBZ, EPUB) is wrong, or when you are about to write "the cause is…" in a commit message or PR comment.
---

# Finding the real cause

This repo has now produced four PSD export bugs, two frozen-Windows crashes, and
several wrong diagnoses. The wrong diagnoses all failed the same way, and the
right ones were all found the same way. This is that pattern.

## The one rule

**A mechanism that reproduces the symptom is not evidence that it is the cause.**

Three times in one session a confident, well-argued, *wrong* cause was published:

| Claimed cause | How it was argued | Actual cause |
|---|---|---|
| A data race in shared Skia objects | Read the code, found a real race, called it "the likeliest candidate" | `icudtl.dat` missing from the PyInstaller bundle |
| Layers written as ZipPrediction | Wrote a decoder supporting only Raw+RLE, showed it recovers nothing, matched the screenshot | Every layer written with **fill opacity 0** |
| Pure-Python `_pack_bits` too slow for an 18700px strip | Reasoned about a per-byte Python loop over 42 MB | Measured it: 3 seconds. Not a factor. |

Each claim was *true as a mechanism* and false as a diagnosis. The race was
real. Zip-less readers really do see nothing. Python byte loops really are slow.
None of them was what happened.

The cost is not just wasted time — a wrong cause gets committed, shipped, and
then has to be publicly corrected.

So: when you have a theory that explains the symptom, that is the moment you are
**most** at risk, not least. Ask what observation would distinguish your theory
from the others, and go get that observation.

## The verification ladder

Every export/render bug in this repo sits at one of four levels, and each level
is blind to the one above it. Know which level your evidence is on.

| Level | What it proves | What it cannot see |
|---|---|---|
| 1. The writer reads its own output | Nothing. It agrees with itself. | Everything |
| 2. An independent parser reads it (psd-tools) | The structure is well-formed | Whether it *draws* |
| 3. A real renderer opens it (Photopea) | It actually appears | Whether it survives packaging |
| 4. The frozen build on the target OS | It ships | — |

Mapped onto the real bugs:

- **Black merged image** — passed 1 and 2, failed 3.
- **Fill opacity 0** — passed 1 and 2 *perfectly*: correct document size, group
  and layer names, bounds, channel data, alpha. Failed 3.
- **Missing `numpy.core.multiarray`** — passed 1–3, failed 4.
- **Missing `icudtl.dat`** — passed 1–3, failed 4. Could not have been caught on
  Linux at all: skia-python links ICU statically there, so the file does not
  exist in the Linux package.

`scripts/check_psd_in_photopea.py` is level 3 for PSD. Use it after any
`psd_exporter.py` change. `app/controllers/psd_importer.py` is **not** level 2 —
it imports `psapi` from the exporter, so it is level 1 wearing a disguise.

## Silent-success bugs

The expensive class: **every checker passes and the artifact is still useless.**
Fill opacity 0 is the archetype — a parser returns the pixels regardless, so
every structural assertion stayed green while the page drew nothing.

When you write a test for an artifact, ask: *what would still pass if the file
were completely unusable?* If the honest answer is "all of it", the test is
checking that the writer ran, not that the output works. Add an assertion at a
level the bug can't hide at — for fill opacity that meant reading the `iOpa`
byte out of the file, because nothing in the parsing library exposes it.

## When a build regresses

The user reports the built app broke. Before theorising about code:

1. **Find the last build that worked and the first that didn't.** Build runs are
   `workflow_dispatch` only, so the list of runs is short and each records its
   `head_sha`.
2. **Prove the dependency environment is identical.** `requirements.txt` pins
   loosely (`PhotoshopAPI>=0.9.0`), so a dependency can move under you. The
   `setup-python-…-pip-<hash>` cache key in the build log is derived from the
   requirements files — same key in both runs means same dependency set, and the
   difference really is your code.
3. **Diff only the file that matters** between those two SHAs. In the PSD crash
   this reduced the search space to `+60/-0` in one file, containing exactly two
   functional lines. That is a search space you can reason about; "somewhere in
   47 commits" is not.

## Platform asymmetries — "works on Linux" is not evidence about Windows

For anything native, packaged, or filesystem-level, a green Linux run says
nothing. Known cases in this repo:

- **ICU** is statically linked into skia-python's `.so` on Linux and loaded from
  a separate `icudtl.dat` on Windows. The dependency exists only on Windows,
  only once frozen.
- **The heap.** Windows detects heap corruption and calls `__fastfail`, killing
  the process with no signal — `faulthandler` never runs. glibc quietly tolerates
  the same corruption. A latent buffer overrun is invisible on Linux and
  instantly fatal on Windows.
- **`os.replace`** is atomic on POSIX. On Windows it is `MoveFileEx`, which
  returns `ACCESS_DENIED` if either file has an open handle — and Defender opens
  freshly written files to scan them. Intermittent, and only on Windows.
- **stdout is discarded** in a `--windowed` PyInstaller build, so a native
  library's own progress logging vanishes. Anything you need to see must go
  through `app/crash_log.py`.
- **Fonts** differ between the CI runner, the dev container and the user's
  machine. Never assert a metric that depends on a face being installed.

## Reading a crash from the log

`app/crash_log.py` routes logging, both excepthooks, Qt messages and
`faulthandler` to `<user data>/logs/comic-translate.log`. Read it precisely:

- **A log line missing** means the code never got there. `logging.FileHandler`
  flushes per record, so absence is real evidence, not a buffering artifact.
- **`faulthandler` armed and silent** rules a lot out: it catches access
  violations and `SIGABRT`. Silence points at `__fastfail` (heap corruption) or
  an external kill, not an ordinary segfault.
- **A 0-byte output file** means the native call opened it and died before
  flushing — `_write_page_psd` says exactly this in a comment, because it has
  happened before.
- **Hang and crash look identical in a log.** The discriminator is not in the
  file: ask the user whether the app vanished on its own or they closed it. That
  one question was worth more than an hour of reasoning.

Time gaps in a log measure *when the user noticed*, not how long the code ran.

## Ask the cheap question first

Before a long investigation, work out which single observation would split the
hypothesis space most, and whether the user can get it in a minute. Good ones
from real use:

- Did it die on its own, or did you close it?
- Is there an output file, and how big is it?
- Does the same thing happen on a small input?

"Export a short page" settled in one attempt what would otherwise have needed
two builds: the small page crashed too, which eliminated size, layer count and
memory in one move.

## Before claiming a fix

- Run the level that the bug lives at, not the level that is convenient.
- Show the guard **failing when the fix is reverted**. A test that passes both
  ways guards nothing, and this is cheap: revert, run, restore, run.
- Say what you did not verify. "Verified in Photopea" and "should work in
  Photoshop" are different sentences and both belong in the commit.

## When you were wrong

You will be. Correct it where the wrong claim lives — if it went into a commit
message or a PR comment, a follow-up comment saying plainly which earlier claim
was wrong and why is worth more than a quiet fix. Leave the incorrect reasoning
described, not just the conclusion, so the same argument does not get made
again.

Keep the change if it stands on its own merits; just stop calling it the fix.
The RLE compression change was kept for real compatibility reasons, with its
docstring rewritten to say it fixed nothing.
