# AGENTS.md

The contributor guide for this repository, for **humans and AI coding agents alike**. If you are an
agent, read all of it before your first change. It is short on purpose.

## 1. Project at a glance

- **What:** a copilot that helps Chinese-speaking support agents answer Japanese players' emails
  for the game 『ぽちゃガチョ！』. See [README.md](README.md).
- **Why it's built this way:** [docs/decisions.md](docs/decisions.md). Read it before proposing
  architectural changes.
- **The data:** [docs/data.md](docs/data.md) (what it looks like) and
  [data/README.md](data/README.md) (where it goes and the handling rules).
- **What to work on:** GitHub Issues. Nothing else is a task list (D-003).

Toolchain, run and check commands: **not decided yet.** They will be added to §8 by the foundation issue
([#2](https://github.com/NatsumeRyuhane/qingci-test/issues/2)). Until then there is no build.

## 2. Agent setup: instructions and skills

This repo is tool-agnostic (D-004). The canonical files are:

| What | Canonical location |
|---|---|
| Instructions | `AGENTS.md` (this file) |
| Skills | `.agents/skills/<skill-name>/SKILL.md` (frontmatter `name` and `description`, then instructions) |

Many agents read `AGENTS.md` natively. **If yours reads instructions or skills from somewhere else,
find out where (check your tool's documentation) and link that location to the canonical one with
a symlink.** Never copy the files: copies drift.

Already linked in the repo:

| Tool | Link | Target |
|---|---|---|
| Claude Code | `CLAUDE.md` | `AGENTS.md` |
| Claude Code | `.claude/skills` | `.agents/skills` |

Adding a link for another tool (commit it in a small PR so the next person on that tool gets it):

```sh
# macOS / Linux / WSL. Link targets are relative to the link's own directory.
ln -s AGENTS.md GEMINI.md                    # example: instructions file
mkdir -p .sometool && ln -s ../.agents/skills .sometool/skills   # example: skills dir
```

### Symlinks are best-effort

Symlinks break on some Windows setups. Git then checks each one out as a small text file containing
the target path (for example `CLAUDE.md` contains only `AGENTS.md`). If you see that:

1. Preferred: enable Windows Developer Mode, run `git config core.symlinks true`, then
   `git checkout -- CLAUDE.md .claude/skills` to recreate the real links.
2. Otherwise: ignore the broken links and read `AGENTS.md` and `.agents/skills/` directly at the
   start of every session. **Don't commit** the broken link files or replace them with copies.

### Skills in this repo

| Skill | Use it when |
|---|---|
| [`github-workflow`](.agents/skills/github-workflow/SKILL.md) | Picking up an issue, branching, opening a PR, handling review threads |
| [`sample-data`](.agents/skills/sample-data/SKILL.md) | Reading, quoting or processing the support-mail sample |
| [`project-docs`](.agents/skills/project-docs/SKILL.md) | Recording a decision, writing a proposal, updating docs |

## 3. Ground rules

1. **Write for someone without context** (D-006). Every issue, PR, commit message and doc must make
   sense to a reader who never saw your chat session. Say *why*, not just *what*. Link the issue.
2. **The sample data never enters git** (D-002): not the workbook, not derived files, not large
   excerpts in issues, PRs or logs. Tests use small synthetic fixtures. `*.csv` and `*.xlsx` are
   ignored globally, so a synthetic fixture in those formats needs `git add -f`, and the PR must say
   it is synthetic.
3. **Secrets live in `.env` only** (D-005). Read them from environment variables. Whenever you add a
   variable, add it to `.env.example` with a comment and no value. Never print secrets in logs,
   tests or PR text. If a secret is ever committed, treat it as leaked: rotate it first, then clean
   up.
4. **Stay in scope.** Do what the issue asks. File new issues for anything else you notice.
5. **Ask when it's not your decision.** Architecture, scope and trade-offs belong to the repository
   owner. Raise them on the issue.

## 4. Workflow

```
issue ──► branch off origin/main ──► change → check → commit (repeat) ──► push ──► PR ──► review ──► squash-merge
```

1. **Start from an issue.** Read it in full, including comments, with `gh issue view <n> --comments`.
   If no issue exists, open one first, unless the change is a trivial typo.
   Assign yourself (`gh issue edit <n> --add-assignee @me`) so others know it's taken.
2. **Branch** off `origin/main`: `git fetch && git switch -c <type>/<issue#>-<slug> --no-track origin/main`
   (for example `feat/4-template-kb`). `<type>` is a Conventional Commit type (§6).
3. **Change → check → commit.** Make one focused change, run the checks (§8), commit, then move on.
   Don't build a big diff and split it afterwards: commits split after the fact were never tested.
4. **Before every commit and push**, confirm `git branch --show-current` and `pwd` are what you
   expect, and that the diff contains only your changes.
5. **Open a PR** (§7) once the work is reviewable. A draft PR is fine for early feedback.
6. **Agents:** ask your human before pushing and before opening a PR, unless they have already told
   you to go ahead for this task.

### Parallel work

If the checkout you would use has someone else's uncommitted changes, an operation in progress
(`.git/index.lock`, `MERGE_HEAD`, `rebase-merge/`), or unpushed commits on another branch, **don't
touch it.** Make your own worktree:

```sh
git worktree list
git worktree add ../qingci-test-<slug> -b <type>/<issue#>-<slug> --no-track origin/main
```

Never reuse another person's or agent's branch or worktree. Remove your worktree after merge:
`git worktree remove <path>`.

## 5. Branch protection: rules for `main`

`main` is protected. **These rules bind every contributor, human or agent, whether or not GitHub is
currently enforcing them.** (GitHub Free does not enforce protection on private repositories, see D-002.)

| Rule | What it means for you |
|---|---|
| No direct pushes | All changes reach `main` through a PR. Never `git push origin main`. |
| No force-pushes or deletion | Never rewrite `main`'s history. Force-pushing your own feature branch is fine before review starts. Avoid it once review has begun. |
| PR requires 1 approving review | From the repository owner or a designated reviewer. Authors don't approve their own PRs. |
| Stale approvals dismissed | Pushing new commits after approval requires re-approval. |
| Required checks must pass | CI (lint and tests, defined by the foundation issue [#2](https://github.com/NatsumeRyuhane/qingci-test/issues/2)) must be green before merge. |
| All review threads resolved | By the **reviewer**, not the author (§7). |
| Signed commits required | Sign every commit (SSH or GPG): `git config commit.gpgsign true`. Unsigned commits are rejected. If signing fails, stop and fix it. Don't bypass it. |
| Linear history, squash-merge only | The PR title becomes the squash commit message, so it must be a valid Conventional Commit (§6). |
| Branch up to date before merge | Rebase or merge `origin/main` into your branch if it is behind. |
| Merging | Only the repository owner merges, on GitHub. **Never merge locally** into `main`. |

Repository owners: the GitHub-side configuration that mirrors this table is tracked in the
foundation issue. Keep the two in sync. If you change one, change the other in the same PR.

## 6. Commits

[Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>[(scope)][!]: <description>

[body: why the change was made, what a reviewer should know]

[footers, e.g. Refs: #12]
```

- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `build`, `chore`, `perf`, `ci`, `style`, `revert`.
- Scopes follow the code areas: `ingest`, `kb`, `draft`, `eval`, `ui`, `api`, `a2a`, `auth`, `docs`.
- Breaking change: `!` before the colon, or a `BREAKING CHANGE:` footer.
- One logical change per commit. Every commit must pass the checks on its own.

## 7. Pull requests and review

**A PR must have:**
- A Conventional Commit title (it becomes the squash commit).
- `Closes #<n>` (or `Refs #<n>` for partial work) in the description.
- A description with: **what** changed, **why**, **how it was tested** (commands and results), and
  anything the reviewer should look at closely. Assume the reviewer has not seen your session.
- Docs updated in the same PR when behaviour, setup or decisions change (see `project-docs` skill).
- A small diff. Split large work into a stack of PRs, each reviewable on its own.

**Review threads:**
- Reply to every thread. **Don't resolve threads yourself.** The reviewer decides when their concern
  is settled.
- Fix: make the change, push, and reply with what changed and the commit SHA.
- Disagree: reply with your reasoning and leave the thread open.
- If the owner rules a comment won't-fix, reply with the reason, note it was the owner's decision,
  and then resolve it.
- Unclear feedback or an architectural trade-off: ask the owner before acting.
- `gh pr view --comments` does not show inline threads or whether they're resolved. Use the GraphQL
  query in the `github-workflow` skill.

## 8. Checks and commands

_To be defined by the foundation issue [#2](https://github.com/NatsumeRyuhane/qingci-test/issues/2) (stack, skeleton, CI)._ Until then, check documentation
changes by confirming that relative links resolve.

## 9. Repository map

```
AGENTS.md            this guide (CLAUDE.md → AGENTS.md)
README.md            project overview and getting started
.agents/skills/      shared agent skills (.claude/skills → .agents/skills)
.env.example         every environment variable the app reads, no values
data/                local-only data (contents ignored), see data/README.md
docs/
  requirements.md    original assignment brief (Chinese)
  data.md            sample-data profile and findings
  decisions.md       decision log (D-NNN)
  proposals/         design proposals for post-MVP work
```
