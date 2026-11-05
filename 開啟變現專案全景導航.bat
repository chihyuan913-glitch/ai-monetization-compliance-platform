@echo off
chcp 65001 >nul
title AI 商業變現總控 - 全景導航中控台 (V3.0 圓滿結案)
cls
echo ============================================================
echo   AI 商業變現專案 V3.0 - 全生命週期圓滿結案與導航中控台
echo   主引擎：工程雙計畫書代排與月維護 ｜ 輔引擎：採購法數位資產
echo   全期決算：營收 NT$ 148,870 ｜ 毛利 NT$ 143,922 ｜ 時薪 NT$ 4,628/hr
echo ============================================================
echo.
echo [1] 開啟《全景導航地圖與快速取用手冊》(23)
echo [2] 開啟《A4 一頁紙客戶展示對照單》(16)
echo [3] 開啟《10 位同業口袋名單與話術庫》(17)
echo [4] 開啟《客戶訪談問卷與紀錄表》(10 & 11)
echo [5] 執行《合規檢查引擎 15 秒診斷示範》(Demo)
echo [6] 開啟《專案終極里程碑交付手冊》(26)
echo [7] 啟動《客戶訪談與訂單進度管理系統》(Pipeline Manager)
echo [8] 開啟《常見退件 30 避坑實戰手冊》(29 - 破冰見面禮)
echo [9] 開啟《全生命週期完工決算與結案報告書》(34 - 終極結案)
echo [10] 開啟《專案全生命週期戰情室與全表單總控儀表板》(35 - 離線HTML版)
echo [11] 啟動《Web UI 機構級合規總控工作台》(Port 8770 / monetize.chihyuan.date)
echo [0] 結束離開
echo.
set /p opt="請輸入選項編號 [0-11]: "

if "%opt%"=="1" (
    start "" "output\23_專案全景導航地圖與快速取用手冊.md"
) else if "%opt%"=="2" (
    start "" "output\16_客戶展示專用_A4一頁紙示範對照單.md"
) else if "%opt%"=="3" (
    start "" "output\17_訪談名單建立與邀約話術庫.md"
) else if "%opt%"=="4" (
    start "" "output\10_客戶訪談問卷.md"
    start "" "output\11_客戶訪談紀錄表.csv"
) else if "%opt%"=="5" (
    cls
    python "digital_product_mvp\run_audit_tool.py" --demo
    echo.
    pause
) else if "%opt%"=="6" (
    start "" "output\26_專案終極里程碑交付手冊_V1.8.md"
) else if "%opt%"=="7" (
    cls
    python "manage_pipeline.py"
) else if "%opt%"=="8" (
    start "" "output\29_公共工程計畫書送審常見退件30避坑實戰手冊.md"
) else if "%opt%"=="9" (
    start "" "output\34_AI商業變現專案全生命週期完工決算與結案報告書.md"
) else if "%opt%"=="10" (
    start "" "output\35_專案全生命週期戰情室儀表板.html"
) else if "%opt%"=="11" (
    wscript.exe "start_silent.vbs"
)

exit /b 0
