# Scout

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Find the code `prompt` is about, so the plan that follows is written against
this repository and not against a guess.

1. Search for the modules, functions, config and tests the request touches or
   depends on. Read enough of each to say what it does today.
2. Write `<context_handoff_dir>/scout_findings.md`: one section per finding —
   the path, what lives there, why it matters to the request. Close with what
   you did NOT find, if the request assumes something that is not there.
3. Emit your `Report` JSON. `findings` is the same list, one entry per file;
   `artifacts` names the findings file you wrote.

Change nothing in the repository.

## Report

Respond with ONLY valid JSON matching `ScoutOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence on what you found>",
  "findings": [
    { "file": "src/server.ts", "note": "<why this file matters to the request>" }
  ],
  "artifacts": ["<context_handoff_dir>/scout_findings.md"],
  "notes_for_next_agent": "<what the planner should read first, and what is NOT there>"
}
```
