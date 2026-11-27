// 待处理队列：未结案的案件，先进先出。底部的案件栏和“处理完自动跳到下一个”都用它。
import { api, type CaseSummary, call } from "../api/client";

export async function fetchQueue(): Promise<{ items: CaseSummary[]; total: number }> {
  return call(
    api.GET("/v1/cases", { params: { query: { actionable: true, sort: "oldest", limit: 200 } } }),
  );
}

/** 队列中下一个要处理的案件：排在最前、且不是当前案件的那个。 */
export async function nextCaseId(currentId: string): Promise<string | null> {
  const queue = await fetchQueue();
  return queue.items.find((item) => item.id !== currentId)?.id ?? null;
}
