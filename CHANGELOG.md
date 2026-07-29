# Changelog

All notable changes to **monet** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/). The
version is derived from git tags via setuptools-scm, so cutting a release means:
move the `[Unreleased]` notes into a new `[x.y.z]` section dated today, then
`git tag vx.y.z && git push --tags`.

## [Unreleased]

### Changed
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
