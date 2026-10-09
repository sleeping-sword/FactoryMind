import demo from "@/data/demo.json";
import type { DemoRun } from "./types";

// 目前讀後端匯出的示範資料(python -m factorymind.demo --json);API 完成後改成向後端取資料。
export async function loadRun(): Promise<DemoRun> {
  return demo as unknown as DemoRun;
}
