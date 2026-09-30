# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [SemVer](https://semver.org/);
while `0.x`, a minor bump may break the public API (`agentloop.__all__`).

## [Unreleased]

## [0.4.0]

### Changed
- Published on PyPI as `based-models-agentloop` (import name `agentloop`), under the
  Apache-2.0 license. Installing from git is no longer required.
- Release pipeline: build once, publish to PyPI with Trusted Publishing (no API tokens),
  then attach the same files to the GitHub release.
- Comments, docstrings and tests no longer point at external course material.

## [0.3.0]

### Changed
- Distribution renamed to `based-models-agentloop` (import name is still `agentloop`).
  The name `agentloop` on PyPI belongs to an unrelated project. Reinstall under the new name.

### Added
- `agentloop.providers.fake.FakeClient`: a scripted `LLMClient` for tests.
- Architecture tests (`tests/test_layering.py`), cross-adapter translation tables, and a
  golden transcript test.
- CI, a wheel-in-a-bare-venv check (`scripts/test-wheel.sh`), and a tag-driven release.

## [0.2.0]

### Added
- Hosted tools and native web search, the OpenAI Responses API (now the OpenAI default),
  `Origin` for exact replay, `StopReason.PAUSED`.
