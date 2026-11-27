// 按段落（空行分隔）拆分和拼回文本，用于中日文逐段对照。拼回时使用原来的分隔符，
// 没有修改时与原文完全相同（不会被当作修改）。
export type Paragraphs = { blocks: string[]; separators: string[] };

const SEPARATOR = /(\n[ \t　]*\n\s*)/;

export function splitParagraphs(text: string): Paragraphs {
  const parts = text.split(SEPARATOR);
  const blocks: string[] = [];
  const separators: string[] = [];
  parts.forEach((part, i) => {
    if (i % 2 === 0) blocks.push(part);
    else separators.push(part);
  });
  return { blocks, separators };
}

export function joinParagraphs({ blocks, separators }: Paragraphs): string {
  return blocks
    .map((block, i) => (i === 0 ? block : (separators[i - 1] ?? "\n\n") + block))
    .join("");
}

export function replaceParagraph(paragraphs: Paragraphs, index: number, value: string): Paragraphs {
  const blocks = [...paragraphs.blocks];
  blocks[index] = value;
  return { blocks, separators: paragraphs.separators };
}
