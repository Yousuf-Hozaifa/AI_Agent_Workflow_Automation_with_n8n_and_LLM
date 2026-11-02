import json, os
os.environ["DB_PATH"] = "/tmp/test_leads.db"
os.environ["ADMIN_TOKEN"] = "secret"
os.environ["GROQ_API_KEY"] = "test"
from fastapi.testclient import TestClient
import main

def fake_llm_factory(cls_json):
    async def fake(system, user, max_tokens=400, json_mode=False):
        return cls_json if json_mode else "Thanks! The Team at Mysoft Heaven (BD) Ltd."
    return fake

def run(cls_json, body, headers=None):
    main._hits.clear()
    main.call_llm = fake_llm_factory(cls_json)
    with TestClient(main.app) as c:
        return c, c.post("/leads", json=body)

def test_sales_ok():
    c, r = run(json.dumps({"intent": "sales", "confidence": 0.9, "name": "Ana", "company": "X", "requirement": "demo", "email": None, "phone": None}),
               {"message": "I want a demo", "sender_email": "a@b.co"})
    assert r.status_code == 200 and r.json()["intent"] == "Sales"
    lid = r.json()["lead_id"]
    assert c.get("/leads").status_code == 401
    assert c.get(f"/leads/{lid}", headers={"X-Admin-Token": "secret"}).json()["extracted"]["email"] == "a@b.co"

def test_bad_json_falls_back():
    _, r = run("not json", {"message": "hello"})
    assert r.status_code == 200 and r.json()["intent"] == "Unknown" and r.json()["status"] == "needs_review"

def test_validation():
    _, r = run("{}", {"message": "   "}); assert r.status_code == 422
    _, r = run("{}", {"message": "hi", "sender_email": "nope"}); assert r.status_code == 422

def test_rate_limit_and_health():
    main._hits.clear(); main.call_llm = fake_llm_factory("{}")
    with TestClient(main.app) as c:
        codes = [c.post("/leads", json={"message": "hi"}).status_code for _ in range(12)]
        assert 429 in codes
        assert "groq_key_hint" not in c.get("/health").json()
