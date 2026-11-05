# AI 商業變現與公共工程合規總控平台 - 專案規範與工作指引 (AGENTS.md)

## 一、專案入口與核心定位
- **專案名稱**：AI 商業變現與公共工程合規總控平台 (Project-E-Hybrid-Monetization V3.0)
- **主要工作目錄**：`G:\我的雲端硬碟\AI 變現\E_混合型_AI變現總控\`
- **外部穿透網域**：`https://monetize.chihyuan.date`（亦可由 `https://chihyuan.date/monetize` 轉址直達）
- **本機通訊埠**：**Port 8770**
- **核心功能**：
  1. 全生命週期 20 張表單總控戰情室（含 W01~W12 財務決算、訪談問卷、委託合約、驗收單與 30 避坑全表）。
  2. 公共工程計畫書 AI 合規審查工作台（支援上傳 `.docx`、內建樣本測試與段落文字診斷）。
  3. 品管＋安衛「雙計畫書開工統包」一鍵完稿生成中心（即時產生 100/100 分 `.docx` 與 `.md`）。
  4. 客戶訪談與訂單管線動態管理系統（即時讀寫 CSV 並重算驗證儀表板）。
  5. 全案 35 項交付資產線上預覽與下載中心。

## 二、核心工作準則
1. **本機生命週期與網域 24H 自癒守護**：
   - 桌面啟動捷徑（`AI商業變現與工程合規總控平台.lnk`）透過 `start_silent.vbs` 無黑窗喚起。
   - 網域服務納管於 `G:\我的雲端硬碟\chihyuan_domain_watchdog.py`，帶入 `--no-browser --no-auto-shutdown` 確保外網 24H 零 502。
2. **防碎片與資安合規**：
   - 測試與執行強制帶入 `PYTHONDONTWRITEBYTECODE=1` 與 `-p no:cacheprovider`。
   - 檔案預覽與下載端點嚴格檢查 `resolve().is_relative_to(BASE_DIR)`，阻絕路徑穿越攻擊。
