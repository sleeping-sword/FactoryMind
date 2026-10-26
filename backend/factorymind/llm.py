"""語言模型的唯一出入口。其他模組不直接碰供應商的 API。

走 OpenAI 相容的 chat completions 介面:競賽用 Google Gemini(免費額度),落地改成廠內 vLLM 時
只要換 FACTORYMIND_LLM_BASE_URL 與模型名稱,程式不變。

環境變數(可寫在 backend/.env):
- GEMINI_API_KEY 或 FACTORYMIND_LLM_API_KEY:金鑰;沒有金鑰時 AI 關閉,全部改用規則產生的文字
- FACTORYMIND_LLM_MODEL:模型名稱,預設 gemma-4-26b-a4b-it(競賽指定的模型家族)。啟動時向供應端查一次
  實際識別碼;供應端沒有這個模型就改用規則文字並標明原因,不自動換成別的模型
- FACTORYMIND_LLM_TIMEOUT_S:每次呼叫的等待上限,預設 60 秒(Gemini 免費版排隊時常要 15–25 秒)
- FACTORYMIND_LLM_BASE_URL:預設 Gemini 的 OpenAI 相容端點
- FACTORYMIND_LLM=off:強制關閉(測試用)

模型輸出一律要求 JSON,經 pydantic 驗證後才交出去;驗證失敗做有界重試,仍失敗就丟 LLMError,
由呼叫端退回規則基準。
"""

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_MODEL = "gemma-4-26b-a4b-it"
# 免費額度每分鐘 15 次,超過時 Gemini 會說要等幾秒;等得不久就自動重送
_RETRY_IN = re.compile(r"retry in ([\d.]+)s")
_MAX_WAIT_S = 10

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """模型不可用或輸出不合格;訊息給主管看。"""


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    model: str
    base_url: str
    timeout_s: float = 60
    enabled: bool = True
    json_mode: bool = True

    @property
    def provider(self) -> str:
        return "Gemini" if "generativelanguage.googleapis.com" in self.base_url else "OpenAI 相容端點"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        key = os.environ.get("FACTORYMIND_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY") or ""
        base_url = os.environ.get("FACTORYMIND_LLM_BASE_URL") or GEMINI_OPENAI_URL
        # 地端 vLLM 可以不設金鑰;雲端一定要有
        enabled = os.environ.get("FACTORYMIND_LLM", "on").lower() != "off" and bool(key or base_url != GEMINI_OPENAI_URL)
        model = os.environ.get("FACTORYMIND_LLM_MODEL") or DEFAULT_MODEL
        # Gemma 在 JSON 模式下會把思考過程寫進回覆、拖到逾時;改靠提示要求 JSON
        json_mode = os.environ.get("FACTORYMIND_LLM_JSON_MODE", "off" if model.startswith("gemma") else "on").lower() != "off"
        return cls(
            api_key=key,
            model=model,
            json_mode=json_mode,
            base_url=base_url.rstrip("/"),
            timeout_s=float(os.environ.get("FACTORYMIND_LLM_TIMEOUT_S", 60)),
            enabled=enabled,
        )


