"""
工程計畫書合規檢查核心模組 (ComplianceChecker)
支援 DOCX 檔案與文字串流之法規比對、過期條文攔截與章節完整度評估。
"""
from dataclasses import dataclass, field
import os
import re
from typing import Dict, List, Optional

try:
    import docx
except ImportError:
    docx = None

from .compliance_rules import (
    VALID_REGULATIONS,
    OUTDATED_PATTERNS,
    MANDATORY_QUALITY_CHAPTERS,
)


@dataclass
class OutdatedFinding:
    """過期法規偵測結果"""
    matched_text: str
    risk_level: str
    obsolete_name: str
    suggested_name: str
    reason: str


@dataclass
class ComplianceReport:
    """合規檢查報告資料模型"""
    total_words: int
    health_score: int  # 0~100 分
    valid_regulations_found: List[str] = field(default_factory=list)
    outdated_warnings: List[OutdatedFinding] = field(default_factory=list)
    found_chapters: List[str] = field(default_factory=list)
    missing_chapters: List[str] = field(default_factory=list)
    table_count: int = 0
    issues_summary: List[str] = field(default_factory=list)

    @property
    def is_passed(self) -> bool:
        """判定是否通過（無嚴重過期法規且必要章節覆蓋率達 80% 以上）"""
        has_severe_risk = any(w.risk_level == "嚴重" for w in self.outdated_warnings)
        return not has_severe_risk and len(self.missing_chapters) <= 2


class ComplianceChecker:
    """工程計畫書合規性檢查器"""

    def __init__(self):
        self.valid_regs = VALID_REGULATIONS
        self.outdated_patterns = OUTDATED_PATTERNS
        self.mandatory_chapters = MANDATORY_QUALITY_CHAPTERS

    def check_text(self, text: str) -> ComplianceReport:
        """
        掃描傳入之文字內容，執行法規與章節合規檢查
        :param text: 計畫書正文字串
        :return: ComplianceReport
        """
        if not text or not text.strip():
            raise ValueError("輸入文字不得為空或僅包含空白字元")

        clean_text = text.strip()
        total_words = len(clean_text)

        # 1. 偵測有效現行法規
        found_valid = []
        for reg_name, reg_info in self.valid_regs.items():
            for kw in reg_info["keywords"]:
                if kw in clean_text:
                    found_valid.append(f"{reg_name} ({reg_info['latest_version']})")
                    break

        # 2. 偵測過期或已廢止法規
        outdated_findings = []
        for item in self.outdated_patterns:
            matches = re.finditer(item["pattern"], clean_text)
            for m in matches:
                outdated_findings.append(
                    OutdatedFinding(
                        matched_text=m.group(0),
                        risk_level=item["risk_level"],
                        obsolete_name=item["obsolete_name"],
                        suggested_name=item["suggested_name"],
                        reason=item["reason"],
                    )
                )

        # 3. 掃描必要章節覆蓋情形
        found_chapters = []
        missing_chapters = []
        for ch in self.mandatory_chapters:
            matched = any(kw in clean_text for kw in ch["keywords"])
            if matched:
                found_chapters.append(f"第{ch['chapter_num']}章 {ch['title']}")
            else:
                missing_chapters.append(f"第{ch['chapter_num']}章 {ch['title']}")

        # 4. 計算合規健康度評分 (滿分 100)
        # 扣分權重：嚴重過期法規各 -20 分、中度過期法規各 -10 分、缺漏必要章節各 -8 分
        score = 100
        issues = []

        for w in outdated_findings:
            if w.risk_level == "嚴重":
                score -= 20
                issues.append(f"【嚴重退件風險】引用已廢止名稱：{w.obsolete_name} ➔ 建議替換為：{w.suggested_name}")
            else:
                score -= 10
                issues.append(f"【法規版本警告】引用舊版條文：{w.obsolete_name} ➔ 建議替換為：{w.suggested_name}")

        for mc in missing_chapters:
            score -= 8
            issues.append(f"【架構缺漏】未檢測到必要章節關鍵字：{mc}")

        # 最低分限制為 0 分
        health_score = max(0, min(100, score))

        return ComplianceReport(
            total_words=total_words,
            health_score=health_score,
            valid_regulations_found=found_valid,
            outdated_warnings=outdated_findings,
            found_chapters=found_chapters,
            missing_chapters=missing_chapters,
            table_count=0,
            issues_summary=issues,
        )

    def check_docx(self, file_path: str) -> ComplianceReport:
        """
        解析 Word (.docx) 檔案並執行合規檢查
        :param file_path: 本機 Word 檔案絕對或相對路徑
        :return: ComplianceReport
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"找不到指定的檔案：{file_path}")

        if not file_path.lower().endswith(".docx"):
            raise ValueError(f"僅支援 .docx 格式，目前輸入為：{file_path}")

        if docx is None:
            raise ImportError("未安裝 python-docx 套件，請先安裝以支援 Word 解析")

        doc = docx.Document(file_path)
        full_text = []

        # 提取所有段落文字
        for p in doc.paragraphs:
            if p.text:
                full_text.append(p.text)

        # 提取所有表格文字
        table_count = len(doc.tables)
        for tbl in doc.tables:
            for row in tbl.rows:
                for cell in row.cells:
                    if cell.text:
                        full_text.append(cell.text)

        combined_text = "\n".join(full_text)
        report = self.check_text(combined_text)
        report.table_count = table_count
        return report

    @staticmethod
    def generate_markdown_summary(report: ComplianceReport, file_name: str = "送審計畫書") -> str:
        """
        將檢查結果轉換為供工程師覆核與客戶展示之 Markdown 摘要報告
        """
        status_label = "🟢 建議送審（符合規範）" if report.is_passed else "🔴 建議修正後再送審（存在退件風險）"

        md = [
            f"# 《{file_name}》合規檢查與格式初審報告",
            f"",
            f"- **總字數**：{report.total_words} 字",
            f"- **表格總數**：{report.table_count} 個",
            f"- **合規健康度評分**：**{report.health_score} / 100 分**",
            f"- **初審結論**：{status_label}",
            f"",
            f"---",
            f"",
            f"## 一、 偵測到之現行有效法規（{len(report.valid_regulations_found)} 項）",
        ]

        if report.valid_regulations_found:
            for reg in report.valid_regulations_found:
                md.append(f"- ✅ {reg}")
        else:
            md.append("- ⚠️ 未偵測到常用公共工程法規引述。")

        md.extend([
            f"",
            f"## 二、 廢止／舊版法規攔截與修正建議（{len(report.outdated_warnings)} 項）",
        ])

        if report.outdated_warnings:
            for w in report.outdated_warnings:
                md.append(f"- ❌ **[{w.risk_level}]** 偵測到 `{w.matched_text}`")
                md.append(f"  - **建議替換為**：`{w.suggested_name}`")
                md.append(f"  - **退件風險說明**：{w.reason}")
        else:
            md.append("- 🎉 太棒了！未發現任何過期或廢止舊法規。")

        md.extend([
            f"",
            f"## 三、 工程會品管九大必要章節覆蓋情形",
            f"- **已包含章節**（{len(report.found_chapters)} 項）：{', '.join(report.found_chapters) if report.found_chapters else '無'}",
            f"- **缺漏章節**（{len(report.missing_chapters)} 項）：{', '.join(report.missing_chapters) if report.missing_chapters else '全部具備'}",
            f"",
            f"---",
            f"*(本報告由公共工程 AI 合規引擎自動產生，僅供格式與行政法規初篩，實質工法仍需由專案技師覆核。)*",
        ])

        return "\n".join(md)
