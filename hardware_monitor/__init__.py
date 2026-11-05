"""hardware-monitor: Windows/Linux 本机硬件看板后端。

包结构（T2 迁移后）：
  config.py            配置与常量（环境变量、采样周期、平台标识）
  state.py             内存快照与错误隔离（put / put_error / collect）
  sampler.py           后台采样调度线程
  api.py               Flask app factory 与全部 HTTP 路由
  collectors/          psutil 通用采集、NVML
  platform/            平台适配（windows.py / linux.py）
"""

__version__ = "2.0.0"
