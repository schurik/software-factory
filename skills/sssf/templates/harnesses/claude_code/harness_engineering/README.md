# harness_engineering — Claude Code

`prompt_engineering` is what an agent is told; **`harness_engineering` is what its
harness can do**. This directory is yours: put the files an agent's entry in
`sssf.config.yaml` points at here, and reference them by repo-relative path.

On this harness an entry says which of three unrelated things it is — the code does not
guess from a file suffix:

| Entry | Becomes | For |
|---|---|---|
| `mcp:adws/adw_data/harness_engineering/servers.json` | `--mcp-config <file>` | MCP servers, and only the ones this config passes (`strict_mcp_config: true`) |
| `agents:adws/adw_data/harness_engineering/agents.json` | `--agents <json-or-file>` | custom subagents the `Task` tool can spawn |
| `plugin:adws/adw_data/harness_engineering/my-plugin` | `--plugin-dir <dir-or-zip>` | a Claude Code plugin |

Two things to know before you add one:

- **`safe_mode: true` suppresses all three.** It is on by default in
  `defaults.harness_options.claude_code`, and it is what keeps a run from depending on
  whose machine it executed on. An agent that needs an entry here sets `safe_mode: false`
  in its own `harness_options`; validation refuses the combination rather than loading an
  entry that would reach nothing.
- **A tool an entry registers must be named in that agent's `tools:`.** `--tools` filters
  MCP and custom tools too, so an unnamed one is loaded and then silently filtered out.

Pi's `-e <file.ts>` extensions have no equivalent here — the two harnesses do not share
this key, and a `.ts` path on a claude_code agent fails validation.
