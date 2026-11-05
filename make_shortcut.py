#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
建立桌面單一無黑窗啟動圖示 (make_shortcut.py)
落實《本機 Web 應用程式生命週期全域規範》：
- 產生專屬高解析度 app_icon.ico
- 於桌面建立單一啟動捷徑「AI商業變現與工程合規總控平台.lnk」，指向 wscript.exe start_silent.vbs
"""

import os
import struct
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ICON_PATH = BASE_DIR / "app_icon.ico"
VBS_PATH = BASE_DIR / "start_silent.vbs"


def create_emerald_ico(ico_path: Path):
    """純 Python 生成 32x32 32-bit 翡翠綠工程合規專屬 .ico 圖示（零外部套件依賴）"""
    width, height = 32, 32
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            # 圓角邊框與中心十字盾牌紋理 (BGRA)
            dx = abs(x - 15.5)
            dy = abs(y - 15.5)
            if dx > 14 or dy > 14 or (dx + dy > 24):
                pixels.extend((0, 0, 0, 0))
            elif 12 <= x <= 19 and 6 <= y <= 25:
                pixels.extend((240, 253, 244, 255))
            elif 7 <= x <= 24 and 13 <= y <= 18:
                pixels.extend((240, 253, 244, 255))
            else:
                pixels.extend((105, 150, 5, 255))  # Emerald Green

    mask_row_bytes = ((width + 31) // 32) * 4
    and_mask = bytes(mask_row_bytes * height)

    bih = struct.pack(
        "<IIIHHIIIIII",
        40,
        width,
        height * 2,
        1,
        32,
        0,
        len(pixels) + len(and_mask),
        0,
        0,
        0,
        0,
    )
    image_data = bih + bytes(pixels) + and_mask
    ico_header = struct.pack("<HHH", 0, 1, 1)
    ico_entry = struct.pack(
        "<BBBBHHII",
        width,
        height,
        0,
        0,
        1,
        32,
        len(image_data),
        6 + 16,
    )
    ico_path.write_bytes(ico_header + ico_entry + image_data)


def get_desktop_dir() -> Path:
    home = Path.home()
    for candidate in [
        home / "OneDrive" / "桌面",
        home / "OneDrive" / "Desktop",
        home / "Desktop",
        home / "桌面",
    ]:
        if candidate.exists():
            return candidate
    return home


def create_shortcut():
    create_emerald_ico(ICON_PATH)
    desktop = get_desktop_dir()
    lnk_path = desktop / "AI商業變現與工程合規總控平台.lnk"

    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{lnk_path}')
$Shortcut.TargetPath = 'wscript.exe'
$Shortcut.Arguments = '"{VBS_PATH}"'
$Shortcut.WorkingDirectory = '{BASE_DIR}'
$Shortcut.IconLocation = '{ICON_PATH},0'
$Shortcut.Description = 'AI 商業變現與公共工程合規總控平台 (Port 8770)'
$Shortcut.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        check=True,
    )
    print(f"[OK] 已建立桌面捷徑: {lnk_path}")


if __name__ == "__main__":
    create_shortcut()
