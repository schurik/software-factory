# sssf — Super Simple Software Factory

The Claude Code plugin form of the factory. One skill, `sssf`, that stamps a
deterministic Python control plane into any repository and then operates it.

```
claude plugin marketplace add schurik/software-factory
claude plugin install sssf@sssf
```

Then, from the repo you want the factory in:

```
/sssf:sssf install
```

`skills/sssf/SKILL.md` carries the hard rules and routes each request to one of
nine cookbooks. `skills/sssf/templates/` is exactly what
`skills/sssf/scripts/install.py` stamps.

Full documentation — why the control plane lives in code, the trace schema, the
twelve starter workflows — is in the [repository README](../../README.md).
