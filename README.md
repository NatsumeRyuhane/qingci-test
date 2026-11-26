# qingci-test: Japanese support copilot for Chinese CS agents

A copilot that lets **Chinese-speaking customer-support agents** answer **Japanese players'** support
emails for the mobile game 『ぽちゃガチョ！』 (Pochagacho!) quickly, accurately and in the correct
Japanese business register. It also gets better as agents use it.

> **Status:** scaffolding only. No application code yet. The development plan is tracked in
> [GitHub Issues](https://github.com/NatsumeRyuhane/qingci-test/issues). Start with the roadmap issue.

## Background

This started as a practical assignment (original brief, in Chinese: [docs/requirements.md](docs/requirements.md)):

- A game published in Japan has its customer support staffed from China.
- **Goal:** overcome the language barrier and Japanese support etiquette, and use a small knowledge
  base built from past support mail to make replies as **automated, accurate and continuously
  improving** as possible.
- **Input:** a de-identified sample of about 5,950 support emails in about 2,870 threads
  ([docs/data.md](docs/data.md)).
- **Deliverables:** (A) working source with a README and a screen recording, (B) a write-up of the
  technical approach, (C) the log of AI-assisted development, which is the part weighted most heavily.

We treat it as a real, long-lived project: everything a future maintainer needs is written down in
this repository and its issues, not in anyone's chat history.

## Approach (planned)

**Agent copilot with a human in the loop**, not a fully automatic responder
([why](docs/decisions.md#d-001-agent-copilot-not-a-fully-automatic-responder)).

```
Japanese email ──► ingest & clean ──► analyse ─────────────► draft ──────────► route
                                      · zh translation        · retrieve         · auto-send (low risk)
                                      · summary & sentiment     templates and    · agent review
                                      · category & fields       similar cases    · escalate
                                                              · keigo reply
                                                              · zh back-translation
                                                                       │
                       agent edits / approves ◄────────────────────────┘
                                 │
                                 └──► feedback improves the template KB, measured by the offline eval
```

| Phase | Scope |
|---|---|
| **MVP** | (a) ingest and cleaning · (b) template knowledge base · (c) analysis, retrieval, drafting, routing · (d) offline evaluation and agent web UI |
| **Next** | (e) REST API and A2A endpoint for integration · proposals: agentic loop core, identity-based access control |

## Repository layout

```
AGENTS.md          Contributor guide for humans and AI agents (read this first)
CLAUDE.md          → symlink to AGENTS.md (for Claude Code)
.agents/skills/    Shared agent skills (tool-agnostic)
.claude/skills     → symlink to .agents/skills (for Claude Code)
data/              Local-only data directories (contents never committed)
docs/              Requirements, data profile, decisions, proposals
```

## Getting started

1. Clone the repo, then read [AGENTS.md](AGENTS.md).
2. Get the sample workbook from the repository owner and put it in `data/raw/` ([data/README.md](data/README.md)).
3. `cp .env.example .env` and fill in the values.
4. Toolchain and run instructions arrive with the foundation issue (stack decision, skeleton and CI).

## Documentation

| Document | Purpose |
|---|---|
| [AGENTS.md](AGENTS.md) | Workflow, branch protection rules, conventions |
| [docs/requirements.md](docs/requirements.md) | Original assignment brief (Chinese) |
| [docs/data.md](docs/data.md) | Structure and findings for the sample data |
| [docs/decisions.md](docs/decisions.md) | Decision log: what we chose and why |
| [docs/proposals/](docs/proposals/) | Design proposals for post-MVP work |
| [docs/ai-log/](docs/ai-log/) | How the AI collaboration log (deliverable C) is collected |
