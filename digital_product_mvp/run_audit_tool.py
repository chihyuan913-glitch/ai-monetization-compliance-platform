"""
公共工程計畫書 AI 一鍵合規檢查工具 (PCC-Plan Audit Tool)
免聯網、零機敏外洩風險之命令列診斷工具。
"""
import sys
import os

# 設定終端編碼防呆 (相容 Windows cp950 與 UTF-8)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 自動定位並加入父層模組路徑
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from src.checker import ComplianceChecker


def run():
    print("=" * 60)
    print(" 歡迎使用《公共工程計畫書 AI 合規加速工具》(PCC-Plan Tool)")
    print(" 適用於：工程會最新品管作業要點、職安法規與標準大綱診斷")
    print("=" * 60)

    if len(sys.argv) > 1:
        target_path = sys.argv[1]
    else:
        # 若非互動式環境，預設執行 demo
        if not sys.stdin or not sys.stdin.isatty():
            target_path = "--demo"
        else:
            target_path = input("\n請輸入欲檢查的 Word 檔案路徑 (.docx) 或按 Enter 執行內建示範測試：").strip()

    checker = ComplianceChecker()

    if not target_path or target_path == "--demo":
        print("\n[INFO] 未指定檔案或啟用 --demo，啟動內建工程會範例文本檢查...")
        demo_text = """
        第一章 計畫編製說明與工程概述
        本工程依據民國105年公共工程施工品質管理作業要點與勞工安全衛生法訂定。
        第二章 品質管理組織及職責
        設專任主任技師與專職品管人員。
        第三章 施工要領與作業程序
        規範各項工法與作業要領。
        第四章 品質管理標準
        材料與施工檢驗標準。
        """
        report = checker.check_text(demo_text)
        file_name = "示範標案整體施工計畫書"
        output_file = os.path.join(current_dir, "示範標案_合規初審報告.md")
    else:
        # 去除引號防呆
        target_path = target_path.strip('"').strip("'")
        if not os.path.exists(target_path):
            print(f"\n[ERROR] 找不到檔案：{target_path}")
            return 1
        
        file_name = os.path.basename(target_path)
        print(f"\n[INFO] 正在解析並掃描文件：{file_name} ...")
        report = checker.check_docx(target_path)
        output_file = os.path.splitext(target_path)[0] + "_合規初審報告.md"

    # 生成 Markdown 報告
    summary_md = checker.generate_markdown_summary(report, file_name)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(summary_md)

    print("\n" + "-" * 60)
    print(f"[OK] 檢查完成！合規健康度評分：{report.health_score} / 100 分")
    print(f"[RESULT] 審查結論：{'建議送審' if report.is_passed else '存在退件風險，請參閱修正報告'}")
    print(f"[REPORT] 完整診斷報告已匯出至：\n   {output_file}")
    print("-" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(run())

