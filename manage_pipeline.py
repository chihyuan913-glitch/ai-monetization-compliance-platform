"""
AI 商業變現專案：客戶訪談與訂單動態管理工具 (Pipeline Manager)
支援 Windows 終端編碼，零外部依賴，一鍵同步 11 號訪談表與 13 號需求儀表板。
"""
import os
import sys
import csv
from datetime import datetime

# 設定終端編碼防呆 (相容 Windows cp950 與 UTF-8)
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

current_dir = os.path.dirname(os.path.abspath(__file__))
output_dir = os.path.join(current_dir, "output")
csv_interview = os.path.join(output_dir, "11_客戶訪談紀錄表.csv")
csv_dashboard = os.path.join(output_dir, "13_客戶需求驗證儀表板.csv")


def view_dashboard():
    print("\n" + "=" * 65)
    print(" 【當前市場需求與獲利驗證儀表板】")
    print("=" * 65)
    if not os.path.exists(csv_dashboard):
        print(f"[ERROR] 找不到檔案: {csv_dashboard}")
        return

    with open(csv_dashboard, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        print(f"{'指標類別':<10} | {'指標名稱':<24} | {'目標值':<8} | {'實際值':<8} | {'達成率':<8}")
        print("-" * 68)
        for row in reader:
            if len(row) >= 6:
                cat, name, unit, target, actual, rate = row[0], row[1], row[2], row[3], row[4], row[5]
                print(f"{cat:<10} | {name:<24} | {target:<8} | {actual:<8} | {rate:<8}")
    print("=" * 65)


def view_interviews():
    print("\n" + "=" * 65)
    print(" 【已記錄之訪談名單與反饋清單】")
    print("=" * 65)
    if not os.path.exists(csv_interview):
        print(f"[ERROR] 找不到檔案: {csv_interview}")
        return

    with open(csv_interview, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader, None)
        count = 0
        for row in reader:
            if row:
                count += 1
                rid = row[0]
                org = row[1] if len(row) > 1 else ""
                title = row[2] if len(row) > 2 else ""
                pref = row[10] if len(row) > 10 else ""
                willingness = row[13] if len(row) > 13 else ""
                status = row[16] if len(row) > 16 else ""
                print(f"[{count}] 編號: {rid} | 機構: {org} | 職稱: {title} | 偏好: {pref} | 試用意願: {willingness} ({status})")
        if count == 0:
            print("目前尚未有任何訪談紀錄。")
    print("=" * 65)


def add_interview_record():
    print("\n" + "=" * 65)
    print(" 【新增客戶訪談紀錄 (同步寫入 CSV)】")
    print("=" * 65)
    
    # 互動式輸入 (支援 Enter 預設值)
    rid = input("請輸入受訪編號 (例: R01): ").strip() or f"R{datetime.now().strftime('%m%d%H%M')}"
    org = input("機構性質 [1.營造廠 2.顧問公司 3.事務所 4.其他] (預設1): ").strip()
    org_map = {"1": "營造廠", "2": "工程技術顧問公司", "3": "技師事務所", "4": "其他"}
    org = org_map.get(org, "營造廠")
    
    title = input("受訪者職稱 (例: 工務所長/監造主任/主任技師): ").strip() or "工務主管"
    eng_type = input("主要工程類型 (例: 建築/土木/水利/捷運): ").strip() or "土木建築工程"
    date_str = datetime.now().strftime("%Y/%m/%d")
    
    doc_count = input("每月處理文件量 [1.1本以下 2.2~3本 3.4~6本 4.7本以上] (預設2): ").strip()
    doc_map = {"1": "1本以下", "2": "2~3本", "3": "4~6本", "4": "7本以上"}
    doc_count = doc_map.get(doc_count, "2~3本")
    
    hours = input("單份計畫書調排版耗時 [1.2H內 2.半天 3.1~2天 4.3天以上] (預設3): ").strip()
    hours_map = {"1": "2小時以內", "2": "半天(4H)", "3": "1~2天(8~16H)", "4": "3天以上"}
    hours = hours_map.get(hours, "1~2天(8~16H)")
    
    reason = input("最常被退件原因 (例: 法規過期/表格跑版/目錄跳頁): ").strip() or "法規過期與跨頁表格跑版"
    offline_req = input("是否要求 100% 離線處理 (y/n, 預設y): ").strip().lower()
    offline_req = "必須100%本機離線" if offline_req != "n" else "去識別化可接受雲端"
    
    pref = input("方案偏好 [1.方案A代客排版 2.方案B買斷工具包 3.雙本統包] (預設1): ").strip()
    pref_map = {"1": "方案A代排初檢", "2": "方案B買斷工具包", "3": "雙計畫書統包"}
    pref = pref_map.get(pref, "方案A代排初檢")
    
    price_str = input("價格接受上限 NTD (例: 1800/3500/3200): ").strip() or "1800"
    trial_willing = input("是否願意試用 [1.高度願意 2.看樣張後考慮 3.暫無需求] (預設1): ").strip()
    trial_map = {"1": "高度願意(待排程)", "2": "看樣張後考慮", "3": "暫無需求"}
    trial_willing = trial_map.get(trial_willing, "高度願意(待排程)")
    
    actual_pay = "有意願以試行價付費" if trial_willing == "高度願意(待排程)" else "待後續確認"
    
    new_row = [
        rid, org, title, eng_type, date_str, doc_count, hours, reason,
        "工務加班手動改", offline_req, pref, price_str, "監造審查標準主觀",
        trial_willing, "願意保持聯絡", actual_pay, "真實訪談完成"
    ]
    
    with open(csv_interview, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(new_row)
        
    print(f"\n[OK] 訪談紀錄已成功寫入: {csv_interview}")
    
    # 自動連動更新儀表板中的指標
    update_dashboard_counts(has_need=True, willing_trial=(trial_willing == "高度願意(待排程)"))


def update_dashboard_counts(has_need=True, willing_trial=True):
    """更新儀表板接觸數與訪談數"""
    if not os.path.exists(csv_dashboard):
        return
        
    rows = []
    with open(csv_dashboard, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        headers = next(reader)
        rows.append(headers)
        for row in reader:
            if not row:
                continue
            name = row[1]
            if name == "接觸潛在客戶數":
                curr = int(row[4]) + 1
                row[4] = str(curr)
                row[5] = f"{(curr / float(row[3])) * 100:.2f}%"
            elif name == "完成有效訪談數":
                curr = int(row[4]) + 1
                row[4] = str(curr)
                row[5] = f"{(curr / float(row[3])) * 100:.2f}%"
            elif name == "有明確痛點與需求的客戶數" and has_need:
                curr = int(row[4]) + 1
                row[4] = str(curr)
                row[5] = f"{(curr / float(row[3])) * 100:.2f}%"
            elif name == "願意參與試用的客戶數" and willing_trial:
                curr = int(row[4]) + 1
                row[4] = str(curr)
                row[5] = f"{(curr / float(row[3])) * 100:.2f}%"
            rows.append(row)
            
    with open(csv_dashboard, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)
    print("[OK] 客戶需求驗證儀表板數據已連動更新！")


def record_paid_order():
    print("\n" + "=" * 65)
    print(" 【登記正式付費訂單 (計算毛利與營收)】")
    print("=" * 65)
    
    order_name = input("委託案名或客戶稱呼 (例: 北部某標案整體品質計畫): ").strip() or "示範標案送審計畫書"
    amount = float(input("成交金額 NTD (例: 1800 或 3200): ").strip() or "1800")
    cost = float(input("直接成本支出 NTD (例: 0): ").strip() or "0")
    hours = float(input("實質投入交付工時 (小時, 例: 0.77): ").strip() or "0.77")
    
    margin = amount - cost
    margin_per_hour = margin / hours if hours > 0 else 0
    
    print("-" * 65)
    print(f"✅ 訂單登記完成！")
    print(f"   - 案名: {order_name}")
    print(f"   - 營收: NT$ {amount:,.0f} 元")
    print(f"   - 貢獻毛利: NT$ {margin:,.0f} 元")
    print(f"   - 實質工時: {hours:.2f} 小時")
    print(f"   - 每小時貢獻毛利: NT$ {margin_per_hour:,.0f} / hr")
    print("-" * 65)
    
    # 同步回寫儀表板
    if os.path.exists(csv_dashboard):
        rows = []
        with open(csv_dashboard, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            headers = next(reader)
            rows.append(headers)
            for row in reader:
                if not row:
                    continue
                name = row[1]
                if name == "實際付費委託客戶數":
                    curr = int(row[4]) + 1
                    row[4] = str(curr)
                    row[5] = f"{(curr / float(row[3])) * 100:.2f}%"
                elif name == "實際入帳收入":
                    curr = float(row[4]) + amount
                    row[4] = str(int(curr))
                    row[5] = f"{(curr / float(row[3])) * 100:.2f}%"
                elif name == "累計交付與支援工時":
                    curr = float(row[4]) + hours
                    row[4] = f"{curr:.2f}"
                    row[5] = f"{(curr / float(row[3])) * 100:.2f}%"
                elif name == "每小時貢獻毛利":
                    row[4] = f"{int(margin_per_hour)}"
                    row[5] = f"{(margin_per_hour / float(row[3])) * 100:.2f}%"
                rows.append(row)
                
        with open(csv_dashboard, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(rows)
        print("[OK] 儀表板財務與毛利數據已即時同步更新！")


def main_menu():
    while True:
        print("\n" + "=" * 65)
        print("  AI 商業變現專案：客戶訪談與訂單管理系統 (Pipeline Manager)")
        print("=" * 65)
        print(" [1] 檢視當前需求驗證儀表板 (Dashboard)")
        print(" [2] 檢視所有客戶訪談紀錄名單 (Interview List)")
        print(" [3] 新增一筆客戶訪談紀錄 (Add Interview)")
        print(" [4] 登記一筆正式付費試行訂單 (Record Order)")
        print(" [0] 結束離開")
        print("-" * 65)
        
        choice = input("請選擇功能 [0-4]: ").strip()
        if choice == "1":
            view_dashboard()
        elif choice == "2":
            view_interviews()
        elif choice == "3":
            add_interview_record()
        elif choice == "4":
            record_paid_order()
        elif choice == "0":
            print("\n感謝使用，系統安全退出。")
            break
        else:
            print("\n[WARN] 無效選項，請重新輸入。")


if __name__ == "__main__":
    main_menu()
