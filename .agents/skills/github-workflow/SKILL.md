---
name: github-workflow
description: 如何在本仓库中用 gh CLI 查找、认领和交付工作。涵盖阅读 issue、建分支、开符合仓库规则的 PR，以及获取和回复评审讨论。在开始一项任务、开或更新 PR、回复评审时使用。
---

# 用 `gh` 走 GitHub 工作流

GitHub Issues 和 PR 是唯一的任务跟踪工具（docs/decisions.md D-003）。用 `gh` 读取它们，不要依赖别人在聊天里告诉你的内容。规则本身见 AGENTS.md 第 4–7 节，本技能讲的是具体怎么做。

Issue、PR 和评审回复使用简体中文（AGENTS.md 第 3 节）。

如果在沙箱中运行 `gh` 出现认证错误，可能是沙箱拦截了凭据存储。先请你的人类协作者允许在沙箱外运行，再判断认证是否真的有问题。

## 1. 找工作

```sh
gh issue list --state open --milestone "MVP"                # 计划中的工作
gh issue list --label "area:kb" --assignee ""               # 某个模块中无人认领的工作
gh issue view 1 --comments                                  # 路线图：阶段与顺序
gh issue view <n> --comments                                # 完整阅读一个 issue，包括评论
```

- 确认 issue 的依赖项（列在“依赖”下）已关闭，或其接口已合并。如果没有，在 issue 上说明，不要去猜接口。
- 需要机器可读的输出时：`gh issue view <n> --json title,body,labels,comments,milestone`。
- 认领：`gh issue edit <n> --add-assignee @me`。如果 issue 较大，评论写明你的计划。

## 2. 建分支

```sh
git fetch origin
git switch -c feat/<n>-<slug> --no-track origin/main
```

## 3. 开 PR

push 和开 PR 之前先征得你的人类协作者同意，除非对方已明确表示这项任务可以直接进行。

```sh
git push -u origin HEAD
gh pr create --title "feat(kb): 从历史客服回复构建模板知识库" --body-file pr.md
```

`pr.md` 骨架（用完删除，不要提交）：

```md
Closes #<n>

## 改了什么
<用几条要点描述改动>

## 为什么
<要解决的问题，附 issue 或决策链接>

## 如何测试
<运行的命令及结果>

## 评审要点
<有风险的部分、待讨论的问题、已开的后续 issue>
```

后续工作另开 issue（`gh issue create`），并在“评审要点”中提及。

## 4. 处理评审

等你的人类协作者告诉你评审已完成再处理，不要轮询。

获取未解决的行内评审讨论（`gh pr view --comments` 看不到它们）：

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

- 如果某条讨论的最新评论已经是你的回复，就跳过它，那是在等评审人回应。
- 回复一条讨论：

  ```sh
  gh api graphql -f threadId=<id> -f body='已在 <sha> 中修复：<改了什么>。@<reviewer> 如果确认没问题，请标记为已解决。' -f query='
  mutation($threadId:ID!,$body:String!){
    addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$threadId, body:$body}){ comment{ url } }
  }'
  ```
- 不要自己标记讨论为已解决。唯一的例外是仓库所有者决定某条意见不修改：回复理由后执行

  ```sh
  gh api graphql -f threadId=<id> -f query='mutation($threadId:ID!){ resolveReviewThread(input:{threadId:$threadId}){ thread{ isResolved } } }'
  ```

## 5. 合并之后

```sh
git switch --detach origin/main   # 或者删除你的 worktree：git worktree remove <path>
git branch -d <branch>
```
