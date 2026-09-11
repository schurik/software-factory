# Fix

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

The repository's own checks ran against your build and came back red.
`previous_envelope` holds their verdict: `failures` is the command, its exit
code and what it printed, verbatim. That output is the spec for this turn —
trust it over any summary, including your own from the build.

Address every reported failure, not the first one. Do not widen the change:
the request in `prompt` is unchanged, and a failing check is a bug in how it
was met, not a new ask. Re-run the same command yourself before you report,
and judge it by exit status.

## Report

Respond with ONLY valid JSON matching `BuildOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence on what was wrong and what you changed>",
  "changed_files": ["src/server.ts"],
  "artifacts": [],
  "commit_message": "<imperative one-line git subject for the code as it now stands — the build plus this fix land as one commit>",
  "notes_for_next_agent": "<which failure each change addressed>"
}
```

`changed_files` is every file this turn touched. The commit lands your build
and every fix together, so the message describes the whole change, not the
repair alone.
