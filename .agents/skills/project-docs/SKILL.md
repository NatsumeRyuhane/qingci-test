---
name: project-docs
description: Where project knowledge is written down and how to keep it current. Covers the decision log, design proposals, and README and AGENTS.md updates. Use when a change alters behaviour, setup or a decision, or when proposing a design.
---

# Keeping the docs current

The project is meant to be maintained by people who never saw the sessions that built it
(docs/decisions.md D-006). Write the knowledge down where they will look for it.

| You… | Update |
|---|---|
| Made or changed a choice that constrains future work (architecture, library, data rule, process) | Add `D-NNN` to `docs/decisions.md`. Supersede old entries, don't edit them. |
| Want to build something out of scope or contentious | Open an issue labelled `proposal`. Once the design settles, write `docs/proposals/NNNN-<slug>.md`. |
| Changed setup, commands or env vars | `README.md` Getting started, `AGENTS.md` §8, `.env.example` |
| Changed workflow or branch rules | `AGENTS.md` (and the GitHub settings, in the same PR) |
| Learned something new about the data | `docs/data.md` |

Docs changes go in the **same PR** as the change they describe.

## Writing style

- Lead with the point. State the decision or fact first, then the reasoning.
- Say *why*. A rule without its reason gets broken or cargo-culted.
- Link sources: issues, PRs, decision IDs.
- Don't write status ("TODO", "next we will…") into docs. Status lives in issues.
- Keep relative links valid. Check they resolve before committing.
