# Sample data profile

Source: `data/raw/脱敏数据2026.2.10.xlsx` (not committed, see [data/README.md](../data/README.md)).
Figures below come from a first exploratory pass on 2026-11-26. Re-verify them once the ingest
pipeline exists, because the cleaning rules will shift some counts.

## Shape

One sheet, 5,950 data rows, three columns:

| Column | Meaning |
|---|---|
| `メールID` | Thread ID (2,872 distinct) |
| `メールID枝番` | Message number within the thread, 1 to 12 |
| `本文` | Message body (plain text, median about 410 chars, max about 5,900, 5 empty) |

Thread lengths: 1 message: 670 · 2: 1,705 · 3: 292 · 4: 130 · 5 or more: 75.
Inquiry dates run from 2024-02 to 2024-09. App versions run from 1.0.x to 1.4.

## Message types

- **Message 1** is almost always the player's initial inquiry, submitted through an in-game form
  with fixed fields:

  ```
  アカウントID / プレイヤー名 / アプリバージョン / ご利用のOSバージョン / ご利用端末名 /
  お問い合わせ内容の種類 / ご利用環境 / 問題が発生した日時 / メールアドレス /
  お問い合わせ内容 / 添付ファイル
  ```

  Many fields are blank. The free text is in `お問い合わせ内容`.
- **Agent replies** (about 2,950) start with `いつもご利用いただきありがとうございます。『ぽちゃガチョ！』サポート担当です。`
- **Player follow-ups** (about 140) are later messages without that greeting, usually with the
  previous mail quoted below.
- 670 threads have no reply at all.

## Inquiry categories (`お問い合わせ内容の種類`)

| Category | Messages | Meaning |
|---|---|---|
| 不具合について | 3,052 | Bug report |
| アイテム購入時に問題が発生した | 867 | Problem when purchasing items |
| ご意見・ご要望 | 807 | Feedback or requests |
| データの引き継ぎについて | 191 | Data transfer between devices |

Platform (`ご利用環境`): iOS 2,969 · Google Play 1,948.
These counts include the forms quoted inside replies, so they over-count threads.

## Key findings

1. **Replies are heavily templated.** After removing quoted text and the fixed greeting and closing,
   the 2,951 agent replies reduce to 820 distinct bodies, and **the 50 most common cover 66%**.
   This is the "small knowledge base": the main job is classifying the inquiry and retrieving the
   right template, with an LLM adapting the wording.
2. **Some templates only apply during one incident.** Examples: an App Store outage on
   3/2 11:00–14:00 that broke purchases, a ranking-reward "構成エラー" error on 3/15, and an
   ad-reward bug that was "still under investigation". These templates are only valid for a time
   window and must not be sent for unrelated problems.
3. **Replies follow a fixed structure:** greeting → apology (when appropriate) → answer → "if the
   problem persists, please contact us again" → `今後とも『ぽちゃガチョ！』をよろしくお願いいたします。`
   → `※本メール内容の無断掲載、無断複製、転送はお控えください。` Chinese agents most often get this
   register wrong, so drafts should follow it.
4. **Quoted history is noisy.** Follow-ups nest `>` quotes in several mail-client formats (Gmail
   `2024年3月29日(金) 16:32 … <>:`, iPhone `2024/03/29 12:18、… のメール:`), sometimes collapsed onto
   one line. These need stripping before analysis.
5. **Some characters were lost.** Symbols such as `・`, `①②③` and some bullets were replaced by `?`
   (for example `ご意見?ご要望`). The cleaning step should repair the common cases.
6. **De-identification left gaps.** Account IDs, player names and email addresses are blank, and
   sender addresses appear as `<>`.
