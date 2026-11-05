"""配置与常量：路径、环境变量、采样周期、平台标识。

环境变量在模块导入时读取一次；命令行参数覆盖由 hardware_monitor.__main__
写入同名模块属性（api / sampler 在运行时读取这些属性，因此覆盖会生效）。
"""

from __future__ import annotations

import os
import sys

# 项目根目录（hardware_monitor/ 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(BASE_DIR, "plugin")

# ---------------- 服务配置（均可被命令行覆盖） ----------------
HOST = os.environ.get("MONITOR_HOST", "0.0.0.0")
PORT = int(os.environ.get("MONITOR_PORT", "5678"))
# 设置后才校验：请求需带 X-Monitor-Token 头或 ?token= 参数
TOKEN = os.environ.get("MONITOR_TOKEN", "").strip()
# 跨域来源；页面由本服务提供时同源即可，默认放宽以兼容直接打开的副本
CORS_ORIGIN = os.environ.get("MONITOR_CORS_ORIGIN", "*")
# 允许嵌入 /widget 的父页面（CSP frame-ancestors），T4 插件功能使用
FRAME_ANCESTORS = os.environ.get(
    "MONITOR_FRAME_ANCESTORS",
    "'self' http://127.0.0.1:* http://localhost:*",
).strip()

# ---------------- 采样节奏 ----------------
FAST_INTERVAL = 1.0    # CPU / 内存 / 网络速率 / GPU
SLOW_INTERVAL = 3.0    # 磁盘 / 系统信息（WMI、进程枚举较重）
ENGINE_INTERVAL = 3.0  # nvidia-smi 子进程较重，单独低频

# ---------------- 平台标识 ----------------
PLATFORM = (
    "windows"
    if sys.platform == "win32"
    else ("linux" if sys.platform.startswith("linux") else "other")
)
