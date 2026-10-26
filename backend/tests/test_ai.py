import json

import httpx

from factorymind.ai import Advisor, invented_numbers
from factorymind.governance import VirtualFactory
from factorymind.llm import LLMClient, LLMConfig


def fake_model(*replies: str | int | httpx.Response):
    """依序回傳預先寫好的模型輸出;整數代表 HTTP 錯誤碼,也可直接給 Response。"""
    queue, seen = list(replies), []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        reply = queue.pop(0)
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, int):
            return httpx.Response(reply, text="error")
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    config = LLMConfig(api_key="test", model="gemini-test", base_url="https://example.test/v1")
    return Advisor(LLMClient(config, transport=httpx.MockTransport(handler))), seen


def processed(advisor):
    factory = VirtualFactory(advisor=advisor)
    incident = factory.open_incident(factory.source.next(factory.snapshot))
    return factory, factory.process(incident.id)


def test_without_key_the_brief_comes_from_rules():
    factory, incident = processed(None)
    assert factory.state()["ai"]["enabled"] is False
    assert incident.ai_brief.source == "rules"
    assert "P3" in incident.ai_brief.headline


def test_ai_brief_replaces_rule_brief_and_is_traced():
    reply = json.dumps({"headline": "建議核准 P3", "reasons": ["策略客戶全部準時"], "watch": []})
    advisor, seen = fake_model(reply)
    _, incident = processed(advisor)
    assert incident.ai_brief.source == "ai" and incident.ai_brief.model == "gemini-test"
    assert incident.ai_brief.headline == "建議核准 P3"
    assert incident.stage.value == "decide"  # 摘要不推進處理階段
    assert incident.trace[-1].actor == "報告角色"
    assert "P3" in seen[0]["messages"][1]["content"]  # 模型看得到系統算好的方案


def test_invented_numbers_are_sent_back_then_fall_back_to_rules():
    made_up = json.dumps({"headline": "延誤約 3.5 小時", "reasons": ["多花 NT$99,999"], "watch": []})
    advisor, seen = fake_model(made_up, made_up, made_up)
    _, incident = processed(advisor)
    assert len(seen) == 3 and "99999" in seen[1]["messages"][-1]["content"]
    assert incident.ai_brief.source == "rules" and "不合格" in incident.ai_brief.note
    assert "改用規則" in incident.trace[-1].message


def test_short_rate_limit_waits_and_retries(monkeypatch):
    """每分鐘次數超過時 Gemini 會說幾秒後再試;等得不久就自動重送,不讓主管看到錯誤。"""
    waits = []
    monkeypatch.setattr("factorymind.llm.time.sleep", waits.append)
    reply = json.dumps({"headline": "建議核准 P3", "reasons": [], "watch": []})
    advisor, seen = fake_model(httpx.Response(429, text="Please retry in 1.3s."), reply)
    _, incident = processed(advisor)
    assert incident.ai_brief.source == "ai" and len(seen) == 2 and waits == [1.8]


def test_quota_exhausted_is_reported_not_crashing():
    advisor, _ = fake_model(429, 429)
    factory, incident = processed(advisor)
    assert incident.ai_brief.source == "rules" and "額度" in incident.ai_brief.note
    turn = factory.ask(incident.id, "為什麼選 P3?", "王主管")
    assert turn.source == "rules" and "額度" in turn.answer
    assert incident.chat == [turn]


def test_chat_answer_may_repeat_numbers_from_the_question():
    brief = json.dumps({"headline": "建議核准 P3", "reasons": [], "watch": []})
    answer = json.dumps({"answer": "資料沒有加班 45 分鐘的試算,系統只評估了列出的方案。"})
    advisor, seen = fake_model(brief, answer)
    factory, incident = processed(advisor)
    turn = factory.ask(incident.id, "如果加班 45 分鐘呢?", "王主管")
    assert turn.source == "ai" and "45" in turn.answer


def test_number_check():
    facts = "成本 NT$12,400,交期 D1 20:00"
    assert invented_numbers("成本 NT$12,400,第 2 點", facts) == []
    assert invented_numbers("約 13000 元", facts) == ["13000"]


def model_list(*ids: str):
    """模擬供應端的 /models 清單;Gemini 的識別碼前面帶 models/。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})

    config = LLMConfig(api_key="test", model="gemma-4-26b-a4b-it", base_url="https://example.test/v1")
    return LLMClient(config, transport=httpx.MockTransport(handler))


def test_default_model_is_gemma(monkeypatch):
    """規格要求維持 gemma-4-26b-a4b 家族;沒設定模型時不能悄悄換成別的。"""
    from factorymind.llm import LLMConfig

    monkeypatch.delenv("FACTORYMIND_LLM_MODEL", raising=False)
    assert LLMConfig.from_env().model == "gemma-4-26b-a4b-it"


def test_startup_check_records_the_provider_model_id():
    client = model_list("models/gemini-3.5-flash-lite", "models/gemma-4-26b-a4b-it")
    client.verify_model()
    status = client.status()
    assert status["enabled"] is True
    assert status["check"] == "ok" and status["model_id"] == "models/gemma-4-26b-a4b-it"


def test_missing_model_falls_back_to_rules_instead_of_switching():
    client = model_list("models/gemini-3.5-flash-lite")
    client.verify_model()
    status = client.status()
    assert status["enabled"] is False and status["model"] == "gemma-4-26b-a4b-it"
    assert status["check"] == "missing" and "gemma-4-26b-a4b-it" in status["note"]
    _, incident = processed(Advisor(client))
    assert incident.ai_brief.source == "rules"
