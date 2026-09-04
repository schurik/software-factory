# Install

`/sssf install` — stamp the entire factory out of the skill and into the current working directory.

## Ask first, stamp second

`install.py` stamps ONE set of defaults. Four of them are decisions the
repository owns, not the factory, and every one of them is cheap to answer now
and annoying to discover later — the first is a silently-passing test suite, the
second is a machine moving your main branch, the third is who may start a run at
all. **Put these to the engineer before you run the installer**, in one message,
with the defaults named so they can say "all defaults" and be done.

| Ask | Default if they shrug | Why it is not the installer's call |
|---|---|---|
| **How does this repo run its tests, lint and typecheck?** (`bun test`, `uv run pytest -q`, `npm run lint` …) | placeholders that `echo` and exit 0 | `quality.py` ships every block as a fake that **announces it is fake and passes**. A stamped repo cannot guess your runner, and a wrong-but-plausible command that goes green is worse than one that says so out loud. Until you replace them, `adw_simple_sdlc` "tests" nothing. |
| **How should a run's branch land — `merge`, `pr`, or `none`?** | `merge` | Repositories genuinely disagree about whether a machine may move the base branch. `merge` is right for a solo repo; `pr` is right anywhere a human reviews before main moves; `none` leaves every branch for a person. |
| **Should issues be able to start runs?** | off | This is the one path where the prompt is written by whoever can file an issue rather than by the engineer at the keyboard. Off until the repo opts in — see the block below. |
| **Which coding-agent backend?** | `pi` with `gemini-3.6-flash` | An all-`claude_code` roster needs **no API key at all** (the CLI brings its own auth), which is often the shortest path to a first green run. `pi` needs the right provider key in `.env`. |

Two more worth naming only if the answer is not the default: `worktree.enabled`
(on — a run works in its own tree on its own branch, never the engineer's
checkout) and `defaults.protected_files` (the factory's own code, so an agent
cannot edit the machinery that judges its work).

Apply the answers by editing `adws/adw_sssf_config/sssf.config.yaml` and
`adws/adw_modules/quality.py` **after** stamping — the installer takes no flags
for any of this, on purpose: the config is the record of what this repo decided,
and a flag would hide that decision in a shell history.

## Run it

```bash
uv run .claude/skills/sssf/scripts/install.py
```

Run from the **target repo root** — the cwd is where everything lands. If the skill lives in your user scope, the path is `~/.claude/skills/sssf/scripts/install.py`.

## What gets stamped

`install.py` copies `templates/` into the cwd:

| Stamped | From | Tracked? |
|---|---|---|
| `adws/adw_sssf_config/sssf.config.yaml` | `templates/sssf.config.yaml` | yes — the agent roster |
| `.env.sample` | `templates/env.sample` | yes |
| `adws/adw_*.py` | `templates/adws/` | yes — the fifteen starter ADWs, including `adw_integrate.py` and the two issue chains |
| `adws/adw_modules/` | `templates/adws/adw_modules/` | yes — all low-level logic |
| `adws/adw_data/prompt_engineering/{planner,builder,scout,reviewer,documenter}/` | `templates/prompt_engineering/` | yes — **the user-owned home for prompts** |
| `adws/adw_data/harness_engineering/` | `templates/harness_engineering/` | yes — **the user-owned home for pi extensions** |
| `justfile` | `templates/justfile` | yes — starter recipes: `just demo`, the workflows, the trace reads, `just obs` |
| `adws/adw_data/sessions/`, `adws/adw_data/sssf.db` | created at runtime | no — gitignored |
| `.sssf-worktrees/`, `adws/adw_data/issue-locks/` | created at runtime | no — gitignored: one worktree per run, one lock file per claimed issue |

The two `*_engineering` dirs mirror the two config keys of the same name: `prompt_engineering` is what an agent is told, `harness_engineering` is what its harness can do. Both are yours the moment they are stamped. Edit them in `adws/adw_data/`, never back inside the skill.

`harness_engineering/` ships with `subagents.ts` — the pi extension backing `subagent_create` / `_continue` / `_list` / `_remove`, wired to the planner and scout in the starter roster.

## Idempotency

Re-running is safe. `install.py` skips **every** file that already exists — your config, your prompts, and previously stamped code alike — and reports what it skipped, so a second run doubles as a drift check. To refresh stamped code (`adw_modules/`, the starter `adw_*.py`) to the skill's current version, run with `--force` — but know that `--force` overwrites ALL existing stamped files, including `sssf.config.yaml` and `prompt_engineering/`, so commit or back up user-owned edits first.

