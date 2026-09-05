# Run ADW

Run a workflow and report on it. **You run and observe — you never step into the process or do the work yourself.**

## Step 0 — translate the request

**Read [how_to_prompt_for_the_eng.md](how_to_prompt_for_the_eng.md) before you launch anything.** The prompt you pass is read by every agent in the chain, so it gets written deliberately: same intent, sharper words, verified paths, and a stated "done means". That cookbook is the whole procedure; this one starts once you have the prompt.

## The orchestrator's posture

The ADW is the worker. Your job is to launch it, watch the trace, and tell the engineer what happened. Do not read the agent's target files and "help", do not fix the code an agent was supposed to fix, do not edit an envelope. If a run fails, report the failing phase and its violations — the fix is a config, prompt, or ADW change, made deliberately, and then a re-run.

## Launch

Which chain to launch is decided in `how_to_prompt_for_the_eng.md`, and the short version is: **the ADW the engineer named, or else the most complete composed chain the work justifies — never a single-agent one.** Read `ls adws/adw_*.py` and the `Phases:` line in each docstring to see what this repo has; the names below are shape, not a menu.

```bash
uv run adws/<end-to-end-chain>.py "add a /health endpoint"
uv run adws/<plan-build-verify-chain>.py requests/health.md
uv run adws/<build-first-chain>.py "implement the plan" --adw-id a1b2c3d4
uv run adws/<recon-chain>.py "where is auth handled" --config path/to/other.config.yaml
```

The prompt is inline text or a file path. Launch in the background so you can poll while it works; the `adw_id` is printed on startup — capture it, everything else keys off it.

### Listen for the roster

The chain says *what runs*; the config says *who runs it*. **If the engineer references a roster, a config, or a model tier, pass it — do not fall through to the default.**

```bash
just rosters                            # every roster on disk, and the model each agent runs
```

That prints the path to pass and who is in it, in one read:

```
adws/adw_sssf_config/sssf.config.yaml
    planner     fireworks/accounts/fireworks/models/kimi-k3
    builder     google/gemini-3.6-flash (inherited)
adws/adw_sssf_config/sssf.frontier.config.yaml
    planner     anthropic/claude-opus-5
```

Read those from disk every time. Rosters are the engineer's to add, rename, and retune, so a name you remember from a doc is a guess.

They will rarely say `--config`. Treat any of these as naming a roster, then resolve it to a file:

| What they say | What it means |
|---|---|
| "run it on the frontier config", "use the frontier roster" | the roster file whose name matches |
| "run this with the big models", "use the sota roster" | the non-default roster — confirm which if there is more than one. Each config's header comment lists the names it answers to, so `head -3` on the file settles it |
| "have opus plan this one" | a roster whose planner is that model; if none exists, say so rather than editing the config mid-request |
| nothing about models at all | the default, `adws/adw_sssf_config/sssf.config.yaml` |

`--config` takes the path directly; the justfile recipes read `SSSF_CONFIG` instead:

```bash
uv run adws/<chain>.py "<prompt>" --config adws/adw_sssf_config/sssf.frontier.config.yaml
SSSF_CONFIG=adws/adw_sssf_config/sssf.frontier.config.yaml just <recipe> "<prompt>"
```

Two things that bite:

- **Never swap rosters on your own.** A different roster is a different cost and a different result. If the default's model looks wrong for the work, say so and let the engineer choose.
- **Switching rosters mid-session breaks resumption.** `agent_map.json` records the model each coding-agent session was created with, so a joined run (`--adw-id`) whose config now names a different model starts that agent **fresh** instead of resuming its context window. That is deliberate — a bad resume is worse — but it means "plan on the frontier roster, then build on the default" costs the builder its accumulated context. Say so when you report it.

`--adw-id` is optional on **every** ADW. Given one, the run joins that session if it exists or creates it pinned to exactly that id: same `sessions/{adw_id}/` dirs, same `context_handoff/`, envelopes appended, and each agent resumes its existing coding-agent context window via `agent_map.json`. That is how you chain ADWs — plan under one id, then build under the same id.

## Launching from an issue

Only when `issues.enabled` is on. Two shapes, and the difference matters for what you tell the engineer afterwards:

```bash
just issue 42                    # one issue, end to end, right now
just issue-scout 42              # read-only triage; comments its findings back
just issues                      # one poll: claim everything labelled, launch each
just issues-status               # what the watcher would do, and whether it CAN
```

`just issues-status` before scheduling anything. A watcher that cannot resolve its project polls nothing and looks exactly like a watcher with nothing to do — it prints the resolved project, the routes, and how many runs it believes are in flight.

An issue-triggered run differs from a typed one in three ways worth reporting:

- **Its ask came from a stranger.** The body reached the agents as an artifact framed as a user's description of a problem; the routing label a human applied is the authorization.
- **It cannot merge.** `issues.force_pr` downgrades a configured `merge` to `pr`, so the work arrives as a pull request even in a repo that normally merges.
- **The issue heard about it.** The run's last phase comments the outcome, the `adw_id` and the PR url back on the issue, so "did anyone tell the reporter" is already answered.

