import { describe, expect, it } from "vitest";
import { caseHref, parseHash } from "./route";

describe("parseHash", () => {
  it.each([
    ["", { page: "inbox" }],
    ["#inbox", { page: "inbox" }],
    ["#metrics", { page: "metrics" }],
    ["#sim", { page: "sim" }],
    ["#case-SIM-1", { page: "case", caseId: "SIM-1" }],
    ["#/cases/1234", { page: "case", caseId: "1234" }],
    ["#case-", { page: "inbox" }],
    ["#unknown", { page: "inbox" }],
  ])("%s", (hash, route) => {
    expect(parseHash(hash)).toEqual(route);
  });

  it("caseHref 与 parseHash 互逆，只用安全字符", () => {
    expect(caseHref("SIM-AB_12")).toBe("#case-SIM-AB_12");
    expect(parseHash(caseHref("SIM-AB_12"))).toEqual({ page: "case", caseId: "SIM-AB_12" });
  });
});
