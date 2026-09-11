# Implement

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

Implement the work described in `prompt`. If `previous_envelope` is a plan, it is
your spec: follow it, and read every artifact it names before you change anything.
If there is no plan, the prompt is the spec — make the smallest change that
satisfies it, and change nothing it does not ask for.

Verify your work compiles or runs before you report, and judge that by exit
status, never by scanning output for words.

## Report

Respond with ONLY valid JSON matching `BuildOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence describing what you built>",
  "changed_files": ["src/server.ts"],
  "artifacts": [],
  "commit_message": "<imperative one-line git subject for the code you changed — this is what the commit of your work will say>",
  "notes_for_next_agent": "<how to verify this work>"
}
```

`changed_files` is every file you created, edited or deleted, by path from the
repository root. A gate compares it with the tree — a file you changed and did
not list, or listed and did not change, fails it.
