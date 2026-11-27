import { describe, expect, it } from "vitest";
import { joinParagraphs, replaceParagraph, splitParagraphs } from "./paragraphs";

describe("paragraphs", () => {
  it.each([
    "",
    "一段だけ",
    "一\n\n二",
    "一\n\n\n二\n \n三",
    "一\r\n二\n\n三\n",
    "挨拶\n署名\n\n本文\n\n今後とも\n※注意",
  ])("拆分后拼回与原文完全相同：%j", (text) => {
    expect(joinParagraphs(splitParagraphs(text))).toBe(text);
  });

  it("按空行拆段，段内换行保留", () => {
    expect(splitParagraphs("挨拶\n署名\n\n本文").blocks).toEqual(["挨拶\n署名", "本文"]);
  });

  it("替换一段时保留原来的分隔符", () => {
    const parts = splitParagraphs("一\n\n\n二\n\n三");
    expect(joinParagraphs(replaceParagraph(parts, 1, "弐"))).toBe("一\n\n\n弐\n\n三");
  });
});
