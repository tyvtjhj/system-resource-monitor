---
name: "hardware-monitor"
description: "查看本机 CPU/内存/GPU/温度/网络实时占用：用户问“CPU 占用多少/内存满没满/显卡温度/网速多少/机器卡不卡”时使用。通过 dsh-hardware-monitor 插件的 hardware_monitor_status 工具读取本机 Hardware Monitor 服务（http://127.0.0.1:5678）。"
---

# Hardware Monitor 本机资源监控 · dsh 集成

数据来源：本机常驻的 Hardware Monitor 服务（默认 `http://127.0.0.1:5678`，Flask 只读采集）。
dsh 侧由 `dsh-hardware-monitor` 插件提供工具与对话右上角挂件，**不要**直接 curl 硬件接口。

## 工具

| 工具 | 用途 |
|---|---|
| `hardware_monitor_status` | 实时快照：CPU 占用、内存占用、NVIDIA GPU 占用、CPU/GPU 温度、网络上下行、开机时长、平台 |

## 回答格式

1. 调用 `hardware_monitor_status`；
2. 用一两句话给出结论（例如“CPU 32%、内存 58%，温度 62℃，机器不忙”）；
3. 需要细节时引用返回字段，不要编造缺失值（`--` 表示该项不可用）。

## 已知口径（避免误报）

- GPU 占用仅 NVIDIA（NVML / `nvidia-smi`）；AMD/Intel 只有核显卡片，独立显卡无数据。
- 温度来源可能是 `lhm`（内核级 `CPU Package`，需管理员 + PawnIO）、`acpi`（平台热区，通常偏低）、`hwmon`/`thermal`（Linux）。
- 网络速率是**整机所有网卡合计**（含回环），不是单进程流量。
- 服务未运行：提示用户先双击 `run.bat`（或 `./run.sh`）启动监控服务。
- 返回 `TOKEN_REQUIRED`：说明服务启用了 `MONITOR_TOKEN`，需要在启动 DSH 的终端设置 `HARDWARE_MONITOR_TOKEN` 后重启 DSH。

## 故障排查

- 服务地址不是默认 5678：启动 DSH 前设置 `HARDWARE_MONITOR_URL`。
- 挂件离线但工具可用：挂件走 dsh 同源代理 `/hardware-monitor-api/summary`，刷新 DSH 页面（Ctrl+R）重新加载客户端半。
- 仪表盘里看到更完整历史曲线：浏览器打开 `http://127.0.0.1:5678/`（如启用 token，需要 `?token=`）。
