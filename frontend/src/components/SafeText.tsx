// 显示不可信文本（邮件正文、草稿）：React 会转义 HTML，这里再把不可见字符和方向控制符
// 显示成可见的标记，防止它们隐藏或反转内容（#18 第 8 项，#8 的评论）。
// 用 String.raw 构造：格式化工具会把正则字面量中的 \u 转义改写成字符本身，源码中就会出现
// 不可见字符（src/components/SafeText.test.tsx 检查源码中没有这些字符）。
export const INVISIBLE_PATTERN = String.raw`\p{Cf}|\u034F|\u115F|\u1160|\u3164|\uFFA0`;
const INVISIBLE = new RegExp(INVISIBLE_PATTERN, "gu");

export function codepoint(char: string): string {
  return `U+${(char.codePointAt(0) ?? 0).toString(16).toUpperCase().padStart(4, "0")}`;
}

export function SafeText({ text, className }: { text: string; className?: string }) {
  const parts: (string | { char: string; index: number })[] = [];
  let last = 0;
  for (const match of text.matchAll(INVISIBLE)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    parts.push({ char: match[0], index });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return (
    <span className={["safe-text", className].filter(Boolean).join(" ")}>
      {parts.map((part) =>
        typeof part === "string" ? (
          part
        ) : (
          <span
            key={part.index}
            className="invisible-char"
            title={`不可见字符 ${codepoint(part.char)}`}
          >
            ⟨{codepoint(part.char)}⟩
          </span>
        ),
      )}
    </span>
  );
}
