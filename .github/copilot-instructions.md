## Copilot instructions for ticker.sh AI improvements

This file provides guidance for GitHub Copilot and future contributors when adding AI features to the `ticker.sh` project.

Goals
- Add an optional AI-powered alerting feature that analyzes a symbol's recent price data and indicators (RSI, MACD, PPO) and returns a buy/sell/hold recommendation with a confidence score (1-10).
- Keep the core script lightweight; offload heavy lifting (indicator computation, AI prompt composition, API calls) to a small, well-tested helper script.
- Store API keys outside of the repository using environment variables and `.env` files; provide an example `.env.example` file.

Environment & .env handling (important)
- Never commit a real `.env` file to the repository. Contributors must not modify or add a `.env` file inside the repo. Real API keys and secrets belong only in the developer's local environment or a private secrets store.
- Use `.env.example` to document configurable variables and safe default values. When changing runtime-config defaults, update only `.env.example` and the README — do not change or include an actual `.env` file in commits.
- Add `.env` to `.gitignore` (already present) so local `.env` files are not accidentally committed. If you need to share a configuration snippet, update `.env.example` or the README.
- For local debugging, contributors may copy `.env.example` to `.env` and fill real credentials locally (e.g., `cp .env.example .env && edit .env`). Do not commit that file.

Design constraints
- Avoid shipping secrets. The repo should include `.env.example` and a `.gitignore` entry for `.env`.
- The default AI model will be `gpt-5-mini` (or a compatible model name); code should allow switching models via environment variables.
- Keep runtime dependencies minimal. Use Python 3.8+ for the helper script and the official OpenAI Python SDK (or a simple HTTP client if SDK not available).
- The helper script should accept CLI arguments: symbol, timeframe (e.g., 1m,5m,20m,1h,1d), and optionally model and API key via env.

Testing and safety
- Add unit tests for indicator calculation when possible, and a smoke test to ensure the helper script can run without network when given canned data.
- Ensure the AI prompt is deterministic and includes a clear instruction to return a JSON blob with fields: recommendation (buy/sell/hold), confidence (1-10), indicators (with values), and rationale.

Integration with `ticker.sh`
- Add a new flag `-a TIMEFRAME` or `--alert TIMEFRAME` to `ticker.sh` that triggers the helper script for each symbol provided.
- The main script should call the helper script and print the AI recommendation inline with the symbol's price output.

Security and privacy
- Do not log API keys. Read from environment variables or `.env` at runtime.
- Rate-limit calls when running for multiple symbols to avoid API overages.

Contributor notes
- Follow POSIX/bash conventions for `ticker.sh` updates.
- Keep the helper Python script small and documented.
