# YSSH full-video assembly

This extension prepares a single editable video track from an ordered filename
array. It uses the existing native plan format and draft CLI. It does not publish
drafts, render a final video, transcribe speech, trim clips, or apply screenshot
annotations such as reverse, speed changes or blurring.

## Prepare

Read the script screenshots in order and save a JSON array of exact local
filenames. Keep repeated references, for example:

```json
["first.mp4", "second.mov", "first.mp4"]
```

Keep real screenshots, sequence files, media and reports outside the source
checkout. The source directory must contain regular files with unambiguous names.

```sh
python3 tools/yssh_assemble.py \
  --media-dir /absolute/local-media \
  --sequence /absolute/job/sequence.json \
  --name personal-assembly-v1 \
  --out /absolute/job/prepared
```

The output directory must be new and outside both the source media directory and
the live draft tree. Compatible H.264/HEVC clips are reused. Otherwise the script
creates an H.264 Main, yuv420p, 10 Mbps copy, applies source display rotation,
preserves display dimensions and frame timestamps, and encodes the selected audio
as AAC 128 kbps at 48 kHz. Multiple audio streams require exactly one marked
default; unselected streams remain available in the unchanged original file.
HDR conversion and non-square source pixels require separate review.

Outputs are `sequence.json`, `plan.json`, `report.json`, `normalized/` and `logs/`.
The report records original and prepared hashes, conversion reasons, audio
selection and source preservation. Failures retain `failure.json` and any partial
artifacts. Do not reuse or overwrite a failed output directory.

The plan uses full source video durations in screenshot order, original sound at
gain 1, speed 1, one main track and a 1080 x 1920 canvas at 30 fps.

## Build and accept

First match the upstream bridge toolchain and pass `doctor`. Use the existing
`skills/yichen-jianying-edit/scripts/headless_draft.py` commands: `build`,
`verify-build`, then `publish` only after saving work and quitting Jianying.
Test a two-clip draft before registering the full assembly. Check actual picture,
sound and independent clip editability, save, quit, reopen, then quit and run
`verify`. A valid JSON plan is not native UI acceptance.

## Source checks and updates

```sh
python3 tools/check_package.py
python3 -m unittest discover -s tests -v
```

Keep `main` aligned with upstream and custom work on `yssh`. Merge upstream
updates into a temporary branch from `yssh`, run these checks and the real-media
acceptance flow, then update `yssh`. Do not force-sync the custom branch.
Create a stable version tag only after native acceptance passes. The original
license and third-party notices continue to apply.