The label is the state a human reads: `sssf:running` left behind means a watcher died mid-run, and moving it back to `sssf:queued` by hand is the whole recovery.

## Observe

The trace db is `adws/adw_data/sssf.db`. It is WAL, so reads never block the running writers — poll it as often as you like.

```bash
# where the run stands
sqlite3 adws/adw_data/sssf.db \
  "select seq, name, kind, owner, status, attempt from phases where adw_id='a1b2c3d4' order by seq;"

# the live tail — cursor on rowid, same query the visualizer polls
sqlite3 adws/adw_data/sssf.db \
  "select rowid, type, name, started_at from events where adw_id='a1b2c3d4' and rowid > 0 order by rowid limit 50;"

# why a phase failed
sqlite3 adws/adw_data/sssf.db \
  "select attempt, gate, passed, checks_json from gate_results where adw_id='a1b2c3d4';"

# session-level status, with where the ask came from and where the work went
sqlite3 adws/adw_data/sssf.db \
  "select adw_id, trigger, status, substr(request,1,40), issue_url, pr_url
     from sessions order by started_at desc limit 5;"

# runs a stranger asked for
sqlite3 adws/adw_data/sssf.db \
  "select adw_id, status, issue_url, pr_url from sessions where trigger='issue' order by started_at desc;"

# what an agent actually did, slowest tool calls first
sqlite3 adws/adw_data/sssf.db \
  "select name, tokens, started_at, ended_at from events
   where adw_id='a1b2c3d4' and type='tool_call' order by ended_at desc limit 20;"
```

Poll on a cursor: keep the highest `rowid` you have seen and query `where rowid > ?`. Don't re-read the whole table each pass.

`trigger` is `engineer` or `issue`, and NULL only on rows written before that column existed — a run nobody can classify and a run somebody typed are different answers. `issue_url` and `pr_url` are the two ends of a run's provenance; the *reason* a branch did not land is in the integrate phase's notes, not in a column.

`tool_call` rows carry a real span, so durations come off the columns — see `references/observability.md` for which fields each event type populates.

The ADW also narrates to stdout, and every line it prints is written to the db as a `log` event — terminal and swim lane tell the same story by construction, so tailing the background process is a valid second view rather than a competing source of truth.

Files are the raw record if you need more than the db shows: `adws/adw_data/sessions/{adw_id}/{agent}/raw_output.jsonl` (full coding-agent stream), `envelope.json` (the parsed final response), `prompts/` (exactly what was sent), and `context_handoff/` (what agents wrote for each other).

## When a run is stuck

A hung coding agent produces no events at all, so the trace goes quiet rather than red. Read it in this order:

```bash
just phases <adw_id>     # which phase is still `running`
just procs <adw_id>      # what that phase is actually running, with pids
just kill <adw_id>       # stop it — children first, then the workflow
```

`processes` rows with `ended_at IS NULL` are the live ones. If `procs` shows a pi child but the phase has produced no `tool_call` events and its `raw_output.jsonl` is empty, the agent never got started properly — check the model resolves and that nothing is blocking the subprocess, rather than waiting it out. `just kill` verifies each pid still matches the command that was recorded before signalling, because pids get recycled.

A killed run marks itself `fail` and closes its process rows, so the trace never claims work is in flight that is already dead. **Its worktree survives on purpose** — `sessions.repo_root` and `sessions.branch` say where, and that is where you go to see what it had done so far.

## Where the work went

A run commits to `sssf/<adw_id>` in its own worktree, never to the engineer's branch. So when a chain reports success and `git log` on their branch shows nothing, nothing is wrong — the work is on the run's branch, waiting to be landed:

```bash
just integrate <adw_id>          # merge it, or push it, per worktree.integration
just worktrees                   # every run worktree, and the state of the run that owns it
just worktrees-prune             # reclaim the ones whose run ended cleanly
```

Once a branch has been pushed (`mode: pr`), later commits in the same session go out with it: each commit phase pushes to the branch it already published, and `just integrate <adw_id>` run again updates that pull request rather than opening a second one. So a chain joined onto a reviewed session with `--adw-id` does not leave the PR behind — the commit phase's log line says `pushed: True` and names the pull request.

Integration can decline for a good reason — the engineer's checkout is mid-edit, or the run left uncommitted work in its worktree — and says which in its phase log. That is not a failed run: the commits exist and the branch is kept, so the same command finishes the job once the reason is gone. Report the decline and its reason rather than retrying it.

## Report

Tell the engineer, in order: which chain and which roster you launched (name the config whenever it was not the default), which phase is running now (or which failed), phase statuses in sequence, and for a failure the gate violations or the error verbatim. Remember **every phase defaults to `fail`** — a phase showing `fail` may simply never have completed; `queued` means it never started. Don't dress up a partial run as a success.

For a visual live view, the visualizer app in the skill (`just obs`, or tmux sessions viz-api :4600 + viz-ui :4601) polls this same db — sessions as cards, runs as swim lanes, phases and tool calls drill-in. The sqlite queries above remain the headless equivalent.
