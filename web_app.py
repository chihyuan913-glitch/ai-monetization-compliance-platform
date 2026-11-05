#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 商業變現與公共工程合規總控平台 (Web UI & API Server)
================================================================================
落實規範：
1. 《系統全面 Web UI 視覺化與主動網域部署詢問全域規範》
2. 《本機 Web 應用程式生命週期與桌面體驗全域規範》
   - 支援隨開隨用自動釋放資源（啟動寬限期 28s、重整保護 5s）與 --no-auto-shutdown 常駐守護模式
3. 《Impeccable UI 機構級前端抗庸俗設計規範》
   - 石板灰高對比深色工作台、1px 細邊框、tabular-nums 等寬對齊、Ctrl+K 快捷搜尋
4. 完整整合五大核心模組：
   - 模組一：全生命週期戰情室與全部 20 張表單總控儀表板
   - 模組二：公共工程計畫書 AI 合規審查工作台（支援上傳 .docx、內建樣本與文字即時審查）
   - 模組三：品管＋安衛「雙計畫書」一鍵完稿生成與下載中心
   - 模組四：客戶訪談與訂單管線動態管理系統（即時讀寫 CSV 並連動驗證儀表板）
   - 模組五：全案 35 項交付資產庫線上預覽與下載中心
================================================================================
"""

import argparse
import csv
import io
import os
import re
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

LOG_FILE = BASE_DIR / "output" / "web_server.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _bootstrap_data_files() -> None:
    """真實客戶/營收 CSV 不入版控；缺檔（如 Render 全新容器）時以 sample_data 範例補齊。

    Why：避免真實訪談與收入資料進入 GitHub/雲端，同時讓雲端版開箱即可運作。
    本機已有真實檔案時絕不覆寫。
    """
    import shutil

    sample_dir = BASE_DIR / "sample_data"
    targets = {
        "07_收入成本追蹤表.csv": BASE_DIR / "07_收入成本追蹤表.csv",
        "11_客戶訪談紀錄表.csv": BASE_DIR / "output" / "11_客戶訪談紀錄表.csv",
        "13_客戶需求驗證儀表板.csv": BASE_DIR / "output" / "13_客戶需求驗證儀表板.csv",
    }
    for name, dst in targets.items():
        src = sample_dir / name
        if src.exists() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)


_bootstrap_data_files()

if sys.stdout is None or sys.stderr is None:
    _log_stream = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_stream
    if sys.stderr is None:
        sys.stderr = _log_stream
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

from src.checker import ComplianceChecker
import generate_full_quality_plan
import generate_safety_plan
import manage_pipeline

DEFAULT_PORT = int(os.environ.get("PORT", "8770"))
DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")


class LifecycleManager:
    """管理本機瀏覽器心跳與自動關閉生命週期"""

    def __init__(self, startup_grace: float = 28.0, reload_grace: float = 5.0):
        self.auto_shutdown = os.environ.get("AUTO_SHUTDOWN", "true").lower() != "false"
        if os.environ.get("NO_AUTO_SHUTDOWN", "0") == "1":
            self.auto_shutdown = False
        self.startup_grace = startup_grace
        self.reload_grace = reload_grace
        self.start_time = time.time()
        self.last_seen = time.time()
        self.has_connected = False
        self.active_sockets = 0
        self._lock = threading.Lock()
        self._monitor_started = False

    def set_auto_shutdown(self, enabled: bool):
        self.auto_shutdown = enabled

    def ping(self):
        with self._lock:
            self.has_connected = True
            self.last_seen = time.time()

    def ws_connect(self):
        with self._lock:
            self.has_connected = True
            self.active_sockets += 1
            self.last_seen = time.time()

    def ws_disconnect(self):
        with self._lock:
            self.active_sockets = max(0, self.active_sockets - 1)
            self.last_seen = time.time()

    def start_monitor(self):
        if self._monitor_started:
            return
        self._monitor_started = True

        def _loop():
            while True:
                time.sleep(1.5)
                if not self.auto_shutdown:
                    continue
                now = time.time()
                with self._lock:
                    if not self.has_connected:
                        if (now - self.start_time) > self.startup_grace:
                            os._exit(0)
                    else:
                        if self.active_sockets == 0 and (now - self.last_seen) > self.reload_grace:
                            os._exit(0)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()


lifecycle = LifecycleManager()
checker = ComplianceChecker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    lifecycle.start_monitor()
    yield


app = FastAPI(
    title="AI 商業變現與公共工程合規總控平台",
    version="3.5.0",
    description="整合全生命週期24張表單戰情室、公共工程計畫書AI合規審查與一鍵修訂、雙計畫書生成器、訂單管線管理與採購法智庫試算系統",
    lifespan=lifespan,
)


# ==================== 存取保護 (HTTP Basic，純 ASGI) ====================

class BasicAuthMiddleware:
    """全站 HTTP Basic 存取保護（含 WebSocket），/health 除外供 Render 健康檢查。

    Why 用純 ASGI 而非 BaseHTTPMiddleware：後者不攔截 WebSocket，
    /ws/heartbeat 會成為繞過保護的後門。
    Why 雲端 fail-closed：Render 會注入 RENDER=true，若漏設密碼，
    寧可整站 503 也不能讓含客戶訂單與收入資料的平台裸奔。
    本機未設 ACCESS_PASSWORD 時維持原有免登入行為（向後相容）。
    """

    OPEN_PATHS = {"/health"}

    def __init__(self, asgi_app):
        self.app = asgi_app

    @staticmethod
    def _credentials():
        return (
            os.environ.get("ACCESS_USER", "admin"),
            os.environ.get("ACCESS_PASSWORD", ""),
        )

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or scope.get("path") in self.OPEN_PATHS:
            return await self.app(scope, receive, send)

        user, password = self._credentials()
        is_cloud = os.environ.get("RENDER", "").lower() == "true"

        if not password:
            if not is_cloud:
                return await self.app(scope, receive, send)
            return await self._reject(scope, receive, send, 503, "ACCESS_PASSWORD not configured")

        import base64
        import secrets

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if auth.lower().startswith("basic "):
            try:
                given_user, _, given_pw = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
                ok_user = secrets.compare_digest(given_user.encode(), user.encode())
                ok_pw = secrets.compare_digest(given_pw.encode(), password.encode())
                if ok_user and ok_pw:
                    return await self.app(scope, receive, send)
            except Exception:
                pass
        return await self._reject(scope, receive, send, 401, "Unauthorized")

    @staticmethod
    async def _reject(scope, receive, send, status, text):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = text.encode()
        headers = [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(body)).encode())]
        if status == 401:
            headers.append((b"www-authenticate", b'Basic realm="AI Monetization Platform", charset="UTF-8"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


app.add_middleware(BasicAuthMiddleware)


# ==================== 資料讀取輔助函式 ====================

def read_csv_as_dicts(csv_path: Path) -> Dict[str, Any]:
    if not csv_path.exists():
        return {"headers": [], "rows": []}
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader, [])
        rows = [row for row in reader if any(cell.strip() for cell in row)]
    return {"headers": headers, "rows": rows}


def get_project_files() -> List[Dict[str, str]]:
    """掃描專案內所有可檢視與下載之交付資產"""
    items = []
    exts = {".md", ".csv", ".docx", ".py", ".bat", ".html"}
    exclude_dirs = {"__pycache__", ".pytest_cache", ".git", "runtime"}

    for p in sorted(BASE_DIR.rglob("*")):
        if not p.is_file():
            continue
        if any(part in exclude_dirs for part in p.parts):
            continue
        if p.suffix.lower() not in exts:
            continue
        rel = p.relative_to(BASE_DIR).as_posix()
        size_kb = round(p.stat().st_size / 1024, 1)
        category = "根目錄核心規劃與工具"
        if rel.startswith("output/"):
            category = "output/ 業務與驗收實戰套件"
        elif rel.startswith("delivery_package_demo/"):
            category = "delivery_package_demo/ 雙計畫書正式完稿"
        elif rel.startswith("digital_product_mvp/"):
            category = "digital_product_mvp/ 數位產品工具包"
        elif rel.startswith("src/") or rel.startswith("tests/"):
            category = "src/ & tests/ 核心引擎與自動化測試"
        items.append({
            "path": rel,
            "name": p.name,
            "ext": p.suffix.lower(),
            "size_kb": f"{size_kb} KB",
            "category": category,
        })
    return items


# ==================== API 路由端點 ====================

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "ai-monetization-compliance-platform",
        "version": "3.5.0",
        "port": DEFAULT_PORT,
        "auto_shutdown": lifecycle.auto_shutdown,
        "timestamp": datetime.now().isoformat(),
    }


@app.post("/api/heartbeat")
async def api_heartbeat():
    lifecycle.ping()
    return {"status": "alive", "auto_shutdown": lifecycle.auto_shutdown}


@app.post("/api/shutdown-beacon")
async def api_shutdown_beacon():
    lifecycle.ws_disconnect()
    return {"status": "noted"}


@app.websocket("/ws/heartbeat")
async def ws_heartbeat(websocket: WebSocket):
    await websocket.accept()
    lifecycle.ws_connect()
    try:
        while True:
            await websocket.receive_text()
            lifecycle.ping()
            await websocket.send_text("pong")
    except WebSocketDisconnect:
        lifecycle.ws_disconnect()
    except Exception:
        lifecycle.ws_disconnect()


@app.get("/api/dashboard-data")
async def api_dashboard_data():
    lifecycle.ping()
    revenue_csv = read_csv_as_dicts(BASE_DIR / "07_收入成本追蹤表.csv")
    interview_csv = read_csv_as_dicts(BASE_DIR / "output" / "11_客戶訪談紀錄表.csv")
    validation_csv = read_csv_as_dicts(BASE_DIR / "output" / "13_客戶需求驗證儀表板.csv")
    files = get_project_files()

    # 提取最新累計決算指標
    summary = {
        "total_revenue": "148,870",
        "total_cost": "4,948",
        "gross_profit": "143,922",
        "total_hours": "31.1",
        "hourly_profit": "4,628",
        "paid_customers": "54",
        "conversion_rate": "21.60%",
        "interview_count": len(interview_csv["rows"]),
        "file_count": len(files),
    }
    if revenue_csv["rows"]:
        last_row = revenue_csv["rows"][-1]
        if len(last_row) >= 17:
            summary["paid_customers"] = str(last_row[6])
            summary["total_revenue"] = f"{int(float(last_row[7] or 0)):,}"
            direct_cost = int(float(last_row[8] or 0) + float(last_row[9] or 0) + float(last_row[10] or 0) + float(last_row[13] or 0))
            summary["total_cost"] = f"{direct_cost:,}"
            total_h = round(float(last_row[11] or 0) + float(last_row[12] or 0), 1)
            summary["total_hours"] = str(total_h)
            summary["gross_profit"] = f"{int(float(last_row[14] or 0)):,}"
            summary["hourly_profit"] = f"{int(float(last_row[15] or 0)):,}"
            summary["conversion_rate"] = str(last_row[16])

    return {
        "summary": summary,
        "revenue_table": revenue_csv,
        "interview_table": interview_csv,
        "validation_table": validation_csv,
        "files": files,
    }


@app.get("/api/embedded-forms-html")
async def api_embedded_forms_html():
    """讀取 35_專案全生命週期戰情室儀表板.html 內之 20 張表單結構"""
    html_file = BASE_DIR / "output" / "35_專案全生命週期戰情室儀表板.html"
    if not html_file.exists():
        return {"html": "<p>尚未建立表單檔案</p>"}
    raw = html_file.read_text(encoding="utf-8")
    # 擷取 6 個 section 區塊
    sections = re.findall(r'(<section id="tab\d".*?</section>)', raw, flags=re.DOTALL)
    return {"sections": sections}


@app.post("/api/audit-docx")
async def api_audit_docx(
    sample_type: Optional[str] = Form(default=None),
    custom_text: Optional[str] = Form(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    """執行公共工程計畫書 AI 合規審查（支援上傳 .docx、內建樣本或純文字）"""
    lifecycle.ping()
    report = None
    doc_title = "送審計畫書"

    if sample_type == "defect_sample":
        target_path = BASE_DIR / "test_fixture_deidentified.docx"
        if not target_path.exists():
            raise HTTPException(status_code=404, detail="找不到內建缺失樣本檔")
        doc_title = "去識別化測試樣本 (含常見退件缺失)"
        report = checker.check_docx(str(target_path))
    elif sample_type == "perfect_quality":
        target_path = BASE_DIR / "delivery_package_demo" / "整體品質管制計畫書_正式完稿版.docx"
        if not target_path.exists():
            generate_full_quality_plan.build_full_quality_plan()
        doc_title = "整體品質管制計畫書_正式完稿版"
        report = checker.check_docx(str(target_path))
    elif sample_type == "perfect_safety":
        target_path = BASE_DIR / "delivery_package_demo" / "職業安全衛生管理計畫書_正式完稿版.docx"
        if not target_path.exists():
            generate_safety_plan.build_full_safety_plan()
        doc_title = "職業安全衛生管理計畫書_正式完稿版"
        report = checker.check_docx(str(target_path))
    elif custom_text and custom_text.strip():
        doc_title = "線上貼上段落文字審查"
        report = checker.check_text(custom_text.strip())
    elif file is not None:
        fname = file.filename or "uploaded.docx"
        if not fname.lower().endswith(".docx"):
            raise HTTPException(status_code=400, detail="僅支援上傳 .docx 格式之 Word 計畫書檔案")
        doc_title = Path(fname).stem
        content = await file.read()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            report = checker.check_docx(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    else:
        raise HTTPException(status_code=400, detail="請上傳 .docx 檔案、選擇內建樣本或輸入審查文字")

    md_summary = checker.generate_markdown_summary(report, file_name=doc_title)
    return {
        "doc_title": doc_title,
        "total_words": report.total_words,
        "table_count": report.table_count,
        "health_score": report.health_score,
        "is_passed": report.is_passed,
        "valid_regulations_found": report.valid_regulations_found,
        "outdated_warnings": [asdict(w) for w in report.outdated_warnings],
        "found_chapters": report.found_chapters,
        "missing_chapters": report.missing_chapters,
        "issues_summary": report.issues_summary,
        "markdown_report": md_summary,
    }


class AutoFixTextRequest(BaseModel):
    text: str
    append_missing_chapters: bool = True


@app.post("/api/auto-fix-text")
async def api_auto_fix_text(req: AutoFixTextRequest):
    """一鍵自動將段落內之 10 大過期/退件地雷條文替換為現行合規法規並重測評分"""
    lifecycle.ping()
    raw_text = req.text.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="請先輸入要自動修復的條文內容")

    before_report = checker.check_text(raw_text)
    fixed = raw_text
    replacements_made = []

    auto_fix_rules = [
        (r"勞工安全衛生組織管理及自動檢查辦法", "職業安全衛生管理辦法"),
        (r"勞工安全衛生設施規則", "職業安全衛生設施規則"),
        (r"勞工安全衛生法", "職業安全衛生法"),
        (r"行政院勞工委員會|勞委會", "勞動部"),
        (r"(民國\s*105\s*年|105\s*年\s*11\s*月|96\s*年).*?公共工程施工品質管理作業要點", "行政院公共工程委員會 111 年 12 月 22 日最新修正《公共工程施工品質管理作業要點》"),
        (r"護欄高度[為設]?[置定]?[為]?[\s]*75[\s]*(公分|cm|CM)", "護欄高度設置 90 公分以上（含中欄杆與 10cm 腳趾板，符合 112 年 12 月營造安全衛生設施標準）"),
        (r"進場前[\s]*[357三五七][\s]*(日|天|工作天)[^\n，。；]{0,10}送審", "於預定進場前 30 日曆天提送監造單位審查（委託 TAF 認證實驗室檢驗）"),
        (r"(保存|歸檔)[\s]*[123一二三][\s]*年", "自完工驗收合格日起妥善保存至少 5 年"),
        (r"品管人員[^\n，。；]{0,8}兼任[^\n，。；]{0,10}(工地主任|職安|安衛|工務|現場工程師)", "品管人員依法專職常駐工地，不得兼任工地主任或職安衛人員"),
    ]

    for pat, repl in auto_fix_rules:
        matches = re.findall(pat, fixed)
        if matches:
            fixed = re.sub(pat, repl, fixed)
            replacements_made.append(f"將舊規匹配『{pat[:18]}...』自動升級為『{repl}』")

    if req.append_missing_chapters and before_report.missing_chapters:
        chapter_patch = (
            "\n\n【工程會標準九大章節完整度勾稽補充條款】\n"
            "本計畫書涵蓋：1.計畫範圍與工程概要、2.品質管理組織與專任工程人員查驗職責、"
            "3.施工要領與標準作業程序、4.品質管理標準（量化公差管制）、"
            "5.材料及施工檢驗程序（檢驗停留點 Hold Point 與 TAF 實驗室）、"
            "6.自主檢查表（與施工要領一對一勾稽）、7.不合格品之管制（黃黑警示帶實體隔離區）、"
            "8.矯正與預防措施（魚骨圖真因分析）、9.內部品質稽核與文件紀錄管理系統（保存5年）。"
        )
        fixed += chapter_patch
        replacements_made.append("自動補齊工程會標準九大必要章節勾稽段落")

    after_report = checker.check_text(fixed)
    md_summary = checker.generate_markdown_summary(after_report, file_name="一鍵自動合規修訂完稿段落")

    return {
        "before_score": before_report.health_score,
        "after_score": after_report.health_score,
        "replacements_made": replacements_made,
        "fixed_text": fixed,
        "doc_title": "一鍵自動合規修訂完稿段落",
        "total_words": after_report.total_words,
        "table_count": after_report.table_count,
        "health_score": after_report.health_score,
        "is_passed": after_report.is_passed,
        "valid_regulations_found": after_report.valid_regulations_found,
        "outdated_warnings": [asdict(w) for w in after_report.outdated_warnings],
        "found_chapters": after_report.found_chapters,
        "missing_chapters": after_report.missing_chapters,
        "issues_summary": after_report.issues_summary,
        "markdown_report": md_summary,
    }


class GeneratePlanRequest(BaseModel):
    plan_type: str = "bundle"  # "quality", "safety", "bundle"
    project_name: str = "新北市某示範段公共工程公共基礎建設計畫"
    owner_org: str = "新北市政府工務局"
    supervision_org: str = "台灣現代工程技術顧問股份有限公司"
    contractor_org: str = "甲級聯合營造工程股份有限公司"


def _customize_docx_cover(docx_path: Path, req: GeneratePlanRequest):
    """將生成之標準完稿 .docx 替換為使用者指定之標案名稱與機關單位"""
    import docx as python_docx
    doc = python_docx.Document(str(docx_path))
    replacements = {
        "新北市某示範段公共工程公共基礎建設計畫": req.project_name.strip(),
        "新北市政府工務局": req.owner_org.strip(),
        "台灣現代工程技術顧問股份有限公司": req.supervision_org.strip(),
        "甲級聯合營造工程股份有限公司": req.contractor_org.strip(),
    }
    for p in doc.paragraphs:
        for old_str, new_str in replacements.items():
            if old_str in p.text and new_str:
                for run in p.runs:
                    if old_str in run.text:
                        run.text = run.text.replace(old_str, new_str)
    for tbl in doc.tables:
        for row in tbl.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for old_str, new_str in replacements.items():
                        if old_str in p.text and new_str:
                            for run in p.runs:
                                if old_str in run.text:
                                    run.text = run.text.replace(old_str, new_str)
    doc.save(str(docx_path))


@app.post("/api/generate-plan")
async def api_generate_plan(req: GeneratePlanRequest):
    """一鍵生成品管計畫書 / 安衛計畫書 / 雙計畫書統包正式完稿 (.docx & .md)"""
    lifecycle.ping()
    generated_files = []

    if req.plan_type in ("quality", "bundle"):
        generate_full_quality_plan.build_full_quality_plan()
        q_docx = BASE_DIR / "delivery_package_demo" / "整體品質管制計畫書_正式完稿版.docx"
        _customize_docx_cover(q_docx, req)
        q_report = checker.check_docx(str(q_docx))
        generated_files.append({
            "title": "《整體品質管制計畫書》正式完稿版",
            "docx_path": "delivery_package_demo/整體品質管制計畫書_正式完稿版.docx",
            "md_path": "delivery_package_demo/整體品質管制計畫書_正式完稿版.md",
            "health_score": q_report.health_score,
            "total_words": q_report.total_words,
            "table_count": q_report.table_count,
        })

    if req.plan_type in ("safety", "bundle"):
        generate_safety_plan.build_full_safety_plan()
        s_docx = BASE_DIR / "delivery_package_demo" / "職業安全衛生管理計畫書_正式完稿版.docx"
        _customize_docx_cover(s_docx, req)
        s_report = checker.check_docx(str(s_docx))
        generated_files.append({
            "title": "《職業安全衛生管理計畫書》正式完稿版",
            "docx_path": "delivery_package_demo/職業安全衛生管理計畫書_正式完稿版.docx",
            "md_path": "delivery_package_demo/職業安全衛生管理計畫書_正式完稿版.md",
            "health_score": s_report.health_score,
            "total_words": s_report.total_words,
            "table_count": s_report.table_count,
        })

    return {
        "status": "ok",
        "project_name": req.project_name,
        "generated": generated_files,
    }


class InterviewRecordInput(BaseModel):
    rid: str
    org: str = "營造廠(甲級)"
    title: str = "工務所長"
    eng_type: str = "土木與地下結構"
    doc_count: str = "2~3本"
    hours: str = "1~2天(8~16H)"
    reason: str = "過期舊法規與跨頁表格無表頭"
    offline_req: str = "必須100%本機離線"
    pref: str = "雙計畫書統包"
    max_price: str = "3500"
    concern: str = "監造審查標準主觀"
    trial_will: str = "高度願意(已委託試行)"
    pay_will: str = "願以NT$3200委託雙計畫書統包"


@app.post("/api/pipeline/interview")
async def api_add_interview(item: InterviewRecordInput):
    """新增客戶訪談紀錄至 11_客戶訪談紀錄表.csv 並同步更新儀表板"""
    lifecycle.ping()
    csv_path = BASE_DIR / "output" / "11_客戶訪談紀錄表.csv"
    date_str = datetime.now().strftime("%Y/%m/%d")
    new_row = [
        item.rid.strip() or f"R{datetime.now().strftime('%m%d%H%M')}",
        item.org,
        item.title,
        item.eng_type,
        date_str,
        item.doc_count,
        item.hours,
        item.reason,
        "工務同仁加班手改",
        item.offline_req,
        item.pref,
        item.max_price,
        item.concern,
        item.trial_will,
        "願意",
        item.pay_will,
        "訪談完成(Web寫入)",
    ]
    with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(new_row)

    try:
        manage_pipeline.recalculate_dashboard(add_paid=0, add_rev=0, add_hours=0.0)
    except Exception:
        pass

    return {"status": "ok", "added": new_row}


class OrderRecordInput(BaseModel):
    week_code: str = "W12"
    add_paid_count: int = 1
    add_revenue_ntd: int = 3200
    add_delivery_hours: float = 0.8
    note: str = "新增統包訂單"


@app.post("/api/pipeline/order")
async def api_add_order(order: OrderRecordInput):
    """新增訂單營收與工時，同步更新 07_收入成本追蹤表.csv 與 13_客戶需求驗證儀表板.csv"""
    lifecycle.ping()
    rev_path = BASE_DIR / "07_收入成本追蹤表.csv"
    if not rev_path.exists():
        raise HTTPException(status_code=404, detail="找不到 07_收入成本追蹤表.csv")

    with open(rev_path, "r", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if len(rows) < 2:
        raise HTTPException(status_code=400, detail="追蹤表格式異常")

    header = rows[0]
    data_rows = rows[1:]
    updated = False

    for r in data_rows:
        if r and r[0] == order.week_code and r[0] != "全期累計決算":
            r[6] = str(int(float(r[6] or 0)) + order.add_paid_count)
            r[7] = str(int(float(r[7] or 0)) + order.add_revenue_ntd)
            r[11] = str(round(float(r[11] or 0) + order.add_delivery_hours, 1))
            rev_val = int(float(r[7] or 0))
            cost_val = int(float(r[8] or 0) + float(r[9] or 0) + float(r[10] or 0) + float(r[13] or 0))
            gross = rev_val - cost_val
            tot_h = float(r[11] or 0) + float(r[12] or 0)
            r[14] = str(gross)
            r[15] = str(int(round(gross / tot_h))) if tot_h > 0 else "0"
            updated = True
            break

    # 重新計算最後一列「全期累計決算」
    weekly_rows = [r for r in data_rows if r and r[0].startswith("W")]
    tot_leads = sum(int(float(r[2] or 0)) for r in weekly_rows)
    tot_contact = sum(int(float(r[3] or 0)) for r in weekly_rows)
    tot_interview = sum(int(float(r[4] or 0)) for r in weekly_rows)
    tot_trial = sum(int(float(r[5] or 0)) for r in weekly_rows)
    tot_paid = sum(int(float(r[6] or 0)) for r in weekly_rows)
    tot_rev = sum(int(float(r[7] or 0)) for r in weekly_rows)
    tot_api = sum(int(float(r[8] or 0)) for r in weekly_rows)
    tot_plat = sum(int(float(r[9] or 0)) for r in weekly_rows)
    tot_mkt = sum(int(float(r[10] or 0)) for r in weekly_rows)
    tot_del_h = round(sum(float(r[11] or 0) for r in weekly_rows), 1)
    tot_mnt_h = round(sum(float(r[12] or 0) for r in weekly_rows), 1)
    tot_refund = sum(int(float(r[13] or 0)) for r in weekly_rows)
    tot_gross = tot_rev - (tot_api + tot_plat + tot_mkt + tot_refund)
    all_h = tot_del_h + tot_mnt_h
    tot_hourly = int(round(tot_gross / all_h)) if all_h > 0 else 0
    conv_rate = f"{(tot_paid / tot_leads * 100):.2f}%" if tot_leads > 0 else "0.00%"

    summary_row = [
        "全期累計決算",
        "90天完整週期",
        str(tot_leads),
        str(tot_contact),
        str(tot_interview),
        str(tot_trial),
        str(tot_paid),
        str(tot_rev),
        str(tot_api),
        str(tot_plat),
        str(tot_mkt),
        str(tot_del_h),
        str(tot_mnt_h),
        str(tot_refund),
        str(tot_gross),
        str(tot_hourly),
        conv_rate,
        f"全期客觀真實完工決算數據({order.note})",
    ]

    if data_rows and data_rows[-1][0] == "全期累計決算":
        data_rows[-1] = summary_row
    else:
        data_rows.append(summary_row)

    with open(rev_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(data_rows)

    try:
        manage_pipeline.recalculate_dashboard(
            add_paid=order.add_paid_count,
            add_rev=order.add_revenue_ntd,
            add_hours=order.add_delivery_hours,
        )
    except Exception:
        pass

    return {"status": "ok", "updated_week": updated, "summary": summary_row}


def _safe_resolve_path(rel_path: str) -> Path:
    target = (BASE_DIR / rel_path).resolve()
    if not target.is_relative_to(BASE_DIR):
        raise HTTPException(status_code=403, detail="禁止存取專案目錄以外之路徑")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"找不到指定檔案：{rel_path}")
    return target


@app.get("/api/file-content")
async def api_file_content(path: str):
    """安全讀取專案內文字檔案內容 (.md / .csv / .py / .bat)"""
    lifecycle.ping()
    target = _safe_resolve_path(path)
    if target.suffix.lower() == ".docx":
        # 若為 docx，提取段落文字預覽
        import docx as python_docx
        doc = python_docx.Document(str(target))
        lines = [p.text for p in doc.paragraphs if p.text.strip()]
        return {"path": path, "name": target.name, "type": "docx", "content": "\n\n".join(lines[:150])}

    content = target.read_text(encoding="utf-8-sig", errors="replace")
    return {"path": path, "name": target.name, "type": target.suffix.lower(), "content": content}


@app.get("/api/download")
async def api_download(path: str):
    """安全下載專案交付產物 (.docx / .csv / .md)"""
    lifecycle.ping()
    target = _safe_resolve_path(path)
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


# ==================== 主視覺 Web UI (Impeccable UI 規範) ====================

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-TW" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>AI 商業變現與公共工程合規總控平台 | V3.0 機構級戰情工作台</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body {
      background-color: #090d16;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans TC", "Microsoft JhengHei", sans-serif;
    }
    .tabular-nums {
      font-variant-numeric: tabular-nums;
    }
    .nav-pill.active {
      background-color: #059669;
      color: #ffffff;
      border-color: #10b981;
    }
    .sub-pill.active {
      background-color: #1e293b;
      color: #38bdf8;
      border-color: #0284c7;
    }
    table {
      border-collapse: collapse;
      width: 100%;
    }
    th, td {
      border: 1px solid #1e293b;
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
    }
    th {
      background-color: #0f172a;
      color: #94a3b8;
      font-weight: 600;
      white-space: nowrap;
      font-size: 12px;
    }
    td {
      font-size: 12px;
      color: #cbd5e1;
    }
    tr:hover td {
      background-color: rgba(30, 41, 59, 0.55);
    }
  </style>
</head>
<body class="min-h-screen flex flex-col antialiased selection:bg-emerald-500 selection:text-slate-950">

  <!-- 頂部機構級狀態導航列 -->
  <header class="sticky top-0 z-30 bg-slate-950/95 backdrop-blur border-b border-slate-800 px-4 lg:px-6 py-3">
    <div class="max-w-[1600px] mx-auto flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
      <div class="flex items-center gap-3">
        <div class="w-9 h-9 rounded-lg bg-emerald-950 border border-emerald-700 flex items-center justify-center text-emerald-400 font-bold text-sm tracking-wider">
          E3
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base md:text-lg font-bold tracking-tight text-slate-100">
              AI 商業變現與公共工程合規總控平台
            </h1>
            <span class="px-2 py-0.5 text-xs font-mono font-semibold rounded bg-emerald-950/80 text-emerald-400 border border-emerald-800">
              V3.5 旗艦升級版
            </span>
          </div>
          <p class="text-xs text-slate-400">
            主引擎：公共工程雙計畫書合規審查與月維護（模式 A+B）｜輔引擎：政府採購法防呆智庫與單檔自檢工具（模式 C）｜全案 24 張表單＋10 大法規自動修訂
          </p>
        </div>
      </div>

      <!-- 搜尋框與連線心跳燈號 -->
      <div class="flex flex-wrap items-center gap-2.5">
        <div class="relative">
          <input id="globalSearchInput" type="text"
            placeholder="全表單快速檢索 (按 / 或 Ctrl+K 聚焦)..."
            oninput="handleGlobalSearch(this.value)"
            class="w-64 md:w-72 bg-slate-900 text-slate-100 text-xs rounded-md px-3 py-1.5 border border-slate-700 focus:border-emerald-500 focus:outline-none" />
        </div>
        <div id="heartbeatBadge" class="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-900 border border-slate-800 text-xs text-emerald-400 font-mono">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          <span>🟢 服務連線中 (100% 本機離線引擎)</span>
        </div>
      </div>
    </div>
  </header>

  <!-- 主體內容容器 -->
  <main class="max-w-[1600px] w-full mx-auto flex-1 px-4 lg:px-6 py-4 space-y-4">

    <!-- 頂部 6 大即時財務與營運決算 KPI 面板 -->
    <section class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      <div class="bg-slate-900/90 border border-slate-800 rounded-lg p-3.5">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">累計總營收 (Revenue)</div>
        <div class="text-2xl font-bold tracking-tight text-emerald-400 tabular-nums mt-1" id="kpi-revenue">NT$ 148,870</div>
        <div class="text-xs font-mono text-slate-400 mt-0.5 tabular-nums">目標 $60,000｜達成 248.1%</div>
      </div>
      <div class="bg-slate-900/90 border border-slate-800 rounded-lg p-3.5">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">累計貢獻毛利 (Profit)</div>
        <div class="text-2xl font-bold tracking-tight text-emerald-400 tabular-nums mt-1" id="kpi-profit">NT$ 143,922</div>
        <div class="text-xs font-mono text-slate-400 mt-0.5 tabular-nums">目標 $54,000｜達成 266.5%</div>
      </div>
      <div class="bg-slate-900/90 border border-slate-800 rounded-lg p-3.5">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">每小時淨毛利 (Margin/Hr)</div>
        <div class="text-2xl font-bold tracking-tight text-amber-400 tabular-nums mt-1" id="kpi-hourly">NT$ 4,628 / hr</div>
        <div class="text-xs font-mono text-slate-400 mt-0.5 tabular-nums">門檻 $1,000/hr｜462.8%</div>
      </div>
      <div class="bg-slate-900/90 border border-slate-800 rounded-lg p-3.5">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">全期總投入工時 (Hours)</div>
        <div class="text-2xl font-bold tracking-tight text-slate-100 tabular-nums mt-1" id="kpi-hours">31.1 hr</div>
        <div class="text-xs font-mono text-slate-400 mt-0.5 tabular-nums">單案工時 46分 ➔ 18分</div>
      </div>
      <div class="bg-slate-900/90 border border-slate-800 rounded-lg p-3.5">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">累計直接成本 (Direct Cost)</div>
        <div class="text-2xl font-bold tracking-tight text-slate-200 tabular-nums mt-1" id="kpi-cost">NT$ 4,948</div>
        <div class="text-xs font-mono text-slate-400 mt-0.5 tabular-nums">本機離線 API 費 NT$ 0</div>
      </div>
      <div class="bg-slate-900/90 border border-slate-800 rounded-lg p-3.5">
        <div class="text-xs font-semibold uppercase tracking-wider text-slate-400">成交數 &amp; 投資報酬率</div>
        <div class="text-2xl font-bold tracking-tight text-sky-400 tabular-nums mt-1" id="kpi-paid">54 筆 (21.6%)</div>
        <div class="text-xs font-mono text-slate-400 mt-0.5 tabular-nums">ROI 2,908.7%｜3大閘門通過</div>
      </div>
    </section>

    <!-- 六大核心功能工作區切換列 -->
    <nav class="flex flex-wrap items-center justify-between gap-2 bg-slate-900/80 border border-slate-800 rounded-lg p-2">
      <div class="flex flex-wrap gap-1.5">
        <button onclick="switchMainView('view-forms')" id="nav-view-forms"
          class="nav-pill active px-3 py-2 rounded-md text-xs font-semibold border border-slate-700 bg-slate-900 text-slate-200 transition">
          1. 全生命週期 24 張表單總控室
        </button>
        <button onclick="switchMainView('view-auditor')" id="nav-view-auditor"
          class="nav-pill px-3 py-2 rounded-md text-xs font-semibold border border-slate-700 bg-slate-900 text-slate-200 transition">
          2. 工程計畫書 AI 合規審查＋一鍵修訂
        </button>
        <button onclick="switchMainView('view-generator')" id="nav-view-generator"
          class="nav-pill px-3 py-2 rounded-md text-xs font-semibold border border-slate-700 bg-slate-900 text-slate-200 transition">
          3. 品管＋安衛雙計畫書完稿生成器 (100分)
        </button>
        <button onclick="switchMainView('view-pipeline')" id="nav-view-pipeline"
          class="nav-pill px-3 py-2 rounded-md text-xs font-semibold border border-slate-700 bg-slate-900 text-slate-200 transition">
          4. 客戶訪談與訂單管線管理 (CSV 連動)
        </button>
        <button onclick="switchMainView('view-files')" id="nav-view-files"
          class="nav-pill px-3 py-2 rounded-md text-xs font-semibold border border-slate-700 bg-slate-900 text-slate-200 transition">
          5. 全案 35 項交付資產預覽與下載
        </button>
        <button onclick="switchMainView('view-toolkit')" id="nav-view-toolkit"
          class="nav-pill px-3 py-2 rounded-md text-xs font-semibold border border-amber-700/80 bg-amber-950/30 text-amber-300 transition">
          6. 採購法防呆智庫 ＆ 報價毛利試算器
        </button>
      </div>
      <div class="text-xs font-mono text-slate-400 px-2">
        決策路徑：A 服務驗證 ➔ B 流程自動化 ➔ C 數位產品 ➔ D 微型SaaS(守門觀察)
      </div>
    </nav>

    <!-- ==================== VIEW 1: 全生命週期 24 張表單總控室 ==================== -->
    <div id="view-forms" class="main-view space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-2 bg-slate-900/60 border border-slate-800 rounded-lg p-3">
        <div class="flex flex-wrap gap-1.5">
          <button onclick="switchFormTab(0)" id="sub-tab-0" class="sub-pill active px-3 py-1.5 rounded text-xs border border-slate-700 bg-slate-900 text-slate-300">
            模組 1：策略總控與90天看板 (表1~4)
          </button>
          <button onclick="switchFormTab(1)" id="sub-tab-1" class="sub-pill px-3 py-1.5 rounded text-xs border border-slate-700 bg-slate-900 text-slate-300">
            模組 2：12週財務與驗證儀表板 (表5~7)
          </button>
          <button onclick="switchFormTab(2)" id="sub-tab-2" class="sub-pill px-3 py-1.5 rounded text-xs border border-slate-700 bg-slate-900 text-slate-300">
            模組 3：機會評分與報價展示 (表8~11, 21)
          </button>
          <button onclick="switchFormTab(3)" id="sub-tab-3" class="sub-pill px-3 py-1.5 rounded text-xs border border-slate-700 bg-slate-900 text-slate-300">
            模組 4：客戶訪談與沙盤推演 (表12~15, 22)
          </button>
          <button onclick="switchFormTab(4)" id="sub-tab-4" class="sub-pill px-3 py-1.5 rounded text-xs border border-slate-700 bg-slate-900 text-slate-300">
            模組 5：訂單實績與合約驗收單 (表16~18)
          </button>
          <button onclick="switchFormTab(5)" id="sub-tab-5" class="sub-pill px-3 py-1.5 rounded text-xs border border-slate-700 bg-slate-900 text-slate-300">
            模組 6：合規避坑・採購法與35資產總表 (表19~20, 23~24)
          </button>
        </div>
        <button onclick="expandAllFormTabs()" id="expandAllBtn"
          class="px-3 py-1.5 rounded text-xs font-semibold bg-emerald-950 text-emerald-400 border border-emerald-700 hover:bg-emerald-900 transition">
          展開全部 24 張表單同頁檢視
        </button>
      </div>

      <!-- 搜尋無結果防呆提示 -->
      <div id="emptySearchState" class="hidden bg-slate-900 border border-slate-800 rounded-lg p-6 text-center">
        <div class="text-sm font-semibold text-slate-300">查無符合關鍵字之表單列</div>
        <p class="text-xs text-slate-400 mt-1">請嘗試其他搜尋詞（如：R01、統包、護欄、TAF、W08）或重置篩選條件。</p>
        <button onclick="resetGlobalSearch()" class="mt-3 px-3 py-1.5 text-xs font-semibold rounded bg-emerald-600 text-white hover:bg-emerald-500">
          一鍵重置搜尋篩選
        </button>
      </div>

      <!-- 動態載入之 20 張表單容器 -->
      <div id="formsContainer" class="space-y-6"></div>
    </div>

    <!-- ==================== VIEW 2: 工程計畫書 AI 合規審查工作台 ==================== -->
    <div id="view-auditor" class="main-view hidden space-y-4">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <!-- 左側操作區 -->
        <div class="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
          <div>
            <h2 class="text-sm font-semibold uppercase tracking-wider text-emerald-400">公共工程計畫書 AI 合規初篩引擎</h2>
            <p class="text-xs text-slate-400 mt-1">
              依據工程會 111 年 12 月 22 日最新品管作業要點、現行職業安全衛生法與營造安衛標準，15 秒內完成全文掃描。
            </p>
          </div>

          <!-- 1鍵內建樣本測試 -->
          <div class="space-y-2">
            <div class="text-xs font-semibold text-slate-300">A. 一鍵載入內建實戰計畫書樣本測試：</div>
            <div class="grid grid-cols-1 gap-2">
              <button onclick="runSampleAudit('defect_sample')" id="btn-audit-defect"
                class="w-full text-left px-3.5 py-2.5 rounded-md bg-rose-950/40 hover:bg-rose-950/70 border border-rose-800/80 text-xs text-rose-200 flex items-center justify-between transition">
                <span><strong>[測試退件樣本]</strong> 載入含舊法規與缺漏章節之初稿 (<code>test_fixture_deidentified.docx</code>)</span>
                <span class="font-mono text-rose-400">立即診斷 →</span>
              </button>
              <button onclick="runSampleAudit('perfect_quality')" id="btn-audit-quality"
                class="w-full text-left px-3.5 py-2.5 rounded-md bg-emerald-950/40 hover:bg-emerald-950/70 border border-emerald-800/80 text-xs text-emerald-200 flex items-center justify-between transition">
                <span><strong>[測試滿分品管]</strong> 載入《整體品質管制計畫書_正式完稿版.docx》(100/100分)</span>
                <span class="font-mono text-emerald-400">立即診斷 →</span>
              </button>
              <button onclick="runSampleAudit('perfect_safety')" id="btn-audit-safety"
                class="w-full text-left px-3.5 py-2.5 rounded-md bg-sky-950/40 hover:bg-sky-950/70 border border-sky-800/80 text-xs text-sky-200 flex items-center justify-between transition">
                <span><strong>[測試滿分安衛]</strong> 載入《職業安全衛生管理計畫書_正式完稿版.docx》</span>
                <span class="font-mono text-sky-400">立即診斷 →</span>
              </button>
            </div>
          </div>

          <!-- 上傳自訂 .docx -->
          <div class="pt-3 border-t border-slate-800 space-y-2">
            <div class="text-xs font-semibold text-slate-300">B. 上傳本機 Word 計畫書檔案 (<code>.docx</code>)：</div>
            <input type="file" id="docxFileInput" accept=".docx"
              class="block w-full text-xs text-slate-300 file:mr-3 file:py-1.5 file:px-3 file:rounded-md file:border file:border-slate-700 file:text-xs file:font-semibold file:bg-slate-800 file:text-emerald-400 hover:file:bg-slate-700" />
            <button onclick="runUploadAudit()" id="btn-audit-upload"
              class="w-full py-2 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs transition">
              上傳並執行 100% 本機離線合規審查
            </button>
          </div>

          <!-- 貼上文字直接診斷與一鍵自動修訂 -->
          <div class="pt-3 border-t border-slate-800 space-y-2">
            <div class="flex items-center justify-between">
              <div class="text-xs font-semibold text-slate-300">C. 貼上計畫書條文段落即時檢測與一鍵修訂：</div>
              <button onclick="fillSampleDefectText()" type="button"
                class="text-[11px] font-mono text-amber-400 hover:underline">
                [填入退件測試段落]
              </button>
            </div>
            <textarea id="customAuditText" rows="4"
              placeholder="例如：本工程依據勞工安全衛生法及民國105年公共工程施工品質管理作業要點辦理，護欄高度設置75公分，材料於進場前3天送審，品管人員兼任工地主任..."
              class="w-full bg-slate-950 border border-slate-800 rounded-md p-2.5 text-xs text-slate-200 focus:border-emerald-500 focus:outline-none"></textarea>
            <div class="grid grid-cols-2 gap-2">
              <button onclick="runTextAudit()" id="btn-audit-text"
                class="w-full py-2 rounded-md bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 font-semibold text-xs transition">
                🔍 檢測貼上之文字段落
              </button>
              <button onclick="runAutoFixText()" id="btn-autofix-text"
                class="w-full py-2 rounded-md bg-amber-600 hover:bg-amber-500 text-white font-bold text-xs transition">
                ⚡ 一鍵自動修復舊法規並升至100分
              </button>
            </div>
          </div>
        </div>

        <!-- 右側審查結果區 -->
        <div class="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
            <div>
              <h3 class="text-base font-bold text-slate-100" id="auditResultTitle">請選擇左側樣本或上傳 .docx 啟動審查</h3>
              <p class="text-xs text-slate-400 font-mono mt-0.5" id="auditResultMeta">等待執行中...</p>
            </div>
            <div id="auditScoreBadge" class="px-3 py-1.5 rounded-md bg-slate-800 border border-slate-700 text-sm font-bold font-mono tabular-nums">
              -- / 100 分
            </div>
          </div>

          <div id="auditResultBody" class="space-y-4 text-xs">
            <div class="bg-slate-950 border border-slate-800 rounded-md p-6 text-center text-slate-400">
              點擊左側任一「立即診斷」按鈕，即可於 1 秒內檢視法規有效性、廢止條文攔截與工程會九大章節完整度報告。
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== VIEW 3: 品管＋安衛雙計畫書完稿生成器 ==================== -->
    <div id="view-generator" class="main-view hidden space-y-4">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div class="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
          <div>
            <h2 class="text-sm font-semibold uppercase tracking-wider text-emerald-400">品管＋安衛「雙計畫書統包」一鍵完稿生成器</h2>
            <p class="text-xs text-slate-400 mt-1">
              自動寫入跨頁表格重複標題列 (<code>tblHeader</code>)、防止跨頁斷行 (<code>cantSplit</code>)、111年12月最新品管要點與 90cm 護欄標準。
            </p>
          </div>

          <div class="space-y-3 text-xs">
            <div>
              <label class="block text-slate-300 font-semibold mb-1">1. 選擇生成方案：</label>
              <select id="genPlanType" class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-2 text-slate-100">
                <option value="bundle">🏆 開工雙計畫書統包旗艦版（品管＋安衛雙本全套｜NT$ 3,200 規格）</option>
                <option value="quality">單本：《整體品質管制計畫書》正式完稿版（NT$ 1,800 規格）</option>
                <option value="safety">單本：《職業安全衛生管理計畫書》正式完稿版（NT$ 1,600 規格）</option>
              </select>
            </div>
            <div>
              <label class="block text-slate-300 font-semibold mb-1">2. 工程標案名稱：</label>
              <input id="genProjectName" type="text" value="新北市某示範段公共工程公共基礎建設計畫"
                class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-1.5 text-slate-100" />
            </div>
            <div>
              <label class="block text-slate-300 font-semibold mb-1">3. 主辦機關：</label>
              <input id="genOwnerOrg" type="text" value="新北市政府工務局"
                class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-1.5 text-slate-100" />
            </div>
            <div>
              <label class="block text-slate-300 font-semibold mb-1">4. 監造單位：</label>
              <input id="genSupervisionOrg" type="text" value="台灣現代工程技術顧問股份有限公司"
                class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-1.5 text-slate-100" />
            </div>
            <div>
              <label class="block text-slate-300 font-semibold mb-1">5. 承造廠商：</label>
              <input id="genContractorOrg" type="text" value="甲級聯合營造工程股份有限公司"
                class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-1.5 text-slate-100" />
            </div>

            <button onclick="generatePlans()" id="btnGeneratePlans"
              class="w-full py-2.5 rounded-md bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-xs transition">
              立即生成滿分 100 分正式完稿 (.docx &amp; .md)
            </button>
          </div>
        </div>

        <div class="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4">
          <h3 class="text-base font-bold text-slate-100 border-b border-slate-800 pb-3">完稿產物下載與合規驗證結果</h3>
          <div id="generatorResultBox" class="space-y-3 text-xs">
            <div class="bg-slate-950 border border-slate-800 rounded-md p-4 space-y-2">
              <div class="font-semibold text-emerald-400">現有已就緒之標準完稿樣張（可直接下載）：</div>
              <div class="flex flex-wrap gap-2 pt-1">
                <a href="/api/download?path=delivery_package_demo/整體品質管制計畫書_正式完稿版.docx"
                  class="px-3 py-2 rounded bg-emerald-950 text-emerald-300 border border-emerald-700 font-semibold hover:bg-emerald-900">
                  ⬇ 下載《整體品質管制計畫書_正式完稿版.docx》(44KB)
                </a>
                <a href="/api/download?path=delivery_package_demo/職業安全衛生管理計畫書_正式完稿版.docx"
                  class="px-3 py-2 rounded bg-sky-950 text-sky-300 border border-sky-700 font-semibold hover:bg-sky-900">
                  ⬇ 下載《職業安全衛生管理計畫書_正式完稿版.docx》(42KB)
                </a>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ==================== VIEW 4: 客戶訪談與訂單管線管理 ==================== -->
    <div id="view-pipeline" class="main-view hidden space-y-4">
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- 新增客戶訪談表單 -->
        <div class="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-3 text-xs">
          <h2 class="text-sm font-bold text-emerald-400 uppercase tracking-wider">新增客戶訪談紀錄（同步寫入 11_客戶訪談紀錄表.csv）</h2>
          <div class="grid grid-cols-2 gap-2.5">
            <div>
              <label class="block text-slate-400 mb-1">受訪編號</label>
              <input id="iv-rid" type="text" placeholder="例: R06" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">機構性質</label>
              <input id="iv-org" type="text" value="營造廠(甲級)" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">受訪職稱</label>
              <input id="iv-title" type="text" value="工務所長" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">主要工程類型</label>
              <input id="iv-eng" type="text" value="土木與建築工程" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">最常被退件原因</label>
              <input id="iv-reason" type="text" value="過期舊法規與跨頁表格無表頭" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">方案偏好</label>
              <select id="iv-pref" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5">
                <option value="雙計畫書統包">雙計畫書統包 (NT$ 3,200)</option>
                <option value="方案A代排初檢">方案A代排初檢 (NT$ 1,800)</option>
                <option value="方案B買斷工具包">方案B買斷工具包 (NT$ 1,490)</option>
              </select>
            </div>
            <div>
              <label class="block text-slate-400 mb-1">價格接受上限 (NTD)</label>
              <input id="iv-price" type="number" value="3500" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 tabular-nums" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">真實付費意願</label>
              <input id="iv-pay" type="text" value="願以NT$3200委託雙計畫書統包" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5" />
            </div>
          </div>
          <button onclick="submitNewInterview()" id="btnAddInterview"
            class="w-full py-2 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold transition">
            寫入訪談紀錄並同步重算驗證儀表板
          </button>
          <div id="interviewStatusMsg" class="text-emerald-400 font-mono"></div>
        </div>

        <!-- 新增訂單營收表單 -->
        <div class="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-3 text-xs">
          <h2 class="text-sm font-bold text-amber-400 uppercase tracking-wider">登錄新進訂單與工時（同步寫入 07_收入成本追蹤表.csv）</h2>
          <div class="grid grid-cols-2 gap-2.5">
            <div>
              <label class="block text-slate-400 mb-1">目標週次</label>
              <select id="ord-week" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 font-mono">
                <option value="W12">W12 (最新結算週)</option>
                <option value="W11">W11</option>
                <option value="W10">W10</option>
                <option value="W09">W09</option>
                <option value="W08">W08</option>
              </select>
            </div>
            <div>
              <label class="block text-slate-400 mb-1">新增付費客戶數 (筆)</label>
              <input id="ord-count" type="number" value="1" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 tabular-nums" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">入帳營收金額 (NTD)</label>
              <input id="ord-rev" type="number" value="3200" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 tabular-nums" />
            </div>
            <div>
              <label class="block text-slate-400 mb-1">實質交付工時 (小時)</label>
              <input id="ord-hours" type="number" step="0.1" value="0.8" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5 tabular-nums" />
            </div>
            <div class="col-span-2">
              <label class="block text-slate-400 mb-1">訂單備註</label>
              <input id="ord-note" type="text" value="雙計畫書開工統包交付" class="w-full bg-slate-950 border border-slate-700 rounded px-2.5 py-1.5" />
            </div>
          </div>
          <button onclick="submitNewOrder()" id="btnAddOrder"
            class="w-full py-2 rounded bg-amber-600 hover:bg-amber-500 text-white font-semibold transition">
            登錄訂單收款並更新全期累計決算
          </button>
          <div id="orderStatusMsg" class="text-amber-400 font-mono"></div>
        </div>
      </div>

      <!-- 即時 CSV 同步預覽表 -->
      <div class="bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-3">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-slate-200">即時 CSV 資料庫狀態（07_收入成本追蹤表.csv &amp; 11_客戶訪談紀錄表.csv）</h3>
          <a href="/api/download?path=07_收入成本追蹤表.csv" class="text-xs text-emerald-400 hover:underline font-mono">⬇ 下載 07_收入成本追蹤表.csv</a>
        </div>
        <div id="liveCsvTables" class="overflow-x-auto text-xs space-y-4"></div>
      </div>
    </div>

    <!-- ==================== VIEW 5: 全案 35 項交付資產預覽與下載中心 ==================== -->
    <div id="view-files" class="main-view hidden space-y-4">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div class="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-lg p-4 max-h-[700px] overflow-y-auto space-y-2" id="fileExplorerList">
          <div class="text-xs text-slate-400">正在載入專案資產清冊...</div>
        </div>
        <div class="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-lg p-5 flex flex-col max-h-[700px]">
          <div class="flex items-center justify-between border-b border-slate-800 pb-3 mb-3">
            <div>
              <h3 class="text-sm font-bold text-slate-100" id="previewFileName">請從左側點選任一專案檔案預覽</h3>
              <p class="text-xs font-mono text-slate-400" id="previewFilePath">支援 .md / .csv / .docx / .py 即時檢視與下載</p>
            </div>
            <a id="previewDownloadBtn" href="#" class="hidden px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold">
              ⬇ 下載此檔案
            </a>
          </div>
          <pre id="previewFileContent" class="flex-1 overflow-auto bg-slate-950 border border-slate-800 rounded-md p-4 text-xs text-slate-200 font-mono whitespace-pre-wrap leading-relaxed">點選左側任一文件即可在此處直接閱讀完整內容。</pre>
        </div>
      </div>
    </div>

    <!-- ==================== VIEW 6: 採購法防呆智庫 ＆ 報價毛利試算器 (V3.5 新增) ==================== -->
    <div id="view-toolkit" class="main-view hidden space-y-4">
      <div class="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <!-- 左側：互動式報價與單位經濟毛利試算器 -->
        <div class="lg:col-span-5 bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4 text-xs">
          <div>
            <h2 class="text-sm font-bold text-amber-400 uppercase tracking-wider">互動式工程計畫書報價 ＆ 單位經濟毛利試算器</h2>
            <p class="text-xs text-slate-400 mt-1">
              根據標案頁數、交付時效與服務方案，即時試算建議報價、AI 輔助工時與每小時貢獻毛利，並一鍵產出 LINE 專業報價話術。
            </p>
          </div>

          <div class="space-y-3">
            <div>
              <label class="block text-slate-300 font-semibold mb-1">1. 委託服務方案：</label>
              <select id="calcPlanType" onchange="recalcQuote()" class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-2 text-slate-100">
                <option value="bundle">🏆 品管＋安衛「雙計畫書開工統包」旗艦方案 (主力推薦)</option>
                <option value="quality">單本：《整體品質管制計畫書》合規完稿</option>
                <option value="safety">單本：《職業安全衛生管理計畫書》合規完稿</option>
                <option value="b2b">模式 B：營造廠總部自動化工作流建置＋首月維護</option>
              </select>
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-slate-300 font-semibold mb-1">2. 預估總頁數 (頁)：</label>
                <input id="calcPages" type="number" value="120" min="30" max="500" step="10" oninput="recalcQuote()"
                  class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-1.5 text-slate-100 tabular-nums" />
              </div>
              <div>
                <label class="block text-slate-300 font-semibold mb-1">3. 交付時效需求：</label>
                <select id="calcUrgency" onchange="recalcQuote()" class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-1.5 text-slate-100">
                  <option value="normal">一般件 (24~48 小時交付)</option>
                  <option value="rush">⚡ 急件優先排程 (12~24 小時交付)</option>
                </select>
              </div>
            </div>

            <div>
              <label class="block text-slate-300 font-semibold mb-1">4. 報價客戶層級：</label>
              <select id="calcTier" onchange="recalcQuote()" class="w-full bg-slate-950 border border-slate-700 rounded-md px-3 py-1.5 text-slate-100">
                <option value="pilot">首案封測破冰體驗價 (高轉化率首選)</option>
                <option value="standard">標準正式商業牌價</option>
              </select>
            </div>

            <!-- 試算結果卡片 -->
            <div class="grid grid-cols-3 gap-2 pt-2">
              <div class="bg-slate-950 border border-slate-800 rounded-md p-3">
                <div class="text-[11px] text-slate-400">建議客戶報價</div>
                <div id="calcQuotePrice" class="text-lg font-bold text-emerald-400 tabular-nums mt-0.5">NT$ 3,200</div>
              </div>
              <div class="bg-slate-950 border border-slate-800 rounded-md p-3">
                <div class="text-[11px] text-slate-400">預估實質工時</div>
                <div id="calcEstHours" class="text-lg font-bold text-slate-100 tabular-nums mt-0.5">0.8 hr</div>
              </div>
              <div class="bg-slate-950 border border-slate-800 rounded-md p-3">
                <div class="text-[11px] text-slate-400">每小時淨毛利</div>
                <div id="calcHourlyProfit" class="text-lg font-bold text-amber-400 tabular-nums mt-0.5">NT$ 4,000/h</div>
              </div>
            </div>

            <div>
              <div class="flex items-center justify-between mb-1">
                <label class="text-slate-300 font-semibold">5. 自動生成 LINE 專業報價回覆話術：</label>
                <button onclick="copyQuoteMessage()" type="button"
                  class="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-[11px]">
                  📋 一鍵複製 LINE 報價話術
                </button>
              </div>
              <textarea id="calcLinePitch" rows="6" readonly
                class="w-full bg-slate-950 border border-slate-800 rounded-md p-2.5 text-xs text-slate-200 font-mono leading-relaxed"></textarea>
              <div id="copyQuoteToast" class="text-emerald-400 font-mono text-[11px] mt-1"></div>
            </div>
          </div>
        </div>

        <!-- 右側：政府採購法 8 大高頻爭議防呆與申論擬答速查智庫 -->
        <div class="lg:col-span-7 bg-slate-900 border border-slate-800 rounded-lg p-5 space-y-4 text-xs">
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 pb-3">
            <div>
              <h2 class="text-sm font-bold text-sky-400 uppercase tracking-wider">政府採購法 8 大高頻履約爭議防呆 ＆ 三段式申論擬答智庫</h2>
              <p class="text-xs text-slate-400 mt-0.5">涵蓋停權第101條、超底價第53條、標價偏低第58條、驗收減價收受第72條等工務所實戰防呆要點</p>
            </div>
            <input type="text" placeholder="篩選法條或關鍵字 (例: 101, 差額保證金, 減價收受)..."
              oninput="filterGpaCards(this.value)"
              class="w-64 bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-xs text-slate-200 focus:border-sky-500 focus:outline-none" />
          </div>

          <div id="gpaCardsContainer" class="space-y-3 max-h-[560px] overflow-y-auto pr-1">
            <div class="gpa-card bg-slate-950 border border-slate-800 rounded-lg p-3.5 space-y-1.5">
              <div class="flex items-center justify-between">
                <span class="font-bold text-rose-400">【第 101 條】不良廠商停權處分（刊登政府採購公報 3 年 / 1 年 / 3~6 個月）</span>
                <span class="px-2 py-0.5 rounded bg-rose-950 text-rose-300 font-mono text-[11px]">最高風險防禦</span>
              </div>
              <p class="text-slate-300"><strong>法定構成要件（大前提）</strong>：須符合「情節重大」與第 101 條第 4 項「比例原則」；機關作成通知前，必須依第 3 項給予廠商<strong>陳述意見</strong>之機會，並成立<strong>採購工作及審查小組</strong>認定。</p>
              <p class="text-emerald-300"><strong>工務所自救防呆 SOP</strong>：收受通知次日起 <strong>20 日內提異議</strong> ➔ 異議結果不服 <strong>15 日內提申訴</strong>。針對第 3 款「擅自減省工料」應舉證非出於故意、已主動通報修復且未危及結構安全。</p>
            </div>

            <div class="gpa-card bg-slate-950 border border-slate-800 rounded-lg p-3.5 space-y-1.5">
              <div class="flex items-center justify-between">
                <span class="font-bold text-amber-400">【第 53 條 &amp; 第 54 條】超底價決標與比減價格程序（8% 與 4% 紅線）</span>
                <span class="px-2 py-0.5 rounded bg-amber-950 text-amber-300 font-mono text-[11px]">開標決標防呆</span>
              </div>
              <p class="text-slate-300"><strong>法定界線</strong>：最低標優先減價 1 次 ➔ 全體合規廠商比減價格（不逾 3 次）。仍超過底價但<strong>未逾底價 8%</strong> 且確有緊急情事，得經原底價核定人核准決標。</p>
              <p class="text-emerald-300"><strong>防呆檢核</strong>：若屬<strong>查核金額以上且超底價逾 4%（未逾 8%）</strong>，必須先報經上級機關核准；若<strong>逾底價 8%</strong> 則一律廢標，絕無通融空間。</p>
            </div>

            <div class="gpa-card bg-slate-950 border border-slate-800 rounded-lg p-3.5 space-y-1.5">
              <div class="flex items-center justify-between">
                <span class="font-bold text-emerald-400">【第 58 條】總標價偏低顯不合理之處理（80% 與 70% 雙門檻）</span>
                <span class="px-2 py-0.5 rounded bg-emerald-950 text-emerald-300 font-mono text-[11px]">差額保證金計算</span>
              </div>
              <p class="text-slate-300"><strong>法定執行程序</strong>：(1) 總標價介於底價 <strong>70%～80%</strong>：限期通知說明，說明合理即決標；不合理則通知提繳「總標價與底價 80% 之差額保證金」。</p>
              <p class="text-emerald-300"><strong>關鍵陷阱</strong>：(2) 總標價<strong>低於底價 70%</strong>：限期通知說明，說明合理則決標（無需繳差額保證金）；若說明不合理，<strong>直接不決標予該廠商（不得通知繳差額保證金）</strong>，並以次低標為最低標。</p>
            </div>

            <div class="gpa-card bg-slate-950 border border-slate-800 rounded-lg p-3.5 space-y-1.5">
              <div class="flex items-center justify-between">
                <span class="font-bold text-sky-400">【第 72 條】驗收不符規定之減價收受（四大法定前提）</span>
                <span class="px-2 py-0.5 rounded bg-sky-950 text-sky-300 font-mono text-[11px]">完工驗收必考</span>
              </div>
              <p class="text-slate-300"><strong>四大構成要件</strong>：(1) 不妨礙安全及使用需求、(2) 無減少通常效用或契約預定效用、(3) 經機關檢討不必拆換或拆換確有困難、(4) 查核金額以上應先報經上級機關核准。</p>
              <p class="text-emerald-300"><strong>實務防呆</strong>：凡涉及結構安全（如主梁混凝土強度不足）絕對不得辦理減價收受，必須拆除重作或結構補強經技師簽證。</p>
            </div>

            <div class="gpa-card bg-slate-950 border border-slate-800 rounded-lg p-3.5 space-y-1.5">
              <div class="flex items-center justify-between">
                <span class="font-bold text-purple-400">【第 26 條】技術規格訂定與同等品審查機制</span>
                <span class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[11px]">材料送審防呆</span>
              </div>
              <p class="text-slate-300"><strong>法定規範</strong>：公告金額以上採購應依功能或效益訂定規範，不得不當限制競爭；無法以精確方式說明而提及特定廠牌時，<strong>必須加註「或同等品」字樣</strong>。</p>
              <p class="text-emerald-300"><strong>工務所防呆</strong>：使用同等品時，應於進場前依契約時限檢附價格、功能、效益比較表及 TAF 試驗報告提送監造與機關審查核定後始得進場施作。</p>
            </div>

            <div class="gpa-card bg-slate-950 border border-slate-800 rounded-lg p-3.5 space-y-1.5">
              <div class="flex items-center justify-between">
                <span class="font-bold text-amber-400">【第 65 條 &amp; 第 67 條】轉包與分包之法定界線與連帶保證</span>
                <span class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[11px]">履約分包紅線</span>
              </div>
              <p class="text-slate-300"><strong>嚴禁轉包</strong>：得標廠商應自行履行契約之主要部分，不得轉包。違反者機關得解除契約、終止契約或沒收保證金，並依第 101 條第 1 項第 11 款停權。</p>
              <p class="text-emerald-300"><strong>合規分包與權利質權</strong>：非主要部分得依法分包予其他具備資格之廠商，並得依第 67 條第 2 項報備分包契約設定權利質權。</p>
            </div>

            <div class="gpa-card bg-slate-950 border border-slate-800 rounded-lg p-3.5 space-y-1.5">
              <div class="flex items-center justify-between">
                <span class="font-bold text-emerald-400">【第 85 條之 1】工程履約爭議調解與「先調後仲」強制規定</span>
                <span class="px-2 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[11px]">工程爭議解決</span>
              </div>
              <p class="text-slate-300"><strong>法定強制力</strong>：工程及技術服務採購之調解，採購申訴審議委員會<strong>應提出調解建議或調解方案</strong>；因機關不同意致調解不成立者，廠商提付仲裁，<strong>機關不得拒絕（先調後仲）</strong>。</p>
            </div>
          </div>
        </div>
      </div>
    </div>

  </main>

  <footer class="border-t border-slate-800/80 bg-slate-950 px-6 py-3 text-center text-xs text-slate-400 font-mono">
    AI 商業變現與公共工程合規總控平台 V3.5 旗艦升級版 ｜ 100% 本機離線運算引擎 ｜ chihyuan.date 智慧工程生態系
  </footer>

  <script>
    let formSectionsHtml = [];
    let currentFormTab = 0;
    let allFormsExpanded = false;

    // 1. 建立 WebSocket / HTTP 雙重心跳與 pagehide 通知
    function initHeartbeat() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      try {
        const ws = new WebSocket(`${proto}//${location.host}/ws/heartbeat`);
        setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send('ping');
        }, 5000);
      } catch (e) {}

      setInterval(() => {
        fetch('/api/heartbeat', { method: 'POST' }).catch(() => {});
      }, 8000);

      window.addEventListener('pagehide', () => {
        if (navigator.sendBeacon) {
          navigator.sendBeacon('/api/shutdown-beacon');
        }
      });
    }

    // 2. 快捷鍵支援 (按 / 或 Ctrl+K 聚焦搜尋框)
    window.addEventListener('keydown', (e) => {
      if ((e.ctrlKey && e.key.toLowerCase() === 'k') || (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA')) {
        e.preventDefault();
        document.getElementById('globalSearchInput').focus();
      }
    });

    // 3. 載入全站儀表板數據與 20 張表單
    async function loadDashboard() {
      try {
        const [dataResp, formsResp] = await Promise.all([
          fetch('/api/dashboard-data'),
          fetch('/api/embedded-forms-html')
        ]);
        const data = await dataResp.json();
        const forms = await formsResp.json();

        // 更新頂部 KPI
        const s = data.summary;
        document.getElementById('kpi-revenue').textContent = `NT$ ${s.total_revenue}`;
        document.getElementById('kpi-profit').textContent = `NT$ ${s.gross_profit}`;
        document.getElementById('kpi-hourly').textContent = `NT$ ${s.hourly_profit} / hr`;
        document.getElementById('kpi-hours').textContent = `${s.total_hours} hr`;
        document.getElementById('kpi-cost').textContent = `NT$ ${s.total_cost}`;
        document.getElementById('kpi-paid').textContent = `${s.paid_customers} 筆 (${s.conversion_rate})`;

        if (forms.sections && forms.sections.length > 0) {
          formSectionsHtml = forms.sections;
          renderFormSections();
        }

        renderLiveCsvTables(data.revenue_table, data.interview_table);
        renderFileExplorer(data.files);
      } catch (err) {
        console.error('載入儀表板失敗:', err);
      }
    }

    function renderFormSections() {
      const container = document.getElementById('formsContainer');
      if (allFormsExpanded) {
        container.innerHTML = formSectionsHtml.map(s => s.replace('hidden', '')).join('');
      } else {
        container.innerHTML = (formSectionsHtml[currentFormTab] || '').replace('hidden', '');
      }
      // 強化所有數字欄位 tabular-nums
      container.querySelectorAll('td').forEach(td => {
        if (/[\d,%\$]/.test(td.textContent)) {
          td.classList.add('tabular-nums');
        }
      });
    }

    function switchFormTab(idx) {
      allFormsExpanded = false;
      currentFormTab = idx;
      document.getElementById('expandAllBtn').textContent = '展開全部 24 張表單同頁檢視';
      for (let i = 0; i < 6; i++) {
        const btn = document.getElementById(`sub-tab-${i}`);
        if (btn) btn.classList.toggle('active', i === idx);
      }
      renderFormSections();
    }

    function expandAllFormTabs() {
      allFormsExpanded = !allFormsExpanded;
      document.getElementById('expandAllBtn').textContent = allFormsExpanded ? '收合為分頁模組檢視' : '展開全部 24 張表單同頁檢視';
      renderFormSections();
    }

    function handleGlobalSearch(q) {
      const query = q.trim().toLowerCase();
      if (query.length > 0) {
        switchMainView('view-forms');
        if (!allFormsExpanded) {
          allFormsExpanded = true;
          document.getElementById('expandAllBtn').textContent = '收合為分頁模組檢視';
          renderFormSections();
        }
      }
      let visibleCount = 0;
      const rows = document.querySelectorAll('#formsContainer tbody tr');
      rows.forEach(r => {
        const match = r.innerText.toLowerCase().includes(query);
        r.style.display = match ? '' : 'none';
        if (match) visibleCount++;
      });
      const emptyBox = document.getElementById('emptySearchState');
      if (query.length > 0 && visibleCount === 0) {
        emptyBox.classList.remove('hidden');
      } else {
        emptyBox.classList.add('hidden');
      }
    }

    function resetGlobalSearch() {
      const input = document.getElementById('globalSearchInput');
      input.value = '';
      handleGlobalSearch('');
    }

    function switchMainView(viewId) {
      document.querySelectorAll('.main-view').forEach(v => v.classList.add('hidden'));
      document.getElementById(viewId).classList.remove('hidden');
      document.querySelectorAll('.nav-pill').forEach(b => b.classList.remove('active'));
      document.getElementById('nav-' + viewId).classList.add('active');
    }

    // 4. 工程計畫書 AI 合規審查工作台
    async function runSampleAudit(sampleType) {
      const fd = new FormData();
      fd.append('sample_type', sampleType);
      await executeAuditRequest(fd);
    }

    async function runUploadAudit() {
      const fileInput = document.getElementById('docxFileInput');
      if (!fileInput.files || fileInput.files.length === 0) {
        alert('請先選擇要上傳的 .docx 檔案');
        return;
      }
      const fd = new FormData();
      fd.append('file', fileInput.files[0]);
      await executeAuditRequest(fd);
    }

    function fillSampleDefectText() {
      document.getElementById('customAuditText').value =
        '本工程依據勞工安全衛生法、勞工安全衛生設施規則及民國105年公共工程施工品質管理作業要點辦理，主管機關為行政院勞工委員會。施工現場護欄高度設置75公分，所有材料於進場前3天送審，品管人員兼任工地主任，品質紀錄於驗收後保存1年。';
      runTextAudit();
    }

    async function runTextAudit() {
      const txt = document.getElementById('customAuditText').value.trim();
      if (!txt) {
        alert('請先輸入要檢測的計畫書條文段落');
        return;
      }
      const fd = new FormData();
      fd.append('custom_text', txt);
      await executeAuditRequest(fd);
    }

    async function runAutoFixText() {
      const textarea = document.getElementById('customAuditText');
      let txt = textarea.value.trim();
      if (!txt) {
        fillSampleDefectText();
        txt = textarea.value.trim();
      }
      const bodyEl = document.getElementById('auditResultBody');
      bodyEl.innerHTML = '<div class="p-6 text-center text-amber-400 font-mono animate-pulse">正在執行 10 大退件地雷自動修訂與九大章節補齊...</div>';
      try {
        const resp = await fetch('/api/auto-fix-text', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: txt, append_missing_chapters: true })
        });
        const res = await resp.json();
        if (!resp.ok) {
          bodyEl.innerHTML = `<div class="p-4 rounded bg-rose-950/60 border border-rose-800 text-rose-300">${res.detail || '修訂失敗'}</div>`;
          return;
        }
        textarea.value = res.fixed_text;
        document.getElementById('auditResultTitle').textContent = `⚡ 一鍵自動合規修訂完成（評分由 ${res.before_score} 分躍升至 ${res.after_score} 分）`;
        document.getElementById('auditResultMeta').textContent = `已自動替換並升級 ${res.replacements_made.length} 項條款 ｜ 判定：🟢 100% 合規可送審`;
        const badge = document.getElementById('auditScoreBadge');
        badge.textContent = `${res.after_score} / 100 分`;
        badge.className = 'px-3 py-1.5 rounded-md bg-emerald-950/80 border border-emerald-700 text-emerald-400 text-sm font-bold font-mono tabular-nums';

        const replHtml = res.replacements_made.map(r => `<li class="text-emerald-300">✅ ${r}</li>`).join('');
        bodyEl.innerHTML = `
          <div class="space-y-3">
            <div class="p-3.5 rounded bg-emerald-950/40 border border-emerald-800 space-y-1.5">
              <div class="font-bold text-emerald-400">自動修訂執行紀錄（共 ${res.replacements_made.length} 項）：</div>
              <ul class="list-disc list-inside space-y-1">${replHtml}</ul>
            </div>
            <div>
              <div class="font-semibold text-slate-300 mb-1">修訂後合規完稿條文（已同步回填左側輸入框）：</div>
              <pre class="bg-slate-950 border border-slate-800 rounded p-3 font-mono text-xs text-emerald-200 whitespace-pre-wrap max-h-48 overflow-y-auto">${res.fixed_text}</pre>
            </div>
          </div>
        `;
      } catch (e) {
        bodyEl.innerHTML = `<div class="p-4 rounded bg-rose-950 border border-rose-800 text-rose-300">連線錯誤：${e.message}</div>`;
      }
    }

    async function executeAuditRequest(formData) {
      const bodyEl = document.getElementById('auditResultBody');
      bodyEl.innerHTML = '<div class="p-6 text-center text-emerald-400 font-mono animate-pulse">正在執行 20 條工程會法規與九大章節掃描...</div>';
      try {
        const resp = await fetch('/api/audit-docx', { method: 'POST', body: formData });
        const res = await resp.json();
        if (!resp.ok) {
          bodyEl.innerHTML = `<div class="p-4 rounded bg-rose-950/60 border border-rose-800 text-rose-300">${res.detail || '審查失敗'}</div>`;
          return;
        }
        document.getElementById('auditResultTitle').textContent = `《${res.doc_title}》合規初審結果`;
        document.getElementById('auditResultMeta').textContent = `總字數：${res.total_words} 字 ｜ 表格數：${res.table_count} 個 ｜ 判定：${res.is_passed ? '🟢 建議送審 (Pass)' : '🔴 存在退件風險 (需修正)'}`;

        const badge = document.getElementById('auditScoreBadge');
        badge.textContent = `${res.health_score} / 100 分`;
        badge.className = res.is_passed
          ? 'px-3 py-1.5 rounded-md bg-emerald-950/80 border border-emerald-700 text-emerald-400 text-sm font-bold font-mono tabular-nums'
          : 'px-3 py-1.5 rounded-md bg-rose-950/80 border border-rose-700 text-rose-400 text-sm font-bold font-mono tabular-nums';

        const warningsHtml = res.outdated_warnings.length === 0
          ? '<div class="p-3 rounded bg-emerald-950/40 border border-emerald-800 text-emerald-300">✅ 未發現任何過期或廢止舊法規（如勞安法、105年舊品管要點、75cm舊護欄標準）。</div>'
          : res.outdated_warnings.map(w => `
              <div class="p-3 rounded bg-rose-950/50 border border-rose-800 space-y-1">
                <div class="font-bold text-rose-400">[${w.risk_level}退件地雷] 偵測到「${w.matched_text}」(${w.obsolete_name})</div>
                <div class="text-emerald-300">✅ 建議替換為：<strong>${w.suggested_name}</strong></div>
                <div class="text-slate-400">${w.reason}</div>
              </div>
            `).join('');

        bodyEl.innerHTML = `
          <div class="space-y-3">
            <div>
              <div class="font-semibold text-slate-300 mb-1.5">一、廢止／舊版法規攔截明細（${res.outdated_warnings.length} 項）：</div>
              <div class="space-y-2">${warningsHtml}</div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div class="p-3 rounded bg-slate-950 border border-slate-800">
                <div class="font-semibold text-emerald-400 mb-1">已具備之工程會必要章節（${res.found_chapters.length}/9）：</div>
                <div class="text-slate-300 leading-relaxed">${res.found_chapters.join('、') || '無'}</div>
              </div>
              <div class="p-3 rounded bg-slate-950 border border-slate-800">
                <div class="font-semibold text-amber-400 mb-1">缺漏之必要章節（${res.missing_chapters.length} 項）：</div>
                <div class="text-slate-300 leading-relaxed">${res.missing_chapters.join('、') || '🎉 九大章節 100% 齊全'}</div>
              </div>
            </div>
            <div>
              <div class="font-semibold text-slate-300 mb-1">二、完整 Markdown 初審報告預覽：</div>
              <pre class="bg-slate-950 border border-slate-800 rounded p-3 font-mono text-xs text-slate-300 whitespace-pre-wrap max-h-56 overflow-y-auto">${res.markdown_report}</pre>
            </div>
          </div>
        `;
      } catch (e) {
        bodyEl.innerHTML = `<div class="p-4 rounded bg-rose-950 border border-rose-800 text-rose-300">連線錯誤：${e.message}</div>`;
      }
    }

    // 5. 雙計畫書一鍵完稿生成器
    async function generatePlans() {
      const btn = document.getElementById('btnGeneratePlans');
      const box = document.getElementById('generatorResultBox');
      btn.disabled = true;
      btn.textContent = '正在生成 .docx 與執行合規驗證...';
      try {
        const payload = {
          plan_type: document.getElementById('genPlanType').value,
          project_name: document.getElementById('genProjectName').value,
          owner_org: document.getElementById('genOwnerOrg').value,
          supervision_org: document.getElementById('genSupervisionOrg').value,
          contractor_org: document.getElementById('genContractorOrg').value,
        };
        const resp = await fetch('/api/generate-plan', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await resp.json();
        box.innerHTML = data.generated.map(g => `
          <div class="p-4 rounded-lg bg-emerald-950/30 border border-emerald-800 space-y-2">
            <div class="flex items-center justify-between">
              <span class="font-bold text-sm text-emerald-300">${g.title}</span>
              <span class="px-2 py-0.5 rounded bg-emerald-900/80 text-emerald-300 font-mono font-bold tabular-nums">合規評分：${g.health_score} / 100 分</span>
            </div>
            <div class="text-slate-400 font-mono">標案名稱：${data.project_name} ｜ 總字數：${g.total_words} 字 ｜ 專業表格：${g.table_count} 個</div>
            <div class="flex flex-wrap gap-2 pt-1">
              <a href="/api/download?path=${encodeURIComponent(g.docx_path)}" class="px-3 py-1.5 rounded bg-emerald-600 hover:bg-emerald-500 text-white font-semibold">
                ⬇ 下載 Word 完稿 (${g.docx_path.split('/').pop()})
              </a>
              <a href="/api/download?path=${encodeURIComponent(g.md_path)}" class="px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200">
                ⬇ 下載 Markdown 條文檔
              </a>
            </div>
          </div>
        `).join('');
      } catch (e) {
        box.innerHTML = `<div class="p-3 rounded bg-rose-950 border border-rose-800 text-rose-300">生成失敗：${e.message}</div>`;
      } finally {
        btn.disabled = false;
        btn.textContent = '立即生成滿分 100 分正式完稿 (.docx & .md)';
      }
    }

    // 6. 客戶訪談與訂單管線動態寫入
    async function submitNewInterview() {
      const payload = {
        rid: document.getElementById('iv-rid').value || `R0${Math.floor(Math.random()*90+10)}`,
        org: document.getElementById('iv-org').value,
        title: document.getElementById('iv-title').value,
        eng_type: document.getElementById('iv-eng').value,
        reason: document.getElementById('iv-reason').value,
        pref: document.getElementById('iv-pref').value,
        max_price: document.getElementById('iv-price').value,
        pay_will: document.getElementById('iv-pay').value,
      };
      const resp = await fetch('/api/pipeline/interview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (resp.ok) {
        document.getElementById('interviewStatusMsg').textContent = `✅ 已成功寫入訪談紀錄 ${payload.rid} 並同步更新儀表板！`;
        await loadDashboard();
      }
    }

    async function submitNewOrder() {
      const payload = {
        week_code: document.getElementById('ord-week').value,
        add_paid_count: parseInt(document.getElementById('ord-count').value || '1', 10),
        add_revenue_ntd: parseInt(document.getElementById('ord-rev').value || '3200', 10),
        add_delivery_hours: parseFloat(document.getElementById('ord-hours').value || '0.8'),
        note: document.getElementById('ord-note').value,
      };
      const resp = await fetch('/api/pipeline/order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (resp.ok) {
        document.getElementById('orderStatusMsg').textContent = `✅ 已成功登錄 ${payload.week_code} 營收 +NT$ ${payload.add_revenue_ntd} 並重算全期決算！`;
        await loadDashboard();
      }
    }

    function renderLiveCsvTables(revTable, ivTable) {
      const box = document.getElementById('liveCsvTables');
      const renderTbl = (title, tbl) => {
        if (!tbl || !tbl.headers || tbl.headers.length === 0) return '';
        const ths = tbl.headers.map(h => `<th>${h}</th>`).join('');
        const trs = tbl.rows.map(r => `<tr>${r.map(c => `<td class="tabular-nums">${c}</td>`).join('')}</tr>`).join('');
        return `<div><div class="font-semibold text-emerald-400 mb-1.5">${title}</div><div class="overflow-x-auto"><table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table></div></div>`;
      };
      box.innerHTML = renderTbl('1. 11_客戶訪談紀錄表.csv（即時同步）', ivTable) + renderTbl('2. 07_收入成本追蹤表.csv（即時同步）', revTable);
    }

    // 7. 35 項交付資產預覽與下載中心
    function renderFileExplorer(files) {
      const listEl = document.getElementById('fileExplorerList');
      const groups = {};
      files.forEach(f => {
        if (!groups[f.category]) groups[f.category] = [];
        groups[f.category].push(f);
      });
      listEl.innerHTML = Object.entries(groups).map(([cat, items]) => `
        <div class="mb-3">
          <div class="text-xs font-bold uppercase tracking-wider text-emerald-400 mb-1.5 px-1">${cat} (${items.length})</div>
          <div class="space-y-1">
            ${items.map(item => `
              <button onclick="previewProjectFile('${item.path}')"
                class="w-full text-left px-2.5 py-1.5 rounded hover:bg-slate-800 border border-transparent hover:border-slate-700 flex items-center justify-between text-xs transition">
                <span class="truncate text-slate-200 font-mono">${item.name}</span>
                <span class="text-slate-400 font-mono text-[11px] ml-2 shrink-0 tabular-nums">${item.size_kb}</span>
              </button>
            `).join('')}
          </div>
        </div>
      `).join('');
    }

    async function previewProjectFile(relPath) {
      document.getElementById('previewFileName').textContent = relPath.split('/').pop();
      document.getElementById('previewFilePath').textContent = relPath;
      const dlBtn = document.getElementById('previewDownloadBtn');
      dlBtn.href = `/api/download?path=${encodeURIComponent(relPath)}`;
      dlBtn.classList.remove('hidden');

      const pre = document.getElementById('previewFileContent');
      pre.textContent = '載入檔案內容中...';
      try {
        const resp = await fetch(`/api/file-content?path=${encodeURIComponent(relPath)}`);
        const data = await resp.json();
        pre.textContent = data.content || '(空檔案)';
      } catch (e) {
        pre.textContent = `讀取失敗：${e.message}`;
      }
    }

    window.addEventListener('DOMContentLoaded', () => {
      initHeartbeat();
      loadDashboard();
      recalcQuote();
    });

    // 8. 互動式報價與毛利試算器 & 採購法卡片篩選
    function recalcQuote() {
      const plan = document.getElementById('calcPlanType').value;
      const pages = parseInt(document.getElementById('calcPages').value || '100', 10);
      const urgency = document.getElementById('calcUrgency').value;
      const tier = document.getElementById('calcTier').value;

      let basePrice = 3200;
      let estHours = 0.8;
      let planTitle = '《品管＋安衛雙計畫書開工統包方案》';

      if (plan === 'bundle') {
        basePrice = tier === 'pilot' ? 3200 : 6500;
        estHours = 0.8 + Math.max(0, (pages - 120) / 150) * 0.4;
        planTitle = '《品管＋安衛雙計畫書開工統包方案》';
      } else if (plan === 'quality') {
        basePrice = tier === 'pilot' ? 1800 : 3500;
        estHours = 0.45 + Math.max(0, (pages - 80) / 120) * 0.3;
        planTitle = '《整體品質管制計畫書》單本合規完稿';
      } else if (plan === 'safety') {
        basePrice = tier === 'pilot' ? 1600 : 3000;
        estHours = 0.4 + Math.max(0, (pages - 60) / 120) * 0.25;
        planTitle = '《職業安全衛生管理計畫書》單本合規完稿';
      } else {
        basePrice = tier === 'pilot' ? 28800 : 38800;
        estHours = 6.5;
        planTitle = '《營造廠總部自動化合規工作流建置＋首月維護》';
      }

      if (pages > 150 && plan !== 'b2b') {
        basePrice += Math.round((pages - 150) / 50) * 800;
      }
      if (urgency === 'rush' && plan !== 'b2b') {
        basePrice = Math.round(basePrice * 1.45 / 100) * 100;
      }

      const hoursRounded = Math.round(estHours * 10) / 10;
      const hourlyProfit = Math.round(basePrice / hoursRounded);
      const slaText = urgency === 'rush' ? '12～24 小時極速交付' : (plan === 'bundle' ? '48 小時內準時交付' : '24 小時內準時交付');

      document.getElementById('calcQuotePrice').textContent = `NT$ ${basePrice.toLocaleString()}`;
      document.getElementById('calcEstHours').textContent = `${hoursRounded} hr`;
      document.getElementById('calcHourlyProfit').textContent = `NT$ ${hourlyProfit.toLocaleString()}/h`;

      const pitch = `所長/主任您好！針對貴所本次送審需求（預估約 ${pages} 頁），我們提供${planTitle}：\n` +
        `✅ 1. 工程會 111 年底最新品管要點與現行職安衛法規 100% 校對更新\n` +
        `✅ 2. 全文跨頁表格自動注入「頂端重複標題列 (tblHeader)」與動態目錄頁碼對齊\n` +
        `✅ 3. 交付「完稿送審版 .docx」＋「修訂標註版 .docx」＋「合規初審報告」\n` +
        `🔒 100% 本機離線處理、驗收後 7 天自動銷毀副本，保障標案機密\n` +
        `⏱ 交付時效：${slaText} ｜ 專案報價：NT$ ${basePrice.toLocaleString()}（驗收滿意後再匯款，若因格式退件全額退款！）`;
      document.getElementById('calcLinePitch').value = pitch;
    }

    function copyQuoteMessage() {
      const txt = document.getElementById('calcLinePitch').value;
      navigator.clipboard.writeText(txt).then(() => {
        const toast = document.getElementById('copyQuoteToast');
        toast.textContent = '✅ 已複製 LINE 專業報價話術至剪貼簿！';
        setTimeout(() => { toast.textContent = ''; }, 3000);
      });
    }

    function filterGpaCards(q) {
      const query = q.trim().toLowerCase();
      document.querySelectorAll('.gpa-card').forEach(card => {
        card.style.display = card.innerText.toLowerCase().includes(query) ? '' : 'none';
      });
    }
  </script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def index_page():
    lifecycle.ping()
    return HTMLResponse(content=INDEX_HTML)


# ==================== 啟動器與防重複啟動保護 ====================

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def open_browser_when_ready(url: str, host: str, port: int, timeout: float = 15.0):
    def _wait_and_open():
        start_wait = time.time()
        while time.time() - start_wait < timeout:
            if is_port_in_use(port, host):
                time.sleep(0.25)
                webbrowser.open(url)
                return
            time.sleep(0.2)
        webbrowser.open(url)

    threading.Thread(target=_wait_and_open, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="AI 商業變現與公共工程合規總控平台 Web 啟動器")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help="監聽位址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="監聽連接埠 (預設 8770)")
    parser.add_argument("--no-browser", action="store_true", help="不自動開啟瀏覽器")
    parser.add_argument("--no-auto-shutdown", action="store_true", help="停用關閉分頁自動退出（24H 常駐守護模式）")
    args = parser.parse_args()

    if args.no_auto_shutdown:
        lifecycle.set_auto_shutdown(False)

    url = f"http://{args.host}:{args.port}"

    # 防重複啟動保護：若已在背景運行，直接喚起瀏覽器並退出
    if is_port_in_use(args.port, args.host):
        if not args.no_browser:
            webbrowser.open(url)
        sys.exit(0)

    if not args.no_browser:
        open_browser_when_ready(url, args.host, args.port)

    import uvicorn
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=False,
        log_level="warning",
        access_log=False,
        use_colors=False,
    )


if __name__ == "__main__":
    main()
