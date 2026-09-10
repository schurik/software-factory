# Update ADW

Modify an existing ADW chain — add phases, add gates, add a bounded fix loop.

## Add a phase

Insert a `with run.phase(...)` block where it belongs in the sequence. Pick the right `kind`: `agent` for a `ph.call(...)`, `code` for a deterministic step, `engineer` for a human touchpoint. If the new phase names an agent not already in `REQUIRED_AGENTS`, add it there too — otherwise validation passes and the run dies mid-flight instead of at startup.

```python
    with run.phase(PhaseParams(name="scout", kind="agent", owner="scout",
                               description="Locate the code the request touches")) as ph:
        found = ph.call(AgentCall(output_type=ScoutOutput, prompt=prompt))
```

Phase `name` must be unique within the run — that is what the UI keys blocks on. In a loop, suffix it (`f"test_{i}"`).

`description` is **required**, and `PhaseParams` rejects both a blank one and one that merely restates the name. It is the single line of intent the trace, the console, and the UI phase block show, so write what the phase does and why — `"Land the code only now: green suite, approved review"`, not `"Commit build"`.

A code phase does its work in the block body and logs what it did. The commit phase that closes `adw_plan_build.py` and `adw_plan_build_test.py` is the pattern:

```python
    with run.phase(PhaseParams(name="commit", kind="code", owner="git",
                               description="Land the builder's changes, using the message it wrote")) as ph:
        message = build.commit_message or f"sssf({run.adw_id}): {build.summary}"
        ph.log(sha=git_helper.commit_all(run.repo_root, message), message=message)
```

`commit_message` is a field on `PlanOutput`, `BuildOutput`, and `DocumentOutput` that the agent fills in **for its own work product**, so always pair it with a fallback — it defaults to empty. `commit_all` takes the tree first and has no default for it — a run works in its own worktree, so `run.repo_root` is the answer every time. It raises if that tree is not a git repo or nothing changed, which fails the phase rather than committing nothing. A chain that commits more than once (`adw_simple_sdlc.py`) commits each product with its own author's message.

## Remove a phase

Delete the block, drop any now-unused agent from `REQUIRED_AGENTS`, and re-thread the chain: whatever the removed phase produced was probably somebody's `previous=`. Point that call at the surviving upstream envelope.

## Add gates

Gates are callables over the finished envelope — `gate(envelope, run) -> GateReport`, recording one `check(item, ok, note)` per thing they looked at, with violations derived from the failed ones. Compose them per call:

```python
        build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, previous=plan,
                                  gates=[gates.artifacts_exist, gates.diff_matches_claims]))
```

On violations the harness does **not** restart the agent — it sends the violation list back into the **same session** as a correction (pi's `--session-id` creates-or-continues, so the context window is intact), bounded by that phase's `retries`. Every gate result is traced to the `gate_results` table. Exhausting the retries raises `GateFailure` and fails the phase.

Gate claims, not guesses: declared artifacts exist and are non-empty, declared JSON parses, declared changes appear in the diff, declared test commands pass. Never hardcode counts — express quantity as a property of the declared list ("at least one artifact", "ALL declared paths valid"). Plan quality and code taste are not gateable; that is a reviewer agent or a human. New reusable gates go in `adw_modules/gates.py` (`update_modules.md`).

## Add a bounded fix loop

The pattern from `adw_build_test.py` — always bounded by a module-level constant. The runner is a **code** phase, because the command is known; only repairing it needs an agent:

```python
MAX_FIX_LOOPS = 3

    test = None
    for i in range(1, MAX_FIX_LOOPS + 1):
        with run.phase(PhaseParams(name=f"test_{i}", kind="code", owner="quality",
                                   description="Run the suite — a known command, so code runs it")) as ph:
            test = quality.run_tests(run)          # QualityResult, not an envelope
            ph.log(passed=test.passed, artifacts=", ".join(test.artifacts))

        if test.passed:
            break

        with run.phase(PhaseParams(name=f"fix_{i}", kind="agent", owner="builder", retries=1,
                                   description="Repair what the suite reported, from its verbatim output")) as ph:
            previous = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt,
                                         previous=quality.as_envelope(test, "tests"),
                                         gates=[gates.diff_matches_claims]))

    return run.finish(accepted=test is not None and test.passed,
                      reason=f"the suite still failed after {MAX_FIX_LOOPS} fix attempt(s)")
```

`run.finish()` ends every ADW, and it takes the acceptance criterion the phase
statuses cannot express. A test phase that ran a red suite **succeeded** — the
runner did its job — so phases alone would report a green run that never passed
its tests, in the db and the UI as well as the terminal. Pass `accepted=` and
the exit code, the session status, and the banner are decided together.

`quality.as_envelope` is the adapter: a deterministic result shaped as an envelope, so the builder cannot tell it came from code. Wire the real command in `quality.py` first — a block nobody wired up fails with exit 78 and says so, and `just doctor` lists them all before a run starts.

Three distinctions worth keeping straight:

- **Gate retries vs. JSON retries.** `retries` buys extra *gate*-correction rounds. Malformed final JSON is handled separately and always — `JSON_FIX_ATTEMPTS` in `adw_modules/agents.py` (2 by default) re-prompts the same session for a valid object even on a phase with `retries=0`. Raising the phase's `retries` does not buy more JSON attempts, and vice versa.
- **Phase retries vs. fix loops.** `retries=N` on `PhaseParams` re-attempts one agent phase's gate corrections, re-sent into the same session with its context intact. (Code-phase re-execution is not implemented in v1.) A fix loop is a *chain* of phases repeated — different agents, new envelopes each pass.
- **The test phase succeeds when it runs and reports correctly.** A failing suite does not fail that phase; it fails the run, checked at the end. The runner did its job; the code didn't.

## Add a human gate

A gate stops the chain after a phase and hands its artifact to the engineer. It is one call, placed **after** the phase whose work it shows, and `adw_modules/hitl.py` owns the phases it opens (`approve_<gate>`, `<gate>_revise_<n>`), the wait, and the decision file:

```python
    plan_call = AgentCall(output_type=PlanOutput, prompt=prompt,
                          gates=[gates.artifacts_exist, gates.files_non_empty])
    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner",
                               description="Turn the request into an implementable plan")) as ph:
        plan = ph.call(plan_call)
    plan = hitl.gated(run, Gate(name="plan", owner="planner", call=plan_call), plan)
```

`call` is what a **reject** re-sends — the same output type and gates, with the decision as `previous_envelope`, in the same coding-agent session — so the agent revises its own work rather than starting over. Give the gate no `call` when nothing can revise (after a code phase, before `integrate`): it is then approve/abort only, and `paths=[...]` names the subject to show, such as the run's diff. Place a gate in `adw_simple_sdlc` **before** `commit_plan`, so the plan that lands is the approved one.

Whether a placed gate fires is not the ADW's decision: `hitl:` in the config and `--hitl` on the command line are, and the default is off. Every chain that takes `--resume` also takes `--hitl`, and `--hitl every` stops after every agent phase without any gate being placed — so "let me watch each step" needs no code. The gate `name` is what policy keys on; keep it short and stable.

## Keep scripts thin

An ADW is sequencing and acceptance — nothing else. The moment you are writing parsing, subprocess handling, retry mechanics, or a reusable predicate inside `adw_*.py`, it belongs in `adw_modules/`. See `update_modules.md`.
