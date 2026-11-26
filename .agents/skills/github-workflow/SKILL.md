---
name: github-workflow
description: How to find, pick up and deliver work in this repo with the gh CLI. Covers reading issues, branching, opening PRs that meet the repo's rules, and fetching and answering review threads. Use when starting a task, opening or updating a PR, or responding to review.
---

# GitHub workflow with `gh`

GitHub Issues and PRs are the only task tracker (docs/decisions.md D-003). Use `gh` to read them.
Don't rely on what someone told you in chat. The rules themselves are in AGENTS.md §4–§7. This
skill is the how-to.

If `gh` fails with an authentication error inside a sandbox, the sandbox may be blocking the
credential store. Ask your human to allow running it outside the sandbox before concluding that
auth is broken.

## 1. Find work

```sh
gh issue list --state open --milestone "MVP"                # what's planned
gh issue list --label "area:kb" --assignee ""               # unclaimed work in one area
gh issue view 1 --comments                                  # the roadmap: phases and order
gh issue view <n> --comments                                # read an issue in FULL, comments included
```

- Check that the issue's dependencies (listed under "Depends on") are closed or that their
  interfaces are merged. If they aren't, say so on the issue rather than guessing at the interface.
- For machine-readable output: `gh issue view <n> --json title,body,labels,comments,milestone`.
- Claim it: `gh issue edit <n> --add-assignee @me`, and comment with your plan if the issue is large.

## 2. Branch

```sh
git fetch origin
git switch -c feat/<n>-<slug> --no-track origin/main
```

## 3. Open the PR

Ask your human before pushing and opening the PR, unless they've already approved it for this task.

```sh
git push -u origin HEAD
gh pr create --title "feat(kb): build template knowledge base from agent replies" --body-file pr.md
```

`pr.md` skeleton (delete it afterwards, don't commit it):

```md
Closes #<n>

## What
<the change, in a few bullets>

## Why
<the problem, linking the issue or decision>

## How it was tested
<commands run and their results>

## Review notes
<risky parts, open questions, follow-up issues filed>
```

Link follow-up work as new issues (`gh issue create`) and mention them under Review notes.

## 4. Handle review

Wait until your human says reviews are in. Don't poll.

Fetch unresolved inline threads (`gh pr view --comments` doesn't show them):

```sh
gh api graphql -f owner=NatsumeRyuhane -f repo=qingci-test -F pr=<n> -f query='
query($owner:String!,$repo:String!,$pr:Int!){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$pr){
      reviewThreads(first:100){ nodes{
        id isResolved path line
        comments(first:50){ nodes{ author{login} body createdAt } }
      }}
    }
  }
}' --jq '.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved==false)'
```

- Skip threads where your reply is already the latest comment: you're waiting on the reviewer there.
- Reply to a thread:

  ```sh
  gh api graphql -f threadId=<id> -f body='Fixed in <sha>: <what changed>. @<reviewer>, please resolve if this addresses it.' -f query='
  mutation($threadId:ID!,$body:String!){
    addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId, body:$body}){ comment{ url } }
  }'
  ```
- Don't resolve threads yourself. The one exception is when the owner rules a comment won't-fix:
  reply with the reason, then

  ```sh
  gh api graphql -f threadId=<id> -f query='mutation($threadId:ID!){ resolveReviewThread(input:{threadId:$threadId}){ thread{ isResolved } } }'
  ```

## 5. After merge

```sh
git switch --detach origin/main   # or remove your worktree: git worktree remove <path>
git branch -d <branch>
```
