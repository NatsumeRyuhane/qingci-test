# Decision log

Short records of decisions that shape the project, newest last. Each entry says what was decided,
why, and what it rules out. To change a decision, add a new entry that supersedes the old one;
don't rewrite history. Discussion happens in issues or PRs, and each entry links to its source.

Template:

```
## D-NNN: <title>
- Date: YYYY-MM-DD · Status: accepted | superseded by D-NNN · Source: <issue/PR link>
- Context: …
- Decision: …
- Consequences: …
```

---

## D-001: Agent copilot, not a fully automatic responder
- Date: 2026-11-26 · Status: accepted · Source: project kickoff
- Context: The brief asks for "maximum automation, accuracy and iteration". Wrong answers on
  purchases, refunds or lost accounts are costly. Agent replies in the sample are highly templated
  (top 50 templates cover 66%, see [data.md](data.md)).
- Decision: Build a copilot. It translates and summarises each inquiry into Chinese, classifies it,
  retrieves templates and similar cases, and drafts a Japanese reply in the correct register with a
  Chinese back-translation. Risk-based routing sends a case automatically only in narrow low-risk
  categories. Everything else goes to a human for review or escalation.
- Consequences: Agent edits are the training signal for iteration. Automation coverage grows by
  widening the auto-send categories as the offline eval shows they are safe.

## D-002: Sample data is never committed; repository is private
- Date: 2026-11-26 · Status: accepted · Source: project kickoff
- Context: The workbook is de-identified but still belongs to the client.
- Decision: Keep raw and derived data out of git (`data/*` is ignored). The GitHub repository is
  private until there is a reason to change that.
- Consequences: Contributors obtain the data from the owner. Tests use small synthetic fixtures.
  GitHub Free does not enforce branch protection on private repositories, so the branch rules in
  AGENTS.md are binding by convention even where GitHub cannot enforce them.

## D-003: GitHub Issues and PRs are the task tracker
- Date: 2026-11-26 · Status: accepted · Source: project kickoff
- Decision: All planned work is a GitHub issue. All changes land through PRs that reference an
  issue. There is no `docs/TODO.md`. Agents read and update work with the `gh` CLI.
- Consequences: The issue tracker is the source of truth for status. Docs describe how things are,
  not what is left to do.

## D-004: Tool-agnostic agent configuration
- Date: 2026-11-26 · Status: accepted · Source: project kickoff
- Context: Contributors may use any AI coding agent, or none.
- Decision: `AGENTS.md` is the single contributor guide. Shared skills live in `.agents/skills/`.
  Tool-specific locations (for example `CLAUDE.md` and `.claude/skills`) are **symlinks** to these,
  never copies. Symlinks are best-effort because they break on some Windows setups, and AGENTS.md
  tells agents how to recover.
- Consequences: There is one place to edit. A tool that can't follow symlinks reads the canonical
  files directly.

## D-005: Secrets only via environment
- Date: 2026-11-26 · Status: accepted · Source: project kickoff
- Decision: Secrets are read from environment variables, loaded locally from `.env` (ignored).
  `.env.example` lists every variable, with no values.

## D-006: Write for a maintainer without context
- Date: 2026-11-26 · Status: accepted · Source: project kickoff
- Context: This began as a solo assignment, but we treat it as a real project that someone else may
  inspect and maintain.
- Decision: Issues, PRs, docs and commit messages must make sense to someone who never saw the chat
  sessions that produced them.
