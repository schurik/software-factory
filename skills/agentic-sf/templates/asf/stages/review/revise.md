# Revise

## Variables

### prompt

{{prompt}}

### previous_envelope

{{previous_envelope}}

### context_handoff_dir

{{context_handoff_dir}}

## Task

A reviewer read your build against what was asked and withheld approval.
`previous_envelope` is their review: `blocking` names what must change, and
every `findings` entry with `"met": false` says what is missing and where.
`<context_handoff_dir>/review.md` has the full text.

Close every blocking item and every unmet finding. The request in `prompt`
is unchanged, and the plan, if there is one, is still the spec: this is a
correction of your existing work in the same session, not a new build. Keep
the same paths, do not widen the change, and do not argue with the review in
code comments. Verify the tree still runs before you report, by exit status.

## Report

Respond with ONLY valid JSON matching `BuildOutput` — no prose before or after:

```json
{
  "status": "success",
  "summary": "<one sentence on which findings you closed and how>",
  "changed_files": ["src/server.ts"],
  "artifacts": [],
  "commit_message": "<imperative one-line git subject for the code as it now stands — build and revisions land as one commit>",
  "notes_for_next_agent": "<which blocking item each change addressed>"
}
```

`changed_files` is every file this turn touched. The reviewer reads them next.
