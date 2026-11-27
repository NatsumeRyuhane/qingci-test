// 中日文逐段对照（像对比工具）：每一段中文和对应的日语在同一行，行高取两边较高的一边，
// 两栏在同一个滚动区域中，所以永远对齐、等高。每段的输入框随内容自动增高，不出现内部滚动条。
import {
  type KeyboardEvent,
  type ReactNode,
  type TextareaHTMLAttributes,
  useLayoutEffect,
  useRef,
} from "react";

function AutoTextarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const { value } = props;
  // 按内容计算最小高度（内容变化时重新计算）；实际高度由同一行中较高的一边决定（两边等高）
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || value === undefined) return;
    const measure = () => {
      const height = el.style.height;
      el.style.height = "auto";
      el.style.minHeight = "0px";
      const content = el.scrollHeight;
      el.style.height = height;
      el.style.minHeight = `${content}px`;
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth !== width) {
        width = el.clientWidth;
        measure();
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [value]);
  return <textarea ref={ref} rows={1} {...props} />;
}

export function ParagraphPairs({
  zh,
  ja,
  onZhChange,
  onJaChange,
  onZhBlur,
  onJaBlur,
  onZhKeyDown,
  jaLocked,
  onJaLockedAttempt,
  jaClassName,
  zhHeader,
  jaHeader,
}: {
  zh: string[];
  ja: string[];
  onZhChange: (index: number, value: string) => void;
  onJaChange: (index: number, value: string) => void;
  onZhBlur: () => void;
  onJaBlur: () => void;
  onZhKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  jaLocked: boolean;
  onJaLockedAttempt: () => void;
  jaClassName?: string;
  zhHeader: ReactNode;
  jaHeader: ReactNode;
}) {
  const rows = Math.max(zh.length, ja.length);
  return (
    <div className="pair-view">
      <div className="pair-head">
        <div className="pane-title">{zhHeader}</div>
        <div className="pane-title">{jaHeader}</div>
      </div>
      {zh.length !== ja.length && (
        <p className="notice notice-warn pair-mismatch">
          中文 {zh.length} 段、日语 {ja.length} 段，逐段对照可能错位。翻译后会重新对齐。
        </p>
      )}
      <div className="pair-grid">
        {Array.from({ length: rows }, (_, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 段落按位置对照，位置就是身份
          <div className="pair-row" key={i}>
            <div className="pair-cell">
              {i < zh.length ? (
                <AutoTextarea
                  aria-label={`中文第 ${i + 1} 段`}
                  value={zh[i]}
                  onChange={(e) => onZhChange(i, e.target.value)}
                  onBlur={onZhBlur}
                  onKeyDown={onZhKeyDown}
                />
              ) : (
                <div className="pair-empty" />
              )}
            </div>
            <div className="pair-cell">
              {i < ja.length ? (
                <AutoTextarea
                  aria-label={`日语第 ${i + 1} 段`}
                  lang="ja"
                  value={ja[i]}
                  readOnly={jaLocked}
                  className={[jaLocked ? "locked" : "", jaClassName ?? ""]
                    .filter(Boolean)
                    .join(" ")}
                  onClick={() => jaLocked && onJaLockedAttempt()}
                  onKeyDown={(e) => {
                    if (jaLocked && e.key.length === 1) onJaLockedAttempt();
                  }}
                  onChange={(e) => onJaChange(i, e.target.value)}
                  onBlur={onJaBlur}
                />
              ) : (
                <div className="pair-empty" />
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
