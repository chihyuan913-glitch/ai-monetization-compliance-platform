' 無黑窗靜默啟動引導腳本 (Seamless Silent Launcher)
' 落實《本機 Web 應用程式生命週期全域規範》與《雲端硬碟可攜式規範》
Option Explicit

Dim fso, scriptDir, wsh, pythonExe, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
Set wsh = CreateObject("WScript.Shell")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
wsh.CurrentDirectory = scriptDir

' 優先探測雲端硬碟可攜式執行環境 runtime\pythonw.exe
If fso.FileExists(scriptDir & "\runtime\pythonw.exe") Then
    pythonExe = """" & scriptDir & "\runtime\pythonw.exe"""
ElseIf fso.FileExists(scriptDir & "\runtime\python.exe") Then
    pythonExe = """" & scriptDir & "\runtime\python.exe"""
ElseIf fso.FileExists("C:\Users\User.DESKTOP-OI789R1\AppData\Local\Programs\Python\Python313\pythonw.exe") Then
    pythonExe = """C:\Users\User.DESKTOP-OI789R1\AppData\Local\Programs\Python\Python313\pythonw.exe"""
Else
    pythonExe = "pythonw.exe"
End If

cmd = pythonExe & " """ & scriptDir & "\web_app.py"" --host 127.0.0.1 --port 8770"
wsh.Run cmd, 0, False
