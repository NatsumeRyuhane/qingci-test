---
name: sample-data
description: Rules and practical notes for the Japanese support-mail sample (data/raw/*.xlsx). Covers where it lives, its structure, the parsing pitfalls, and what may and may not be quoted or committed. Use before reading, processing, quoting or writing tests against the sample data.
---

# Working with the sample data

## Rules (non-negotiable)

1. The workbook and anything derived from it **never go into git.** `data/` contents, `*.xlsx` and
   `*.csv` are ignored. Don't force-add them.
2. In issues, PRs, commit messages, docs and logs, quote **at most a few lines**, and only when it
   helps the reader.
3. The configured LLM provider may receive the data, because that's the product. No other external
   service may: no pastebins, public notebooks, or uploads to other tools.
4. Tests use **small synthetic fixtures** written to mimic the format (see below), never real rows.
   Mark them as synthetic in the file and the PR.

If the file is missing, it is not in the repo by design. Ask your human for it and point them to
data/README.md. Don't search the internet for it.

## Where and what

- Path: `data/raw/脱敏数据2026.2.10.xlsx` (also in `.env` as `SAMPLE_DATA_PATH`).
- One sheet, columns `メールID` (thread), `メールID枝番` (message number), `本文` (body).
- Full profile and findings: docs/data.md. Read it rather than re-deriving them.

## Pitfalls

- **Message type isn't a column.** An agent reply starts with
  `いつもご利用いただきありがとうございます。『ぽちゃガチョ！』サポート担当です。`. Message 1 is the
  player's form submission. Later non-agent messages are player follow-ups.
- **Quoted history**: lines starting with `>` (nested `>>`), introduced by headers like
  `2024年3月29日(金) 16:32 … <>:` or `2024/03/29 12:18、… のメール:`. Sometimes the quote is
  collapsed onto one long line. Strip it before analysing the new content.
- **Form fields** are `key : value` lines (the content field uses a full-width `：`). Many are empty.
  The category values are 不具合について, アイテム購入時に問題が発生した, ご意見・ご要望 and
  データの引き継ぎについて.
- **Lost characters**: `・`, `①②③`, and some bullets show up as `?`. Don't treat `?` as a
  question mark without context.
- **Full-width vs half-width** characters and `　` spaces are common. Normalize (NFKC) for
  matching only. Keep the original text for display and drafting.
- **Leading blank lines** are common in message 1.

## Synthetic fixture example

```text
# SYNTHETIC: not from the client data
いつもご利用いただきありがとうございます。
『ぽちゃガチョ！』サポート担当です。

お問い合わせいただきました件につきまして、ご案内いたします。
（テスト用の架空の返信本文）

今後とも『ぽちゃガチョ！』をよろしくお願いいたします。
```
