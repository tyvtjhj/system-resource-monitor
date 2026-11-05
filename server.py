"""兼容入口：`python server.py` 仍然可用，真正的实现已迁移到 hardware_monitor 包。

T2 迁移后本文件只做转发；现有 URL、启动方式（run.bat）保持不变。
"""

from hardware_monitor.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