## Post-install checklist

1. **Env** — `cp .env.sample .env`, then set `OPENROUTER_API_KEY` in `.env` for the starter (Pi) roster. An agent on `coding_agent: claude_code` needs no key here — the `claude` CLI brings its own auth, and a subscription is enough; set `CLAUDE_PATH` only if the binary is not on PATH.
2. **Pi is installed and on PATH** — `pi --version`. Set `PI_PATH` in `.env` if it is not.
3. **The model resolves** — the config's default `gemini-3.6-flash` must be a registered id in `~/.pi/agent/models.json`. Check with `pi --list-models` or read the file directly; see `references/config.md` for model resolution.
4. **Gitignore** — `install.py` appends `adws/adw_data/sessions/`, `adws/adw_data/sssf.db*`, `.env`, `.sssf-worktrees/`, `adws/adw_data/issue-locks/`, `__pycache__/` and `*.pyc`; confirm they landed. The worktree entry earns its place twice: chains that commit call `git add -A`, so without it a run's first commit would try to add the tree it is running in.
5. **Git repo** — ADWs that end in a commit phase call `git_helper.commit_all`, which raises if the cwd is not a git repository. Run `git init` and make a first commit before using `adw_plan_build.py`, `adw_plan_build_test.py`, or `adw_simple_sdlc.py`. `adw_document.py` needs one too: it measures the change with `git diff` against a base ref (`main` by default, `--base` to override).
6. **Quality commands** — replace the `_placeholder(...)` calls in `adws/adw_modules/quality.py` with this repo's real argv, and delete the blocks you do not want. Skipping this does not fail anything, which is the problem: the placeholders exit 0, so a chain reports a green suite it never ran.
7. **Integration** — set `worktree.integration.mode` to what this repo agreed above. On `pr`, also decide `open_pr` (pushing is safe everywhere; opening a PR needs an authenticated forge CLI) and, if you want the PR to close its issue on merge, `pr_body_template`.
8. **Smoke test** — `just demo` runs two cheap read-only workflows back to back, or run the smallest ADW directly:

```bash
just demo                                                    # both, end to end
uv run adws/adw_prompt.py "reply with a one-line summary of this repo"   # the raw form
```

Green means the whole path works: config validated, session minted, Pi ran, envelope parsed, events landed in `adws/adw_data/sssf.db`. Verify the trace exists before trusting anything larger:

```bash
sqlite3 adws/adw_data/sssf.db "select adw_id, status from sessions order by started_at desc limit 1;"
```

If the smoke test fails, fix it before composing chains — every multi-agent ADW rides on this exact path.

## Where a run's work lands, from the first run onwards

Worth saying once at install time, because it surprises people who expect the
agents to edit their checkout: **every run executes in its own git worktree**
(`.sssf-worktrees/<adw_id>`) on its own branch (`sssf/<adw_id>`), cut from
whatever the main checkout had at run start. Your working tree is never touched,
two runs can execute at once, and a failed run leaves its state somewhere you
can open rather than somewhere in the way.

So a chain's commits are **on its branch, not on yours** until integration lands
them — a branch that has not landed is not a failed run. `just worktrees` lists
what exists, `just worktrees-prune` reclaims what nothing needs, and
`just integrate <adw_id>` lands a branch by hand. Details in
[references/config.md](../references/config.md#worktree-per-run).

## Turning on issue-triggered runs

Only if the repository said yes above. Everything is in the `issues:` block of
`sssf.config.yaml`:

```yaml
issues:
  enabled: true
  project: "owner/repo"            # "" infers from the origin remote; SET IT for cron
  route:
    "sssf:build": adws/adw_issue_sdlc.py
    "sssf:scout": adws/adw_issue_scout.py
  trusted_authors: []              # [] = the person who applied the label is the authorization
```

Then create the four labels the state machine uses (`sssf:queued`, `:running`,
`:done`, `:failed`), and drive it with `just issues` from cron. `just
issues-status` prints what the watcher would do and whether it *can* — run it
once before scheduling anything, because a watcher that cannot resolve its
project polls nothing and looks identical to one with nothing to do.

**Say the trust boundary out loud to the engineer**, because it is the thing
that changes when this goes on: an issue body is written by whoever can file
one, and it reaches agents holding `bash`, `write` and a checkout. The routing
label is the authorization — a human applies it — and `issues.force_pr` keeps an
issue-triggered run off the base branch whatever `integration.mode` says. Full
schema and the enforcement order: [references/config.md](../references/config.md#issues).
