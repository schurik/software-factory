# Uninstall

Delete the stamped factory out of a repository and leave the repo the way it
was before `install.py` ran. **The skill is untouched** — it keeps every
generator, every template and every script, so re-stamping tomorrow is one
command. Reached however this harness names it — `/sssf:sssf uninstall` in
Claude Code, `/skill:sssf uninstall` in pi, or plain words in any agent that
just read this file.

## Say what dies, before you run it

This is the one thing the factory does that cannot be undone, and three of the
things it deletes are not code that can be re-stamped. **Put them to the
engineer in one message and get an answer**, the way the install asks its four
questions:

| What goes | Why it is worth a sentence first |
|---|---|
| **The run record** — `adws/adw_data/sssf.db` and every session under `adws/adw_data/sessions/` | Every trace of every run this repo ever did: phases, envelopes, gate results, token spend. Nothing regenerates it. If any of it is wanted, copy the db out first — it is one file. |
| **Your prompt and config edits** — `adws/adw_data/prompt_engineering/`, `sssf.config.yaml`, the quality commands in `adw_modules/quality.py` | Stamped once, then yours. A re-install brings back the starter versions, not the roster you tuned. Commit them, or copy `adws/` somewhere, if this repo might run the factory again. |
| **Uncommitted work in a run's worktree** | A failed or killed run keeps its worktree on purpose, and that tree is the only place its uncommitted changes exist. `just worktrees` lists what is out there; look before deleting. |
| **The branches** — only if asked | `sssf/<adw_id>` branches are the record of runs that never landed, and the script **keeps them by default**. `--branches` deletes them; the plan prints how many there are either way. |

Two things it will *not* delete on its own, and both are worth naming so nobody
goes looking for them afterwards: a `.env` that has your own values in it, and a
`justfile` that has your own recipes in it. See below.

## Look first

```bash
just uninstall-plan                                   # or, without just:
uv run <skill>/scripts/uninstall.py --dry-run
```

Prints exactly what would be deleted, what would be kept and why, how many files
under `adws/` the skill never stamped (your own ADWs — they go with the rest),
and how many run branches exist. Changes nothing.

`<skill>` is the directory this skill lives in — see the note at the top of
`SKILL.md`, and substitute the real path.

Run it from the **target repo root**. The cwd is what gets emptied; the script
refuses if that cwd is the skill itself, or a run's worktree.

## Then do it

```bash
just uninstall                                        # or:
uv run <skill>/scripts/uninstall.py
```

It asks — type `yes` — and there is no undo. `--yes` answers ahead of time for
an agent-driven or scripted uninstall, and without a terminal to ask at, a
missing `--yes` is an error rather than a silent deletion.

| Flag | What it changes |
|---|---|
| `--dry-run` | print the plan, change nothing |
| `--yes` | skip the confirmation; **required** when stdin is not a terminal |
| `--branches` | also delete the `sssf/<adw_id>` branches |
| `--force` | uninstall even while a run or a watcher is alive |

## What it removes

| Removed | Note |
|---|---|
| `adws/` entire | the ADWs, `adw_modules/`, `adw_sssf_config/`, both `*_engineering/` dirs, and the whole `adw_data/` run record |
| the per-run worktrees | via `git worktree remove`, so `.git/worktrees` is cleaned up too — `rm -rf .sssf-worktrees` leaves metadata that breaks the next `git worktree add` for a reused id |
| `.sssf-worktrees/` | the container, once git has released the trees inside it |
| `.env.sample` | when it is still one of the samples this skill ships |
| `justfile` | when it is still exactly the stamped one |
| the `# sssf runtime` block in `.gitignore` | header included; the file goes too if nothing else is left in it |
| `SSSF_SKILL=` in `.env` | the one line the installer wrote |
| `sssf/*` branches | **only** with `--branches` |

## What it keeps, and why

Three files live in a namespace the repository had before the factory arrived,
so each is compared against what was stamped and **kept the moment it differs**:

- **`.env`** — deleted only if it is the sample plus the `SSSF_SKILL` line the
  installer filled in. One API key of yours in it and the whole file stays;
  only the `SSSF_SKILL` line is removed.
- **`justfile`** — deleted only if it is byte-for-byte the stamped one. Yours
  once you edit it, and a stamped-then-edited justfile is kept and reported
  rather than guessed at. A justfile that predates the install is never touched
  — `install.py` skipped it, so it has no sssf recipes in it.
- **`.gitignore`** — only the entries that follow the `# sssf runtime` header
  the installer wrote. Half of that block (`.env`, `__pycache__/`, `*.pyc`) is
  what a repository plausibly ignored already, and un-ignoring a repo's own
  `.env` on the way out is a leak, not a tidy-up. No header — someone tidied
  the file by hand — and it is left alone with a line saying so.

The same conservatism catches version drift: a `.env.sample` or `justfile`
stamped by an **older** version of this skill does not match what ships today,
so it is kept and reported instead of deleted. Read it and remove it yourself.

## It refuses while anything is running

A half-deleted factory under a live run is worse than either end of this, and a
watcher that outlives its config keeps launching runs into a repo with no ADWs
left to run. So the script reads the trace for sessions and watchers that are
believed alive, **verifies each pid actually exists** (a SIGKILL or a reboot
leaves a `running` row forever — the row alone would block an uninstall on a
machine where nothing has run for a week), and stops with the list.

```bash
just kill <adw_id>        # each live run
# ctrl-c the `just up` that owns the watchers
just status               # confirm nothing is up
```

`--force` says you have already dealt with them. It does not stop anything.

## Afterwards

`git status` should show the repo as it was before the install. Re-stamp any
time — the skill never moved:

```bash
uv run <skill>/scripts/install.py --harness claude_code    # or: pi
```

Re-installing gives you the **starter** roster and prompts, not the ones you
tuned. That is the whole reason the questions at the top of this file are asked
before anything is deleted, and not after.
