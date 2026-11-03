@echo off
rem 一键安装 Hardware Monitor 与 DSH 插件（Windows）
rem 用法：在仓库目录双击运行，或执行 install-dsh.cmd
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-dsh.ps1" %*
if errorlevel 1 (
  echo.
  echo [ERROR] 安装失败，请查看上方输出。
  pause
  exit /b 1
)
echo.
echo 安装完成，可以刷新 DSH 页面（Ctrl+R）。
pause
