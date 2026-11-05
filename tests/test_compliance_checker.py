"""
公共工程合規檢查引擎單元測試套件
驗證法規比對、廢止法規攔截、必要章節審查與 Word 解析能力。
"""
import os
import pytest
from src.checker import ComplianceChecker, ComplianceReport


@pytest.fixture
def checker():
    return ComplianceChecker()


def test_valid_compliance_plan(checker):
    """TC-01：驗證結構完整且法規最新的合規計畫書"""
    sample_text = """
    第一章 計畫編製說明與工程概述
    本工程依據行政院公共工程委員會最新頒定之公共工程施工品質管理作業要點及職業安全衛生法相關規定辦理。
    
    第二章 品質管理組織及職責
    工務所設專任主任技師與專職品管人員，職責明確劃分。
    
    第三章 施工要領與作業程序
    針對鋼筋、模板與混凝土制定施工程序與作業流程圖。
    
    第四章 品質管理標準
    參照營造安全衛生設施標準與契約規範訂定施工檢驗標準。
    
    第五章 材料及施工檢驗程序
    訂定材料送審與抽驗程序，確保進場試驗合格。
    
    第六章 自主檢查與管制措施
    工務人員按各施工分項逐一填寫自主檢查表。
    
    第七章 不合格品之管制與矯正預防
    發現施工不合格品立即開立缺失改善通知單並隔離。
    
    第八章 矯正與預防措施
    定期召開品質檢討會議，提出矯正措施避免再犯。
    
    第九章 文件紀錄管理系統與品質稽核
    工程日誌與各項試驗紀錄由品管人員保存歸檔。
    """
    report = checker.check_text(sample_text)
    assert report.health_score >= 90
    assert report.is_passed is True
    assert len(report.outdated_warnings) == 0
    assert len(report.missing_chapters) == 0
    assert len(report.valid_regulations_found) >= 3


def test_outdated_regulations_detection(checker):
    """TC-02：驗證廢止與舊版法規之攔截與代換建議"""
    sample_text = """
    本計畫依據民國95年舊版公共工程施工品質管理作業要點與勞工安全衛生法訂定。
    工地應遵守勞工安全衛生設施規則相關條文。
    """
    report = checker.check_text(sample_text)
    assert report.is_passed is False
    assert len(report.outdated_warnings) >= 2

    # 檢查是否精準辨識出廢止法規與替換建議
    obsolete_names = [w.obsolete_name for w in report.outdated_warnings]
    assert "勞工安全衛生法" in obsolete_names

    severe_risks = [w for w in report.outdated_warnings if w.risk_level == "嚴重"]
    assert len(severe_risks) >= 1
    assert any("職業安全衛生法" in w.suggested_name for w in severe_risks)


def test_missing_chapters(checker):
    """TC-03：驗證必要章節缺漏時之警示與扣分"""
    sample_text = """
    第一章 工程概述。本工程為一般修繕。
    第二章 品質組織。由工地主任兼任。
    """
    report = checker.check_text(sample_text)
    assert len(report.missing_chapters) >= 6
    assert report.health_score < 70
    assert any("自主檢查" in ch for ch in report.missing_chapters)


def test_empty_input_validation(checker):
    """TC-04：驗證空輸入之防呆異常處理"""
    with pytest.raises(ValueError, match="輸入文字不得為空"):
        checker.check_text("")

    with pytest.raises(ValueError, match="輸入文字不得為空"):
        checker.check_text("   \n\t  ")


def test_docx_file_parsing(checker, tmp_path):
    """TC-05：驗證真實 DOCX 檔案之讀取、表格分析與法規檢驗"""
    import docx

    test_file = tmp_path / "test_plan.docx"
    doc = docx.Document()
    doc.add_heading("第一章 工程概述與編製說明", level=1)
    doc.add_paragraph("本案依據公共工程施工品質管理作業要點與職業安全衛生法辦理。")

    # 建立一個測試表格
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "自主檢查項目"
    table.cell(0, 1).text = "品質標準"
    table.cell(1, 0).text = "鋼筋綁紮"
    table.cell(1, 1).text = "符合材料檢驗程序"

    doc.save(str(test_file))

    report = checker.check_docx(str(test_file))
    assert report.table_count == 1
    assert report.total_words > 0
    assert len(report.valid_regulations_found) >= 2


def test_markdown_summary_generation(checker):
    """TC-06：驗證產出 Markdown 覆核報告之格式"""
    sample_text = "本計畫書依據職業安全衛生法辦理，包含自主檢查措施。"
    report = checker.check_text(sample_text)
    summary_md = ComplianceChecker.generate_markdown_summary(report, "大直橋維護工程品質計畫書")

    assert "# 《大直橋維護工程品質計畫書》合規檢查與格式初審報告" in summary_md
    assert "合規健康度評分" in summary_md
    assert "職業安全衛生法" in summary_md


def test_v35_extended_outdated_patterns(checker):
    """TC-07：驗證 V3.5 擴充之 10 大退件地雷（75cm舊護欄、勞委會、3天送審、保存1年、品管兼任）"""
    sample_text = """
    本工程主管機關為行政院勞工委員會，開口護欄高度設置75公分，
    材料於進場前3天送審，品管人員兼任工地主任，品質紀錄保存1年。
    """
    report = checker.check_text(sample_text)
    assert report.is_passed is False
    assert len(report.outdated_warnings) >= 5
    suggested_text = " ".join(w.suggested_name for w in report.outdated_warnings)
    assert "勞動部" in suggested_text
    assert "90 公分" in suggested_text
    assert "30 日" in suggested_text
    assert "5 年" in suggested_text
    assert "專職" in suggested_text

