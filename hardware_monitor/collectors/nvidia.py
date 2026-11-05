"""NVIDIA GPU 采集：NVML（pynvml）与 nvidia-smi 子进程。

NVML 提供利用率/显存；`nvidia-smi dmon -s u` 提供 CUDA/3D/编码/解码引擎，
两者都不可用时静默降级（GPU 字段为 None / 空 engines），不影响其他指标。
"""

from __future__ import annotations

import re
import subprocess

from .. import platform as platform_info


class NvmlSession:
    """封装 pynvml，初始化失败时静默降级，不影响其他指标。"""

    def __init__(self):
        self.nvml = None
        self.handles = []
        try:
            import warnings

            with warnings.catch_warnings():
                # pynvml 包已改名 nvidia-ml-py，导入时的 FutureWarning 无信息量
                warnings.simplefilter("ignore", FutureWarning)
                import pynvml as nvml

            nvml.nvmlInit()
            self.nvml = nvml
            self.handles = [
                nvml.nvmlDeviceGetHandleByIndex(i)
                for i in range(nvml.nvmlDeviceGetCount())
            ]
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] NVML unavailable: {exc}")

    def _name(self, handle):
        name = self.nvml.nvmlDeviceGetName(handle)
        return name.decode() if isinstance(name, bytes) else str(name)

    def gpu(self):
        """返回 {name, usage, used_gb, total_gb}；无 NVIDIA 设备时返回 None。"""
        if not self.handles:
            return None
        names, usages = [], []
        used_mb = total_mb = 0
        for handle in self.handles:
            names.append(self._name(handle))
            try:
                usages.append(self.nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
            except Exception:  # noqa: BLE001
                usages.append(0)
            try:
                mem = self.nvml.nvmlDeviceGetMemoryInfo(handle)
                used_mb += mem.used
                total_mb += mem.total
            except Exception:  # noqa: BLE001
                pass
        return {
            "name": ", ".join(names),
            "usage": round(sum(usages) / len(usages)) if usages else 0,
            "used_gb": round(used_mb / 1024 ** 3, 1),
            "total_gb": round(total_mb / 1024 ** 3, 1),
        }


# ---------------- GPU 引擎利用率（nvidia-smi） ----------------
def _nvidia_smi(args, timeout=4):
    kwargs = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
        **platform_info.subprocess_flags(),
    }
    return subprocess.run(["nvidia-smi", *args], **kwargs)


def _parse_dmon(stdout):
    """dmon -s u 的列依次为：gpu sm mem enc dec jpg ofa（曾错位取成 mem/enc）。"""
    for line in stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            sm, enc, dec = int(float(parts[1])), int(float(parts[3])), int(float(parts[4]))
        except ValueError:
            continue
        return {"cuda": sm, "3d": sm, "encode": enc, "decode": dec}
    return {}


def _parse_query(stdout):
    """nvidia-smi -q 的兜底解析：Utilization 段里的 Gpu / Encoder / Decoder。"""
    engines = {}
    patterns = {
        "gpu": r"Utilization\s*\n\s*Gpu\s*:\s*(\d+)\s*%",
        "encode": r"Encoder\s*:\s*(\d+)\s*%",
        "decode": r"Decoder\s*:\s*(\d+)\s*%",
    }
    if re.search(r"Utilization", stdout) is None:
        return engines
    for key, pattern in patterns.items():
        match = re.search(pattern, stdout)
        if not match:
            continue
        value = int(match.group(1))
        engines["cuda" if key == "gpu" else key] = value
        if key == "gpu":
            engines["3d"] = value
    return engines


_smi_state = {"checked": False, "ok": True}


def collect_engines(gpu_name=""):
    """返回 {engines, name, mem_used_gb, mem_total_gb}；无 nvidia-smi 时返回空引擎。"""
    result = {"engines": {}, "name": gpu_name, "mem_used_gb": 0, "mem_total_gb": 0}
    if not _smi_state["ok"]:
        return result

    try:
        engines = _parse_dmon(_nvidia_smi(["dmon", "-s", "u", "-c", "1"]).stdout)
    except FileNotFoundError:
        _smi_state["ok"] = False
        print("[WARN] nvidia-smi not found, GPU engine metrics disabled")
        return result
    except Exception:  # noqa: BLE001
        engines = {}

    if not engines:
        try:
            engines = _parse_query(_nvidia_smi(["-q"]).stdout)
        except Exception:  # noqa: BLE001
            engines = {}

    result["engines"] = engines
    return result
