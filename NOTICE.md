# Licensing and attribution

This project is distributed under the **GNU General Public License v3.0 or
later** (see `LICENSE`).

It combines code under two different licences:

## Original work — Apache License 2.0

This repository is a fork of [comic-translate](https://github.com/ogkalu2/comic-translate)
by ogkalu2, which is licensed under the Apache License 2.0. The full text of
that licence is retained in `LICENSE-Apache-2.0`.

Those portions remain available under the Apache License 2.0 from their
original authors. Apache-2.0 is one-way compatible with GPLv3, so the
**combined** work distributed from this repository is offered under GPLv3.

## Incorporated work — GPL-3.0-or-later

Parts of the image cleaning pipeline are derived from
[PanelCleaner](https://github.com/VoxelCubes/PanelCleaner) by VoxelCubes,
which is licensed under GPL-3.0-or-later
(`modules/inpainting/mask_fitting.py`).

The text mask refinement in `modules/inpainting/text_mask_refine.py` is
derived from [comic-text-detector](https://github.com/dmMaze/comic-text-detector)
by dmMaze, which is licensed under GPL-3.0 and which PanelCleaner vendors for
the same purpose.

Files containing derived code carry an attribution header naming the upstream
source. Because GPLv3 is copyleft, incorporating this code is what requires
the combined work to be distributed under GPLv3.

## Practical consequences

- Binaries built from this repository must be accompanied by, or offer, the
  corresponding source under GPLv3.
- Changes to GPLv3-derived files cannot be contributed back to the Apache-2.0
  upstream project.

## Models

Model weights downloaded at runtime are covered by their own licences, held by
their respective publishers, and are not covered by this repository's licence.
