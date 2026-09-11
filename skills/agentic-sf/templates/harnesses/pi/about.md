pi — `pi -p --mode json`; models are `provider/model-id` and need that provider's API key

The factory you just stamped runs on pi. Before the first run:

1. **Pi is installed and on PATH** — `pi --version`. Set `PI_PATH` in `.env` if not.
2. **Keys** — `cp .env.sample .env` and fill in the provider `defaults.model` names.
3. **The model resolves** — `pi --list-models` must know it; a bare id several
   providers carry is rejected as ambiguous rather than guessed at.
