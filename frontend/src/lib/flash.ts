// 跳到下一个案件后显示一次的提示（例如“已发送 SIM-1”）。只在内存中保存。
let pending: string | null = null;

export function setFlash(message: string) {
  pending = message;
}

export function takeFlash(): string | null {
  const message = pending;
  pending = null;
  return message;
}
