"""資料去識別化驗收：缺真實 CSV 時以範例補齊、本機真實檔案不被覆寫、範例資料可驅動儀表板 API。"""
import importlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TARGETS = {
    "07_收入成本追蹤表.csv": "07_收入成本追蹤表.csv",
    "11_客戶訪談紀錄表.csv": "output/11_客戶訪談紀錄表.csv",
    "13_客戶需求驗證儀表板.csv": "output/13_客戶需求驗證儀表板.csv",
}


def _stage(tmp_path, with_real=None):
    """複製最小可執行專案到暫存目錄，模擬 Render 全新容器。"""
    dst = tmp_path / "proj"
    def _ignore(directory, names):
        # 只排除專案根目錄的真實檔；sample_data 內同名範例檔必須保留
        skip = set(shutil.ignore_patterns(".git", "__pycache__", "output", ".pytest_cache", "runtime")(directory, names))
        if Path(directory) == ROOT:
            skip.add("07_收入成本追蹤表.csv")
        return skip

    shutil.copytree(ROOT, dst, ignore=_ignore)
    (dst / "output").mkdir(exist_ok=True)
    if with_real:
        for rel, text in with_real.items():
            p = dst / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    return dst


def _load(dst):
    for m in [m for m in sys.modules if m == "web_app"]:
        del sys.modules[m]
    sys.path.insert(0, str(dst))
    try:
        return importlib.import_module("web_app")
    finally:
        sys.path.remove(str(dst))


def test_sample_data_is_non_real_and_complete():
    for name in TARGETS:
        text = (ROOT / "sample_data" / name).read_text(encoding="utf-8-sig")
        assert "範例" in text
    assert "營造廠(甲級)" not in (ROOT / "sample_data" / "11_客戶訪談紀錄表.csv").read_text(encoding="utf-8-sig")


def test_missing_real_files_are_bootstrapped_from_samples(tmp_path):
    dst = _stage(tmp_path)
    _load(dst)
    for rel in TARGETS.values():
        assert (dst / rel).exists(), rel


def test_existing_real_file_never_overwritten(tmp_path):
    marker = "REAL-DATA-MARKER"
    dst = _stage(tmp_path, with_real={"07_收入成本追蹤表.csv": marker})
    _load(dst)
    assert (dst / "07_收入成本追蹤表.csv").read_text(encoding="utf-8") == marker


def test_dashboard_api_works_on_sample_data(tmp_path):
    from starlette.testclient import TestClient
    dst = _stage(tmp_path)
    mod = _load(dst)
    r = TestClient(mod.app).get("/api/dashboard-data")
    assert r.status_code == 200
    assert r.json()
