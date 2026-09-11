claude_code — `claude -p`; models are aliases (opus, sonnet, haiku) and the CLI brings its own auth, so no API key is needed

The factory you just stamped runs on Claude Code. Before the first run:

1. **The CLI is installed and on PATH** — `claude --version`. Install with
   `npm i -g @anthropic-ai/claude-code`, or set `CLAUDE_PATH` in `.env`.
2. **You are authenticated** — `claude auth`. A Claude subscription is enough.
3. **`safe_mode: true` is on** (`defaults.harness_options.claude_code`), so runs ignore
   your `CLAUDE.md`, skills, plugins, hooks and MCP servers. Deliberate: a run that
   depends on whose machine ran it is what the factory removes.
4. **Running as root?** The CLI refuses `permission_mode: bypassPermissions` there.
   Use `acceptEdits`, and know that it silently denies what it would have prompted for.
