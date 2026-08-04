# Contributing to QSave Pro

Thanks for considering a contribution! QSave Pro is a single-file Tkinter application, so a little coordination goes a long way in keeping it maintainable.

> **A note on licensing:** QSave Pro is distributed under a custom, source-available [LICENSE](LICENSE) that does not permit general redistribution of modified copies. Forking this repository *specifically to submit a pull request back to this project* is permitted and encouraged. Forking to publish or distribute your own modified version elsewhere is not covered by this license — see the LICENSE file or contact the author if that's what you're after.

## Before you start

- **Bug fixes**: feel free to open a pull request directly.
- **New features**: please open an issue first describing what you want to add and why. This avoids duplicated effort and lets us agree on the approach (especially for anything touching hotkeys, the save-rotation logic, or the UI layout) before you invest time in code.

## Development setup

```bash
git clone https://github.com/Mahdiqoo/qsave-pro.git
cd qsave-pro
pip install -r requirements.txt
python qsave.py
```

There's no build step — it's a plain Python script. Test manually by pointing a preset at a scratch folder before testing against a real game save.

## Code style

- Keep the file's existing section headers (`# === N. SECTION NAME ===`) and add new code under the most relevant one, or create a new section if it doesn't fit.
- Match the existing Tkinter widget style (see the `RoundButton`, `Switch`, `Input`, and `Dialog` classes) rather than introducing a new UI pattern for a single feature.
- Keep optional dependencies optional: any code touching `keyboard`, `PIL`, `pystray`, or `psutil` must fail gracefully if that package isn't installed, matching the existing `try/except` import pattern at the top of the file.

## Pull requests

- Keep PRs focused — one feature or fix per PR is much easier to review than a bundle of unrelated changes.
- Describe what you tested (OS, Python version, whether hotkeys/tray/screenshots were exercised).
- Update the README's feature list or roadmap if your change adds or removes user-facing functionality.

## Reporting bugs

Please include:
- OS and Python version
- Which optional dependencies are installed
- Steps to reproduce
- The relevant lines from `qsave_activity.log` if the bug involves a save/restore failure
