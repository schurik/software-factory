claude_code — `claude -p`; models are aliases (opus, sonnet, haiku) and the CLI brings its own auth, so no API key is needed

The roster you just stamped runs on Claude Code. Before the first run:

1. **The CLI is installed and on PATH** — `claude --version`. Install with
   `npm i -g @anthropic-ai/claude-code`, or set `CLAUDE_PATH` in `.env`.
2. **You are authenticated** — `claude auth`. A Claude subscription is enough; there is
   no key to put in `.env` for this roster.
3. **`safe_mode: true` is on** (`defaults.harness_options.claude_code`), so runs ignore
   your `CLAUDE.md`, skills, plugins, hooks and MCP servers. That is deliberate — a run
   that depends on whose machine it executed on is what the factory removes. Turn it off
   per agent if this repository genuinely wants its own context loaded.
4. **Running as root?** The CLI refuses `permission_mode: bypassPermissions` there. Use
   `acceptEdits`, and know that it silently denies whatever it would have prompted for.
