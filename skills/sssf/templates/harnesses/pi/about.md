pi — `pi -p --mode json`; models are `provider/model-id` and need that provider's API key

The roster you just stamped runs on pi. Before the first run:

1. **Pi is installed and on PATH** — `pi --version`. Set `PI_PATH` in `.env` if not.
2. **Keys** — `cp .env.sample .env` and fill in the providers the roster names. The
   starter roster names three (openrouter, fireworks, openai); pointing every agent at
   one provider — set `defaults.model`, delete the per-agent `model:` overrides — leaves
   you needing exactly one key.
3. **The models resolve** — every `model:` must be a `provider/model-id` pair pi knows.
   Check with `pi --list-models`; a bare id that several providers carry is rejected as
   ambiguous rather than guessed at.
4. **The extension** — `adws/adw_data/harness_engineering/subagents.ts` backs the
   `subagent_*` tools the planner and the scout are told to use. It is yours now; the
   tools only exist for an agent that both loads the extension and names them in
   `tools:`.