class LLMClient:
    def __init__(self, config: LLMConfig | None = None, transport: httpx.BaseTransport | None = None):
        self.config = config or LLMConfig.from_env()
        self._transport = transport
        # 啟動檢查:unchecked → ok(查到識別碼)/ missing(供應端沒有)/ error(查不到,照常嘗試)
        self.check, self.model_id, self.note = "unchecked", None, None

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.check != "missing"

    def status(self) -> dict:
        return {
            "enabled": self.enabled, "provider": self.config.provider, "model": self.config.model,
            "check": self.check, "model_id": self.model_id, "note": self.note,
        }

    def verify_model(self) -> None:
        """向供應端查模型清單,記下實際識別碼;Gemini 的識別碼前面帶 models/。"""
        if not self.config.enabled:
            return
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        try:
            with httpx.Client(timeout=min(self.config.timeout_s, 10), transport=self._transport) as client:
                response = client.get(f"{self.config.base_url}/models", headers=headers)
            ids = [m["id"] for m in response.json()["data"]] if response.status_code == 200 else None
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            ids = None
        if ids is None:
            self.check, self.note = "error", "啟動時查不到供應端的模型清單,呼叫時才知道能不能用"
            return
        self.model_id = next((i for i in ids if i == self.config.model or i.rsplit("/", 1)[-1] == self.config.model), None)
        if self.model_id:
            self.check, self.note = "ok", None
        else:
            self.check, self.note = "missing", f"供應端沒有模型 {self.config.model},改用規則摘要"

    def structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        check: Callable[[T], str | None] | None = None,
        retries: int = 2,
    ) -> T:
        """要求模型回傳符合 schema 的 JSON。check 回傳錯誤訊息時視為不合格,帶著原因重試。"""
        if not self.enabled:
            raise LLMError("尚未設定 AI 金鑰")
        messages = [
            {"role": "system", "content": f"{system}\n\n只輸出一個 JSON 物件,符合這個 JSON Schema:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"},
            {"role": "user", "content": user},
        ]
        problem = "未知錯誤"
        for _ in range(retries + 1):
            content = self._complete(messages)
            try:
                result = schema.model_validate_json(_strip_fence(content))
            except ValidationError as exc:
                problem = f"格式不符:{exc.errors()[0]['msg']}"
            else:
                problem = check(result) if check else None
                if problem is None:
                    return result
            messages += [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"上一個回答不合格({problem})。請修正後重新輸出完整 JSON。"},
            ]
        raise LLMError(f"AI 輸出不合格:{problem}")

    def _complete(self, messages: list[dict]) -> str:
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}
        # Gemma 的思考過程也算在輸出上限內,給太少會想完就被截斷、JSON 一個字都沒寫
        body = {"model": self.config.model, "messages": messages, "temperature": 0.2, "max_tokens": 4096}
        if self.config.json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(timeout=self.config.timeout_s, transport=self._transport) as client:
                url = f"{self.config.base_url}/chat/completions"
                response = client.post(url, json=body, headers=headers)
                for wait_s in (1, 3):  # 供應商偶發的 5xx 或短暫超過額度,稍等再試
                    if response.status_code == 429:
                        wait = _RETRY_IN.search(response.text)
                        if not wait or float(wait.group(1)) > _MAX_WAIT_S:
                            break
                        wait_s = float(wait.group(1)) + 0.5
                    elif response.status_code < 500:
                        break
                    time.sleep(wait_s)
                    response = client.post(url, json=body, headers=headers)
                if response.status_code == 400 and "response_format" in response.text:
                    # 不支援 JSON 模式的端點:改靠提示要求 JSON,仍由 schema 驗證把關
                    body.pop("response_format")
                    response = client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise LLMError("AI 回應太慢,請稍後再試") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"連不到 AI 服務({type(exc).__name__})") from exc
        if response.status_code == 429:
            raise LLMError("AI 免費額度暫時用完,請稍後再試")
        if response.status_code in (401, 403):
            raise LLMError("AI 金鑰無效或沒有權限")
        if response.status_code >= 500:  # 供應商內部錯誤的原文對主管沒有用
            raise LLMError("AI 服務暫時出錯,請稍後再試")
        if response.status_code >= 400:
            raise LLMError(f"AI 服務回應 {response.status_code}:{response.text[:200]}")
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"] or ""
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError("AI 服務回應格式不明") from exc
        if choice.get("finish_reason") == "length" and not _strip_fence(content):
            raise LLMError("AI 回答太長被截斷")
        return content


def _strip_fence(text: str) -> str:
    """有些模型會先寫思考過程,或把 JSON 包在 ```json 區塊裡。"""
    text = text.strip()
    if "</thought>" in text:
        text = text.rsplit("</thought>", 1)[1].strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
