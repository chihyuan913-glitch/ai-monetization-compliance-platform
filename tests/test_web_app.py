# -*- coding: utf-8 -*-
"""
Web UI 與 API 端點整合測試套件 (tests/test_web_app.py)
驗證：
1. /health 健康檢查與版本資訊
2. / 首頁 HTML 渲染與 Impeccable UI 結構
3. /api/dashboard-data 與 /api/embedded-forms-html 完整 20 張表單與財務數據讀取
4. /api/audit-docx 內建退件樣本、滿分樣本與自訂文字之合規審查
5. /api/generate-plan 雙計畫書完稿生成與 100/100 分驗證
6. /api/file-content 與路徑穿越阻斷資安防呆測試
"""

import io
from fastapi.testclient import TestClient
from web_app import app

client = TestClient(app)


def test_health_endpoint():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "ai-monetization-compliance-platform"
    assert data["port"] == 8770


def test_index_html_renders():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "AI 商業變現與公共工程合規總控平台" in resp.text
    assert "tabular-nums" in resp.text


def test_dashboard_data_and_embedded_forms():
    resp = client.get("/api/dashboard-data")
    assert resp.status_code == 200
    data = resp.json()
    assert "summary" in data
    assert len(data["revenue_table"]["rows"]) >= 12
    assert len(data["interview_table"]["rows"]) >= 5
    assert len(data["validation_table"]["rows"]) >= 10

    forms_resp = client.get("/api/embedded-forms-html")
    assert forms_resp.status_code == 200
    forms_data = forms_resp.json()
    assert len(forms_data["sections"]) == 6


def test_audit_docx_samples_and_text():
    # 1. 測試退件缺失樣本
    r1 = client.post("/api/audit-docx", data={"sample_type": "defect_sample"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["is_passed"] is False
    assert d1["health_score"] < 80
    assert len(d1["outdated_warnings"]) >= 1

    # 2. 測試滿分品管完稿樣本
    r2 = client.post("/api/audit-docx", data={"sample_type": "perfect_quality"})
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["is_passed"] is True
    assert d2["health_score"] == 100

    # 3. 測試自訂文字審查
    r3 = client.post(
        "/api/audit-docx",
        data={"custom_text": "本工程依勞工安全衛生法規定辦理，護欄高度75公分。"},
    )
    assert r3.status_code == 200
    d3 = r3.json()
    assert d3["is_passed"] is False
    assert any("職業安全衛生法" in w["suggested_name"] for w in d3["outdated_warnings"])


def test_generate_plan_api():
    resp = client.post(
        "/api/generate-plan",
        json={
            "plan_type": "bundle",
            "project_name": "新北市某示範段公共工程公共基礎建設計畫",
            "owner_org": "新北市政府工務局",
            "supervision_org": "台灣現代工程技術顧問股份有限公司",
            "contractor_org": "甲級聯合營造工程股份有限公司",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert len(data["generated"]) == 2
    assert all(g["health_score"] == 100 for g in data["generated"])


def test_file_preview_and_path_traversal_protection():
    # 正常讀取專案內文件
    ok_resp = client.get("/api/file-content", params={"path": "00_變現總控與決策紀錄.md"})
    assert ok_resp.status_code == 200
    assert "V3.0" in ok_resp.json()["content"]

    # 惡意路徑穿越攔截測試
    bad_resp = client.get("/api/file-content", params={"path": "../../chihyuan_domain_watchdog.py"})
    assert bad_resp.status_code == 403


def test_auto_fix_text_api():
    """測試 V3.5 一鍵自動修復過期法規並躍升至 100 分端點"""
    defect_text = (
        "本工程依據勞工安全衛生法、勞工安全衛生設施規則及民國105年公共工程施工品質管理作業要點辦理，"
        "主管機關為行政院勞工委員會。護欄高度設置75公分，進場前3天送審，品管人員兼任工地主任，保存1年。"
    )
    resp = client.post(
        "/api/auto-fix-text",
        json={"text": defect_text, "append_missing_chapters": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["before_score"] < 50
    assert data["after_score"] == 100
    assert data["is_passed"] is True
    assert len(data["outdated_warnings"]) == 0
    assert len(data["replacements_made"]) >= 6
    assert "職業安全衛生法" in data["fixed_text"]
    assert "90 公分" in data["fixed_text"]

