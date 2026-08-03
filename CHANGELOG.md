# Changelog

All notable changes to **monet** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/). The
version is derived from git tags via setuptools-scm, so cutting a release means:
move the `[Unreleased]` notes into a new `[x.y.z]` section dated today, then
`git tag vx.y.z && git push --tags`.

## [Unreleased]

### Fixed
- A completed calibration no longer leaves the shared instrument reporting "no
  calibration available": `CalibrationProtocol2D.run_protocol` reloads the
  calibration database at the end (each 1D step toggles `is_calibrated` off),
  and the Set Power tab refreshes its range display when a run finishes. Power
  could not be set from the Set Power tab after calibrating until reconnecting.

### Added
- Calibrate tab: the live "amplitude vs. laser power" plot now overlays the
  most recent previous runs as thin faded reference lines (per wavelength,
  matched to the current power-meter position), so drift is visible while a
  calibration builds up (`io.load_amplitude_history`).
- Calibrate tab: single calibrations whose amplitude strays from the (expected
  linear) amplitude-vs-power trend are flagged in red and listed in a new panel
  where they can be ticked and **discarded** or **recalibrated in place**.
  Outlier detection uses a robust Theil–Sen fit + MAD test
  (`io.flag_amplitude_outliers`); `CalibrationProtocol2D.run_protocol` gained a
  `power_filter` for re-measuring individual points.
- Database tab: a transmission-factor plot showing every objective-transmission
  factor by date, wavelength, and laser power (`io.compute_factor_breakdown`),
  so per-input drift and outliers are visible at a glance.
- Database tab: manually pick which two calibrations (one sample-plane, one
  BFP) define an objective transmission factor — "Add pair from selected"
  computes and stores the pair, "Remove pair" deletes it. Pairs persist over
  time in a local JSON sidecar (`io.save_factor_pair` / `load_factor_pairs` /
  `compute_pair_factor`), drive the transmission plot, and also update the
  factor used for BFP→sample power projection.
- Calibrate tab: a free-text **Comment** field (e.g. "laser status orange
  today") saved with every calibration of a run and shown in its own Database
  tab column.

### Changed
- Calibrate tab: off-linear flagging now uses a simple 2 % relative-deviation
  threshold against the robust line (was a MAD z-score), matching how operators
  reason about it (`io.flag_amplitude_outliers`).
- Calibrate tab: discarding flagged points now only deletes their records and
  keeps them listed; re-measuring is a separate explicit "Re-measure selected"
  action (no automatic re-acquisition).
- Objective transmission factor: the pooled P_sample/P_bfp ratios now have
  robust (MAD) outliers dropped before averaging, so a single failed
  calibration no longer skews the saved factor (`io.mad_outlier_mask`).
- Calibrate tab: the "BFP powermeter" checkbox is now a "Powermeter position"
  dropdown (BFP / sample plane), matching the Set Power tab's selector.
- Database tab: the record list is now pre-filtered to the connected
  microscope on connect (other scopes remain reachable via the web dashboard
  link).
- Build/versioning aligned to the shared DNA-PAINT stack conventions (S0A-2):
  the version is now derived from the git tag via setuptools-scm (written to
  `monet/_version.py`; `monet.__version__` imports it with a fallback) instead
  of a hand-pinned `version` in `pyproject.toml`. `[tool.black]`
  (`target-version = py310`, line-length 79) and `[tool.flake8]`
  (`extend-ignore = E203,E501,W503`, so black owns line length) now live in
  `pyproject.toml`; the standalone `.flake8` was removed. Added the shared
  pre-commit config (pre-commit-hooks + black + flake8 via Flake8-pyproject) and
  a CI workflow running `black --check`, `flake8`, and `pytest`. black, flake8,
  Flake8-pyproject, and pre-commit were added to the `[dev]` extra. No behavior
  change.
