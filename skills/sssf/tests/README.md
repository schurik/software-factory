# The factory's own tests

```
pytest                      # from the repository root; configured in pyproject.toml
ruff check .
```

Nothing here calls a model, opens a socket, or needs a token. It runs in about
twelve seconds.

## Why this exists

The factory stamps ~26k lines of Python into other people's repositories, and
every bug in its recent history was found the expensive way — by paying an agent
to walk a real chain, slowly and non-deterministically, and noticing afterwards:

| commit | what it was |
|---|---|
| `c76a372` | a declared artifact path that escaped the run's worktree, and passed its gate |
| `3c03603` | `gh pr create` with neither `--fill` nor `--title` |
| `5368a8a` | the trace db opened read-only, so it could not create its WAL sidecars |
| `830ae4d` / `fd1a58f` | the documenter escaping `app_docs/` |

Every one of those is a unit test.

## The two layers

**Unit tests** over the parts that are pure: `gates`, `permissions`,
`data_types`, `agents.load_config`/`validate`, `limits`, `artifacts`. They use a
real `git init` and real subprocesses where the mechanism IS git or a child
process — a mocked `git diff` would test the mock, and `limits.Deadline` exists
precisely because a child cannot be trusted to cooperate.

**`test_e2e_fake.py`** runs whole ADW chains on
[`adw_modules/harnesses/fake.py`](../templates/adws/adw_modules/harnesses/fake.py),
a harness that answers from a script. Real `session.ensure`, real worktree, real
`Tracer` writing real sqlite, real session directory — only the coding agent is
scripted, because it is the only part that costs money and does not repeat. That
buys coverage of the parts no unit test reaches: the gate-correction loop, the
JSON re-prompt, the permission rollback mid-phase, a budget ceiling stopping the
NEXT turn, a resumed run replaying instead of paying again.

The same harness is a development tool: put a new ADW's roster on `harness: fake`
until the chain's shape holds, then move it back. See
[references/harnesses.md](../references/harnesses.md#the-fake-harness-a-scripted-stand-in).

## Two rules this suite keeps

**The factory never reads the trace db.** `tracer.py` writes it; every question
is answered from the session's own directory through `adw_modules/artifacts.py`.
`test_no_db_reads.py` asserts that structurally over the shipped source, and
`test_a_run_works_with_the_trace_db_deleted` proves the consequence. Tests may
read the db to *assert* — that is not a production path — and they do.

**A test that cannot fail is not a test.** Every claim here was checked by
breaking the line it covers and watching the suite go red. The `limits`
mutation is the instructive one: removing the clock from `Deadline.lines()`
makes the suite *hang* rather than fail, which is exactly the failure the module
exists to prevent.

## Layout

| file | covers |
|---|---|
| `conftest.py` | the git-repo fixture, roster helpers, a recording tracer |
| `test_gates.py` | `_in_tree` escapes, `artifacts_exist`, `verdict_consistent`, `tests_pass` |
| `test_permissions.py` | the write boundary: globs, `protected_files`, rollback, what is never discarded |
| `test_data_types.py` | rule 7's phase descriptions, envelope parsing, usage arithmetic |
| `test_agents_config.py` | the defaults merge (key by key, per harness), `validate` collecting every problem |
| `test_limits.py` | `Deadline` against real children, `overrun` |
| `test_artifacts.py` | the session record: provenance and spend across processes, replay eligibility, phase numbering |
| `test_fake_harness.py` | the harness contract, for all three, and the fake's own behaviour |
| `test_e2e_fake.py` | whole chains |
| `test_no_db_reads.py` | the invariant above |
| `test_install.py` | `install.py` against a scratch repo |

## What is not covered

Named rather than implied — see the pull request that added this suite:
`integration.py`, `pull_requests.py`, `issues.py` and `git_helper.py` (they
shell out to `gh`/`git` against a live forge), `tracer.py`'s schema, the two
watchers, `worktree.py` beyond what an end-to-end run exercises, and the
visualizer.
