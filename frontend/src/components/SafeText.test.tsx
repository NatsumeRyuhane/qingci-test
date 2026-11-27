import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { INVISIBLE_PATTERN, SafeText } from "./SafeText";

// 用 fromCodePoint 构造不可见字符：源码中不能出现它们（见最后一个测试）。
const ch = (code: number) => String.fromCodePoint(code);

describe("SafeText", () => {
  it("把不可见字符和方向控制符显示为标记，HTML 只作为文本", () => {
    const text = `a${ch(0x200b)}b${ch(0x202e)}c<script>alert(1)</script>${ch(0x3164)}`;
    const { container } = render(<SafeText text={text} />);

    expect(container.textContent).toBe("a⟨U+200B⟩b⟨U+202E⟩c<script>alert(1)</script>⟨U+3164⟩");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelectorAll(".invisible-char")).toHaveLength(3);
  });
});

describe("前端源码", () => {
  it("不含不可见字符和方向控制符（需要时用转义或 String.fromCodePoint）", () => {
    const sources = import.meta.glob(["../**/*.{ts,tsx}", "!../api/schema.d.ts"], {
      query: "?raw",
      import: "default",
      eager: true,
    }) as Record<string, string>;
    const pattern = new RegExp(INVISIBLE_PATTERN, "u");
    const found = Object.keys(sources).filter((path) => pattern.test(sources[path]));

    expect(Object.keys(sources).length).toBeGreaterThan(5);
    expect(found).toEqual([]);
  });
});
