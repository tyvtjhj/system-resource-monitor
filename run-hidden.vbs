' Start Hardware Monitor without a console window.
' Double-click this file, or run: wscript run-hidden.vbs
Option Explicit

Dim fso, shell, repo, python
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

repo = fso.GetParentFolderName(WScript.ScriptFullName)
python = repo & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(python) Then
  MsgBox "未找到 .venv\Scripts\pythonw.exe，请先运行 scripts\bootstrap.ps1 创建环境。", vbExclamation, "Hardware Monitor"
  WScript.Quit 1
End If

shell.CurrentDirectory = repo
' 0 = hidden window, False = do not wait
shell.Run """" & python & """ -m hardware_monitor --no-elevate", 0, False
