/**
 * dsh-hardware-monitor 浏览器半（DSH UI 插件格式）。
 *
 * 在输入框下方（conversation.composer.dock）放一个常驻小挂件（不再占用对话区右上角）：
 *   - 每 2 秒从 /hardware-monitor-api/summary 读一次快照（同源代理，避免 CORS/混合内容）；
 *   - 显示 CPU / 内存 / GPU / 温度，点击展开明细并可直接打开完整面板；
 *   - 会话区 slot 不存在时回退到 composer dock（官方模板位）。
 *
 * 模块格式与 DSH 官方模板一致：
 * window.__ModuleLoader__.load({ id: <package name>, factory(require) { return { inject, apply } } })
 */
window.__ModuleLoader__.load({
  id: 'dsh-hardware-monitor',
  factory(require) {
    const React = require('react');
    const h = React.createElement;

    const REFRESH_MS = 2000;
    const BACKOFF = [2000, 5000, 10000];
    const DEFAULT_BASE = 'http://127.0.0.1:5678';

    function num(value) {
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    }

    function fmt(value, digits) {
      const parsed = num(value);
      return parsed === null ? '--' : parsed.toFixed(digits || 0);
    }

    function readLocalToken() {
      try { return localStorage.getItem('hardware-monitor-token') || ''; } catch { return ''; }
    }

    function dashboardUrl(baseUrl) {
      const base = String(baseUrl || DEFAULT_BASE).replace(/\/+$/, '');
      const token = readLocalToken();
      return token ? `${base}/?token=${encodeURIComponent(token)}` : `${base}/`;
    }

    function HardwareMonitorCorner() {
      const [snapshot, setSnapshot] = React.useState(null);
      const [baseUrl, setBaseUrl] = React.useState(DEFAULT_BASE);
      const [offline, setOffline] = React.useState(false);
      const [open, setOpen] = React.useState(false);
      const rootRef = React.useRef(null);

      // 轮询：完成后才排下一轮（避免请求叠加），失败退避 2s→5s→10s，隐藏时暂停。
      React.useEffect(() => {
        let cancelled = false;
        let timer = null;
        let failures = 0;

        async function poll() {
          if (cancelled) return;
          if (document.hidden) {
            timer = setTimeout(poll, REFRESH_MS);
            return;
          }
          try {
            const response = await fetch('/hardware-monitor-api/summary', { cache: 'no-store' });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const payload = await response.json();
            if (cancelled) return;
            if (!payload || payload.ok !== true) throw new Error((payload && payload.error) || 'offline');
            failures = 0;
            if (payload.base_url) setBaseUrl(payload.base_url);
            setSnapshot(payload.data || null);
            setOffline(false);
            timer = setTimeout(poll, REFRESH_MS);
          } catch (error) {
            if (cancelled) return;
            failures += 1;
            setOffline(true);
            timer = setTimeout(poll, BACKOFF[Math.min(failures - 1, BACKOFF.length - 1)]);
          }
        }

        poll();
        const onVisibility = () => {
          if (!document.hidden) {
            if (timer) clearTimeout(timer);
            poll();
          }
        };
        document.addEventListener('visibilitychange', onVisibility);
        return () => {
          cancelled = true;
          if (timer) clearTimeout(timer);
          document.removeEventListener('visibilitychange', onVisibility);
        };
      }, []);

      // 点击外部关闭展开面板
      React.useEffect(() => {
        if (!open) return undefined;
        function onDown(event) {
          if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false);
        }
        document.addEventListener('mousedown', onDown);
        return () => document.removeEventListener('mousedown', onDown);
      }, [open]);

      const data = snapshot || {};
      const cpu = num((data.cpu || {}).usage);
      const mem = num((data.memory || {}).usage_percent);
      const gpu = num((data.gpu || {}).usage);
      const temperature = data.temperature || {};
      const tempEntry = temperature.cpu || temperature.gpu || {};
      const temp = num(tempEntry.value);
      const net = data.network_speed || {};
      const dotColor = offline ? '#9ca3af' : (cpu !== null && cpu > 80 ? '#ef4444' : '#10b981');
      const chipText = `CPU ${fmt(cpu)}% · MEM ${fmt(mem)}%`;
      const title = offline
        ? `Hardware Monitor 离线：${baseUrl}（先启动 run.bat）`
        : [
            `CPU ${fmt(cpu)}%`,
            `MEM ${fmt(mem)}%`,
            `GPU ${fmt(gpu)}%`,
            `TEMP ${fmt(temp, 1)}°C`,
            `↓ ${fmt(net.download_mbps, 1)} MB/s`,
            `↑ ${fmt(net.upload_mbps, 1)} MB/s`,
          ].join(' · ');

      const chipStyle = {
        display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '2px 9px',
        borderRadius: '999px', border: '1px solid rgba(148,163,184,.45)',
        background: open ? 'rgba(59,130,246,.14)' : 'transparent',
        color: 'inherit', cursor: 'pointer', fontSize: '11px', lineHeight: '18px',
        whiteSpace: 'nowrap', userSelect: 'none', fontFamily: 'ui-monospace,Menlo,Consolas,monospace',
      };
      const dotStyle = { width: '7px', height: '7px', borderRadius: '50%', background: dotColor, flex: '0 0 auto' };
      const panelStyle = {
        position: 'absolute', bottom: '30px', right: '0', zIndex: 60, minWidth: '230px',
        padding: '10px 12px', borderRadius: '10px', border: '1px solid rgba(148,163,184,.35)',
        background: '#0f172a', color: '#e2e8f0', fontSize: '12px', lineHeight: '20px',
        boxShadow: '0 10px 30px rgba(0,0,0,.35)', fontFamily: 'ui-monospace,Menlo,Consolas,monospace',
      };
      const rowStyle = { display: 'flex', justifyContent: 'space-between', gap: '18px' };
      const linkStyle = { color: '#60a5fa', textDecoration: 'none', marginTop: '6px', display: 'inline-block' };

      function onActivate(event) {
        if (event) event.preventDefault();
        setOpen((value) => !value);
      }

      const panel = open ? h('div', { key: 'panel', style: panelStyle },
        h('div', { style: rowStyle }, h('span', {}, 'CPU'), h('b', {}, `${fmt(cpu)}%`)),
        h('div', { style: rowStyle }, h('span', {}, 'MEM'), h('b', {}, `${fmt(mem)}%`)),
        h('div', { style: rowStyle }, h('span', {}, 'GPU'), h('b', {}, `${fmt(gpu)}%`)),
        h('div', { style: rowStyle }, h('span', {}, 'TEMP'), h('b', {}, `${fmt(temp, 1)}°C`)),
        h('div', { style: rowStyle }, h('span', {}, 'DOWN'), h('b', {}, `${fmt(net.download_mbps, 1)} MB/s`)),
        h('div', { style: rowStyle }, h('span', {}, 'UP'), h('b', {}, `${fmt(net.upload_mbps, 1)} MB/s`)),
        h('a', {
          key: 'open', style: linkStyle, href: dashboardUrl(baseUrl), target: '_blank', rel: 'noopener',
        }, '打开完整面板 ↗'),
        offline ? h('div', { key: 'offline', style: { color: '#fbbf24', marginTop: '6px' } },
          `离线：${baseUrl}`) : null,
      ) : null;

      return h('div', { ref: rootRef, style: { position: 'relative', display: 'inline-block' } },
        h('div', {
          key: 'chip', style: chipStyle, title: title, onClick: onActivate, role: 'button', tabIndex: 0,
          onKeyDown: (event) => { if (event.key === 'Enter' || event.key === ' ') onActivate(event); },
          'data-hardware-monitor': 'corner',
        }, [h('span', { key: 'dot', style: dotStyle }), chipText]),
        panel,
      );
    }

    /** 侧边栏图标：显示器 + 心跳波形，跟随宿主 currentColor。 */
    function HardwareMonitorIcon() {
      return h('svg', {
        viewBox: '0 0 16 16', width: 16, height: 16, fill: 'none',
        'aria-hidden': 'true', style: { display: 'block' },
      },
      h('rect', { x: 1.5, y: 2.5, width: 13, height: 9, rx: 1.5, stroke: 'currentColor', strokeWidth: 1.3 }),
      h('path', {
        d: 'M3.5 7h2l1.2-2.2L8.2 9l1.1-1.6h2.2',
        stroke: 'currentColor', strokeWidth: 1.3, strokeLinecap: 'round', strokeLinejoin: 'round',
      }),
      h('path', { d: 'M5.5 13.5h5', stroke: 'currentColor', strokeWidth: 1.3, strokeLinecap: 'round' }));
    }

    function PercentRing({ value, label, color, sub }) {
      const has = value !== null && value !== undefined && Number.isFinite(Number(value));
      const pct = has ? Math.max(0, Math.min(100, Number(value))) : 0;
      const radius = 26;
      const circumference = 2 * Math.PI * radius;
      return h('div', { style: { display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0 } },
        h('svg', { viewBox: '0 0 64 64', width: 64, height: 64, 'aria-hidden': 'true', style: { flex: '0 0 auto' } },
          h('circle', {
            cx: 32, cy: 32, r: radius, fill: 'none',
            stroke: 'var(--dsw-alias-bg-layer-2,#eef0f4)', strokeWidth: 7,
          }),
          h('circle', {
            cx: 32, cy: 32, r: radius, fill: 'none', stroke: color, strokeWidth: 7,
            strokeLinecap: 'round',
            strokeDasharray: `${circumference} ${circumference}`,
            strokeDashoffset: `${circumference * (1 - pct / 100)}`,
            transform: 'rotate(-90 32 32)',
          }),
          h('text', {
            x: 32, y: 36, textAnchor: 'middle', fontSize: 13, fontWeight: 700,
            fill: 'var(--dsw-alias-label-primary,#1f2328)',
          }, has ? `${fmt(pct)}%` : '--')),
        h('div', { style: { minWidth: 0 } },
          h('div', { style: { fontSize: '12px', fontWeight: 600 } }, label),
          h('div', {
            style: {
              fontSize: '11px', color: 'var(--dsw-alias-label-secondary,#6b7280)',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            },
          }, sub || '')));
    }

    function Sparkline({ values, color }) {
      const width = 180;
      const height = 36;
      const pad = 2;
      const data = Array.isArray(values)
        ? values.map((value) => Number(value)).filter((value) => Number.isFinite(value))
        : [];
      if (data.length < 2) {
        return h('div', {
          style: {
            height: `${height}px`, marginTop: '6px', fontSize: '11px',
            color: 'var(--dsw-alias-label-secondary,#6b7280)',
          },
        }, '采集中…');
      }
      const points = data.map((value, index) => {
        const x = (index / (data.length - 1)) * width;
        const y = height - pad - (Math.max(0, Math.min(100, value)) / 100) * (height - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
      return h('svg', {
        viewBox: `0 0 ${width} ${height}`,
        preserveAspectRatio: 'none',
        style: { width: '100%', height: `${height}px`, display: 'block', marginTop: '6px' },
      }, h('polyline', {
        points, fill: 'none', stroke: color, strokeWidth: 1.8,
        strokeLinejoin: 'round', strokeLinecap: 'round',
      }));
    }

    /**
     * 侧边栏「完整面板」页：原生 React 面板，不再用 iframe。
     *
     * 仪表盘页面带 `Content-Security-Policy: frame-ancestors 'self'`，DSH 的 iframe 属于
     * 不同 origin 会被浏览器拦截 → 空白。这里改为直接请求 DSH 同源代理
     * `/hardware-monitor-api/{summary,processes}`，在宿主内渲染完整数据。
     */
    function HardwareMonitorPanel() {
      const [state, setState] = React.useState({
        data: null, processes: null, offline: false, updatedAt: null, baseUrl: DEFAULT_BASE,
      });
      const [tick, setTick] = React.useState(0);
      const rootRef = React.useRef(null);
      const historyRef = React.useRef({ cpu: [], mem: [], gpu: [] });

      React.useEffect(() => {
        let cancelled = false;
        let timer = null;
        let failures = 0;

        async function poll() {
          if (cancelled) return;
          if (document.hidden) {
            timer = setTimeout(poll, REFRESH_MS);
            return;
          }
          try {
            const [summaryResp, processResp] = await Promise.all([
              fetch('/hardware-monitor-api/summary', { cache: 'no-store' }),
              fetch('/hardware-monitor-api/processes', { cache: 'no-store' }).catch(() => null),
            ]);
            if (!summaryResp.ok) throw new Error(`HTTP ${summaryResp.status}`);
            const summary = await summaryResp.json();
            if (!summary || summary.ok !== true) throw new Error((summary && summary.error) || 'offline');
            let processes = null;
            if (processResp && processResp.ok) {
              try { processes = await processResp.json(); } catch { processes = null; }
            }
            if (cancelled) return;
            failures = 0;
            const snapshotData = summary.data || null;
            const cpuValue = num(((snapshotData || {}).cpu || {}).usage) || 0;
            const memValue = num(((snapshotData || {}).memory || {}).usage_percent) || 0;
            const gpuValue = num(((snapshotData || {}).gpu || {}).usage) || 0;
            const history = historyRef.current;
            const pushHistory = (key, value) => {
              const list = history[key];
              if (!Array.isArray(list)) return;
              list.push(value);
              if (list.length > 30) list.shift();
            };
            pushHistory('cpu', cpuValue);
            pushHistory('mem', memValue);
            pushHistory('gpu', gpuValue);
            setState({
              data: snapshotData,
              processes,
              offline: false,
              updatedAt: new Date(),
              baseUrl: summary.base_url || DEFAULT_BASE,
            });
            timer = setTimeout(poll, REFRESH_MS);
          } catch (error) {
            if (cancelled) return;
            failures += 1;
            setState((prev) => ({ ...prev, offline: true }));
            timer = setTimeout(poll, BACKOFF[Math.min(failures - 1, BACKOFF.length - 1)]);
          }
        }

        poll();
        const onVisibility = () => {
          if (!document.hidden) {
            if (timer) clearTimeout(timer);
            poll();
          }
        };
        document.addEventListener('visibilitychange', onVisibility);
        return () => {
          cancelled = true;
          if (timer) clearTimeout(timer);
          document.removeEventListener('visibilitychange', onVisibility);
        };
      }, [tick]);

      // 切换侧边栏面板时，把外层主区域滚回顶部，避免标题被上一页的滚动位置顶出视口。
      React.useEffect(() => {
        const node = rootRef.current;
        if (!node) return;
        if (node.scrollTop) node.scrollTop = 0;
        let parent = node.parentElement;
        while (parent && parent !== document.body) {
          if (parent.scrollHeight > parent.clientHeight + 1) {
            parent.scrollTop = 0;
            break;
          }
          parent = parent.parentElement;
        }
      }, []);

      const data = state.data || {};
      const cpu = data.cpu || {};
      const memory = data.memory || {};
      const gpu = data.gpu || {};
      const temperature = data.temperature || {};
      const network = data.network_speed || {};
      const system = data.system_info || {};
      const disks = Array.isArray(data.disk) ? data.disk : [];
      const physicalDisks = disks.filter((disk) => disk && disk.type === 'physical');
      const partitions = disks.filter((disk) => disk && disk.type !== 'physical');
      const processList = state.processes && Array.isArray(state.processes.processes) ? state.processes.processes : [];
      const cores = Array.isArray(cpu.per_core) ? cpu.per_core : [];

      const cpuUsage = num(cpu.usage);
      const memUsage = num(memory.usage_percent);
      const gpuUsage = num(gpu.usage);
      const cpuTemp = temperature.cpu ? num(temperature.cpu.value) : null;
      const gpuTemp = temperature.gpu ? num(temperature.gpu.value) : null;
      const mainTemp = cpuTemp !== null ? cpuTemp : gpuTemp;
      const download = num(network.download_mbps);
      const upload = num(network.upload_mbps);
      const latency = num(data.latency_ms);
      const history = historyRef.current;

      const panelStyle = {
        height: '100%', overflowY: 'auto', padding: '16px 18px 24px', boxSizing: 'border-box',
        color: 'var(--dsw-alias-label-primary,#1f2328)',
        fontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif',
      };
      const cardStyle = {
        background: 'var(--dsw-alias-bg-layer-1,#fff)',
        border: '1px solid var(--dsw-alias-border-l2,#e5e7eb)',
        borderRadius: '12px', padding: '12px 14px', minWidth: 0,
      };
      const labelStyle = { color: 'var(--dsw-alias-label-secondary,#6b7280)', fontSize: '12px', lineHeight: '18px' };
      const valueStyle = {
        color: 'var(--dsw-alias-label-primary,#1f2328)', fontSize: '22px', fontWeight: 700,
        lineHeight: '28px', fontVariantNumeric: 'tabular-nums',
      };
      const rowStyle = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' };
      const barBg = {
        height: '6px', borderRadius: '999px',
        background: 'var(--dsw-alias-bg-layer-2,#eef0f4)', overflow: 'hidden',
      };
      const barFill = (percent) => ({
        height: '100%', width: `${Math.max(0, Math.min(100, percent || 0))}%`, borderRadius: '999px',
        background: percent >= 85 ? '#ef4444' : percent >= 65 ? '#f59e0b' : '#10b981',
      });
      const gridStyle = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: '10px', marginTop: '12px' };
      const sectionStyle = { ...cardStyle, marginTop: '12px' };
      const buttonStyle = {
        border: '1px solid var(--dsw-alias-border-l2,#d5d9e0)',
        background: 'var(--dsw-alias-bg-layer-1,#fff)', color: 'inherit', borderRadius: '8px',
        padding: '4px 10px', fontSize: '12px', lineHeight: '18px', cursor: 'pointer', textDecoration: 'none',
      };

      function metricCard(label, valueText, subText, percent) {
        return h('div', { key: label, style: cardStyle },
          h('div', { style: labelStyle }, label),
          h('div', { style: valueStyle }, valueText),
          subText ? h('div', { style: { ...labelStyle, fontSize: '11px' } }, subText) : null,
          typeof percent === 'number'
            ? h('div', { style: { ...barBg, marginTop: '8px' } }, h('div', { style: barFill(percent) }))
            : null);
      }

      return h('div', { ref: rootRef, style: panelStyle },
        h('div', {
          style: {
            ...rowStyle, marginBottom: '8px', position: 'sticky', top: 0, zIndex: 2,
            background: 'var(--dsw-alias-bg-layer-2,#f7f8fa)', paddingBottom: '8px',
          },
        },
          h('div', { style: { display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 } },
            h('span', {
              style: {
                width: '8px', height: '8px', borderRadius: '50%', flex: '0 0 auto',
                background: state.offline ? '#ef4444' : '#10b981',
              },
            }),
            h('strong', { style: { fontSize: '16px' } }, '硬件监控'),
            h('span', { style: labelStyle }, state.offline ? '离线' : '在线'),
            state.updatedAt
              ? h('span', { style: { ...labelStyle, fontSize: '11px' } }, `更新于 ${state.updatedAt.toLocaleTimeString()}`)
              : null),
          h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
            h('button', { type: 'button', style: buttonStyle, onClick: () => setTick((value) => value + 1) }, '刷新'),
            h('a', { href: dashboardUrl(state.baseUrl), target: '_blank', rel: 'noopener', style: buttonStyle }, '在浏览器打开 ↗'))),

        state.offline && !state.data
          ? h('div', { style: { ...cardStyle, marginTop: '12px', borderColor: '#f59e0b' } },
              h('strong', {}, '监控服务不可用'),
              h('div', { style: { ...labelStyle, marginTop: '4px' } }, 'DSH 插件会自动拉起 5678；也可手动运行 run.bat。'),
              h('div', { style: { ...labelStyle, marginTop: '2px' } }, `当前地址：${state.baseUrl}`))
          : null,

        h('div', { style: gridStyle },
          metricCard('CPU', `${fmt(cpuUsage)}%`, `${fmt(cpu.freq_current_mhz)} MHz · ${cpu.count_physical || '--'} 核 / ${cpu.count_logical || '--'} 线程`, cpuUsage),
          metricCard('内存', `${fmt(memUsage)}%`, `${fmt(memory.used_gb, 1)} / ${fmt(memory.total_gb, 1)} GB`, memUsage),
          metricCard('GPU', `${fmt(gpuUsage)}%`, `${gpu.name || '--'} · 显存 ${fmt(gpu.used_gb, 1)}/${fmt(gpu.total_gb, 1)} GB`, gpuUsage),
          metricCard('温度', mainTemp === null ? '--' : `${fmt(mainTemp, 1)}°C`, temperature.cpu && temperature.cpu.source ? `来源 ${temperature.cpu.source}` : '', null),
          metricCard('下载', download === null ? '--' : `${fmt(download, 1)} MB/s`, '整机网络合计', null),
          metricCard('上传', upload === null ? '--' : `${fmt(upload, 1)} MB/s`, '整机网络合计', null)),

        h('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '12px', marginTop: '12px', alignItems: 'flex-start' } },
          h('div', { style: { ...cardStyle, flex: '1 1 360px', minWidth: '280px' } },
            h('div', { style: rowStyle },
              h('strong', {}, 'CPU 各核心'),
              h('span', { style: labelStyle }, `${cpu.count_physical || '--'} 核 / ${cpu.count_logical || '--'} 线程`)),
            cores.length === 0
              ? h('div', { style: { ...labelStyle, marginTop: '8px' } }, cpuUsage === null ? '等待数据…' : '核心明细不可用')
              : h('div', {
                  style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(86px,1fr))', gap: '8px', marginTop: '10px' },
                }, cores.map((value, index) => h('div', { key: `core-${index}` },
                  h('div', { style: { ...rowStyle, fontSize: '11px', color: 'var(--dsw-alias-label-secondary,#6b7280)' } },
                    h('span', {}, `C${index}`), h('span', {}, `${fmt(value)}%`)),
                  h('div', { style: barBg }, h('div', { style: barFill(num(value) || 0) })))))),
          h('div', { style: { ...cardStyle, flex: '1 1 260px', minWidth: '240px' } },
            h('strong', {}, '内存 / GPU / 温度'),
            h('div', { style: { ...rowStyle, marginTop: '8px' } },
              h('span', { style: labelStyle }, '内存'),
              h('span', { style: { fontSize: '12px' } }, `${fmt(memory.used_gb, 1)} / ${fmt(memory.total_gb, 1)} GB`)),
            h('div', { style: { ...barBg, marginTop: '4px' } }, h('div', { style: barFill(memUsage || 0) })),
            h('div', { style: { ...rowStyle, marginTop: '8px' } },
              h('span', { style: labelStyle }, 'GPU'),
              h('span', { style: { fontSize: '12px' } }, `${gpu.name || '--'} · ${fmt(gpuUsage)}%`)),
            h('div', { style: { ...rowStyle, marginTop: '4px' } },
              h('span', { style: labelStyle }, '温度'),
              h('span', { style: { fontSize: '12px' } },
                `CPU ${cpuTemp === null ? '--' : `${fmt(cpuTemp, 1)}°C`} · GPU ${gpuTemp === null ? '--' : `${fmt(gpuTemp, 1)}°C`}`)),
            h('div', { style: { ...rowStyle, marginTop: '4px' } },
              h('span', { style: labelStyle }, '延迟'),
              h('span', { style: { fontSize: '12px' } }, `${fmt(latency, 1)} ms`)))),

        h('div', { style: sectionStyle },
          h('div', { style: rowStyle },
            h('strong', {}, '物理硬盘'),
            h('span', { style: labelStyle }, `${physicalDisks.length} 块`)),
          physicalDisks.length === 0
            ? h('div', { style: { ...labelStyle, marginTop: '6px' } },
                '未识别到物理硬盘（WMI/CIM 不可用或需要管理员权限时可能如此）')
            : physicalDisks.map((disk, index) => h('div', { key: `physical-${index}`, style: { marginTop: '8px' } },
                h('div', { style: rowStyle },
                  h('span', { style: { fontSize: '12px' } }, disk.model || `Disk ${index + 1}`),
                  h('span', { style: { ...labelStyle, fontVariantNumeric: 'tabular-nums' } },
                    `${num(disk.size_gb) === null ? '--' : `${fmt(disk.size_gb, 1)} GB`}${disk.interface_type ? ` · ${disk.interface_type}` : ''}`)))),
          h('div', { style: { ...rowStyle, marginTop: '12px' } },
            h('strong', {}, '磁盘分区'),
            h('span', { style: labelStyle }, `${partitions.length} 个`)),
          partitions.length === 0
            ? h('div', { style: { ...labelStyle, marginTop: '6px' } }, '无分区数据')
            : partitions.map((disk, index) => {
                const name = disk.device || disk.mountpoint || disk.mount_point || `分区 ${index + 1}`;
                const usage = num(disk.usage_percent);
                return h('div', { key: `partition-${index}`, style: { marginTop: '8px' } },
                  h('div', { style: rowStyle },
                    h('span', { style: { fontSize: '12px' } }, `${name}${disk.fstype ? ` · ${disk.fstype}` : ''}`),
                    h('span', { style: { ...labelStyle, fontVariantNumeric: 'tabular-nums' } },
                      `${fmt(disk.free_gb, 1)} GB 可用 / ${fmt(disk.total_gb, 1)} GB · ${fmt(usage)}%`)),
                  h('div', { style: barBg }, h('div', { style: barFill(usage || 0) })));
              })),

        h('div', { style: sectionStyle },
          h('div', { style: rowStyle },
            h('strong', {}, '实时可视化'),
            h('span', { style: labelStyle }, '最近 60 秒')),
          h('div', {
            style: {
              display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))',
              gap: '10px', marginTop: '10px',
            },
          },
            h('div', { style: { ...cardStyle, padding: '10px 12px' } },
              h(PercentRing, {
                value: cpuUsage, label: 'CPU', color: '#3b82f6',
                sub: `${fmt(cpu.count_physical)} 核 / ${fmt(cpu.count_logical)} 线程`,
              }),
              h(Sparkline, { values: history.cpu, color: '#3b82f6' })),
            h('div', { style: { ...cardStyle, padding: '10px 12px' } },
              h(PercentRing, {
                value: memUsage, label: '内存', color: '#8b5cf6',
                sub: `${fmt(memory.used_gb, 1)} / ${fmt(memory.total_gb, 1)} GB`,
              }),
              h(Sparkline, { values: history.mem, color: '#8b5cf6' })),
            h('div', { style: { ...cardStyle, padding: '10px 12px' } },
              h(PercentRing, {
                value: gpuUsage, label: 'GPU', color: '#f59e0b',
                sub: `${gpu.name || '--'} · 显存 ${fmt(gpu.used_gb, 1)}/${fmt(gpu.total_gb, 1)} GB`,
              }),
              h(Sparkline, { values: history.gpu, color: '#f59e0b' })))),

        h('div', { style: sectionStyle },
          h('div', { style: rowStyle },
            h('strong', {}, '进程 Top'),
            h('span', { style: labelStyle }, state.processes && state.processes.process_count != null ? `共 ${state.processes.process_count} 个进程` : '进程列表不可用')),
          processList.length === 0
            ? h('div', { style: { ...labelStyle, marginTop: '6px' } }, '暂无进程数据')
            : h('div', {
                style: {
                  marginTop: '8px', display: 'grid',
                  gridTemplateColumns: 'minmax(0,1fr) auto auto auto', gap: '6px 12px', fontSize: '12px',
                },
              },
              h('div', { style: labelStyle }, '进程'), h('div', { style: { ...labelStyle, textAlign: 'right' } }, 'PID'),
              h('div', { style: { ...labelStyle, textAlign: 'right' } }, '私有内存'), h('div', { style: { ...labelStyle, textAlign: 'right' } }, '工作集'),
              processList.slice(0, 10).flatMap((item) => [
                h('div', { key: `${item.name}-name`, style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, item.name),
                h('div', { key: `${item.name}-pid`, style: { ...labelStyle, textAlign: 'right' } }, String(item.pids != null ? item.pids : '--')),
                h('div', { key: `${item.name}-private`, style: { ...labelStyle, textAlign: 'right' } }, `${fmt(item.private_mb, 1)} MB`),
                h('div', { key: `${item.name}-ws`, style: { ...labelStyle, textAlign: 'right' } }, `${fmt(item.working_set_mb, 1)} MB`),
              ]))),

        h('div', { style: { ...labelStyle, marginTop: '10px', fontSize: '11px' } },
          `开机 ${system.uptime || '--'} · 平台 ${(data.platform || {}).os || '--'} · 延迟 ${fmt(latency, 1)} ms`));
    }

    /**
     * 在侧边栏注册入口图标 + 对应的主面板（参考插件市场的 sidebar.panellist / main 槽位）。
     * 点击图标时由宿主切换 main(key=hardware-monitor)，即内嵌完整面板。
     */
    function mountSidebar(ctx) {
      let offRow = null;
      let offPanel = null;
      let offPanelInject = null;
      try {
        ctx.slots.inject('sidebar.panellist', () => {
          try {
            offRow = ctx.slots.register({
              name: 'sidebar.panellist',
              id: 'hardware-monitor',
              order: 2,
              label: () => '硬件监控',
            }, HardwareMonitorIcon);
          } catch { /* 宿主版本不支持侧边栏入口 */ }
        });
      } catch { /* ignore */ }
      try {
        // 与 sidebar.panellist 的 id 对应：点击侧边栏图标时宿主打开 main key=hardware-monitor 的面板。
        // 必须放在 slots.inject('main') 里注册，等布局声明 main 槽位后再注册，否则点击会因缺少条目抛错。
        offPanelInject = ctx.slots.inject('main', () => {
          try {
            offPanel = ctx.slots.register({ name: 'main', key: 'hardware-monitor' }, HardwareMonitorPanel);
          } catch { /* 宿主版本不支持主面板槽位 */ }
        });
      } catch { /* ignore */ }
      return () => {
        try { if (typeof offRow === 'function') offRow(); } catch { /* ignore */ }
        try { if (typeof offPanel === 'function') offPanel(); } catch { /* ignore */ }
        try { if (typeof offPanelInject === 'function') offPanelInject(); } catch { /* ignore */ }
      };
    }

    /**
     * 尝试把挂件注册进某个 slot。
     * @param {object} ctx - client 上下文（含 slots 服务）
     * @param {string} slot - slot 名
     * @param {number} order - 排序
     * @returns {boolean} 是否注册成功
     */
    function mountAt(ctx, slot, order) {
      let mounted = false;
      try {
        ctx.slots.inject(slot, () => {
          try {
            ctx.slots.register({ name: slot, id: 'hardware-monitor', order }, HardwareMonitorCorner);
            mounted = true;
          } catch {
            mounted = false;
          }
        });
      } catch {
        mounted = false;
      }
      return mounted;
    }

    return {
      inject: ['slots'],
      apply(ctx) {
        // 侧边栏入口 + 完整面板（像插件市场一样有图标入口）
        mountSidebar(ctx);
        // 按用户要求：性能小挂件只放在输入框下方的 composer dock，不再占用对话区右上角。
        mountAt(ctx, 'conversation.composer.dock', 60);
      },
    };
  },
});
