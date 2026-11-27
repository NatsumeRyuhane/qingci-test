// 界面上的“当前客服”，写入 Feedback.actor。MVP 中不是身份，不做访问控制（#13）。
// 只保存在本机浏览器中，读写失败（例如隐私模式）时退回默认值。
import { useState } from "react";

const KEY = "copilot.actor";
export const DEFAULT_AGENTS = ["客服A", "客服B", "主管"];

function load(): string {
  try {
    return window.localStorage.getItem(KEY) ?? DEFAULT_AGENTS[0];
  } catch {
    return DEFAULT_AGENTS[0];
  }
}

export function useActor(): [string, (value: string) => void] {
  const [actor, setActor] = useState(load);
  const update = (value: string) => {
    setActor(value);
    try {
      window.localStorage.setItem(KEY, value);
    } catch {
      // 忽略：只是本机的便利设置
    }
  };
  return [actor, update];
}
