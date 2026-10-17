# Changelog

## Unreleased

- Fix Windows encoder-wrapper shutdown so error cleanup releases child-process file handles.
- Extract native still frames without version-specific FFmpeg frame-sync options; report tool stderr on failure.
- Emit CLI JSON and diagnostics as UTF-8 under legacy Windows code pages.
- Keep all CI matrix jobs running independently and print tool/dependency versions for diagnosis.

## 0.1.0

- Portable sprite/video composition with a shared global clock and bounded readers.
- Dedicated caption rendering, native-frame evidence extraction and declared-window audio QA.
- Beat clock and exploratory PCM rhythm analysis with explicit uncertainty.
- Self-contained project archives with hashes, path validation and tested restoration.
- Synthetic demo, reusable asset prompts and instructions for Codex contributors.
