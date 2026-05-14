import { useState, useEffect, useCallback, useRef } from 'react';
import './index.css';

// ─── Types ──────────────────────────────────────────────────────

interface CellInfo {
  cell_id: string;
  template_id: string;
  state: string;
  started_at: string;
  end_at: string;
  metadata: Record<string, string>;
  cpu_ms?: number;
  memory_mb?: number;
}

interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  cell_id: string;
}

interface TemplateInfo {
  name: string;
  runtime: string;
  compiled: boolean;
  packages: string[];
  description?: string;
}

interface GatewayHealth {
  status: string;
  version: string;
  uptime_s: number;
  cells_active: number;
  cells_total: number;
}

// ─── API Client ─────────────────────────────────────────────────

const API_URL = localStorage.getItem('synapse_api_url') || 'http://localhost:8002';
const getApiKey = () => localStorage.getItem('synapse_api_key') || '';

async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const key = getApiKey();
  if (key) headers['Authorization'] = `Bearer ${key}`;

  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

// ─── Utility ────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

const stateColors: Record<string, string> = {
  running: '#10b981',
  paused: '#f59e0b',
  stopped: '#6b7280',
  failed: '#ef4444',
};

// ─── Components ─────────────────────────────────────────────────

function StatusDot({ state }: { state: string }) {
  const color = stateColors[state] || '#6b7280';
  return (
    <span className="status-dot" style={{ background: color }}>
      {state === 'running' && <span className="status-pulse" style={{ background: color }} />}
    </span>
  );
}

function StatCard({ label, value, sub, accent }: {
  label: string; value: string | number; sub?: string; accent?: string;
}) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className="stat-value" style={accent ? { color: accent } : {}}>
        {value}
      </div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

// ─── Settings Modal ─────────────────────────────────────────────

function SettingsModal({ onClose }: { onClose: () => void }) {
  const [apiKey, setApiKey] = useState(getApiKey());
  const [apiUrl, setApiUrl] = useState(
    localStorage.getItem('synapse_api_url') || 'http://localhost:8002'
  );

  const save = () => {
    localStorage.setItem('synapse_api_key', apiKey);
    localStorage.setItem('synapse_api_url', apiUrl);
    onClose();
    window.location.reload();
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <h2>Settings</h2>
        <label>
          API Key
          <input
            type="password"
            value={apiKey}
            onChange={e => setApiKey(e.target.value)}
            placeholder="synapse_sk_..."
          />
        </label>
        <label>
          Gateway URL
          <input
            type="text"
            value={apiUrl}
            onChange={e => setApiUrl(e.target.value)}
            placeholder="http://localhost:8002"
          />
        </label>
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose}>Cancel</button>
          <button className="btn-primary" onClick={save}>Save</button>
        </div>
      </div>
    </div>
  );
}

// ─── Cell Detail Panel ──────────────────────────────────────────

function CellDetail({ cell, onClose, onAction }: {
  cell: CellInfo;
  onClose: () => void;
  onAction: () => void;
}) {
  const [logs, setLogs] = useState<string[]>([]);
  const [cmd, setCmd] = useState('');
  const [cmdResult, setCmdResult] = useState('');
  const logsRef = useRef<HTMLDivElement>(null);

  const runCommand = async () => {
    if (!cmd.trim()) return;
    try {
      const res = await api<{ stdout: string; stderr: string; exit_code: number }>(
        'POST',
        `/v1/cells/${cell.cell_id}/commands`,
        { command: cmd }
      );
      setCmdResult(res.stdout + (res.stderr ? `\nSTDERR: ${res.stderr}` : ''));
      setCmd('');
    } catch (e: any) {
      setCmdResult(`Error: ${e.message}`);
    }
  };

  const killCell = async () => {
    try {
      await api('DELETE', `/v1/cells/${cell.cell_id}`);
      onAction();
      onClose();
    } catch (e: any) {
      alert(`Failed to kill: ${e.message}`);
    }
  };

  const pauseCell = async () => {
    try {
      await api('POST', `/v1/cells/${cell.cell_id}/pause`);
      onAction();
    } catch (e: any) {
      alert(`Failed to pause: ${e.message}`);
    }
  };

  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const result = await api<{ logs: string[] }>('GET', `/v1/cells/${cell.cell_id}/logs`);
        setLogs(result.logs || []);
      } catch { /* gateway may not support logs yet */ }
    };
    fetchLogs();
  }, [cell.cell_id]);

  useEffect(() => {
    logsRef.current?.scrollTo(0, logsRef.current.scrollHeight);
  }, [logs]);

  return (
    <div className="detail-panel">
      <div className="detail-header">
        <div>
          <h2><StatusDot state={cell.state} /> {cell.cell_id.slice(0, 12)}...</h2>
          <span className="detail-template">{cell.template_id}</span>
        </div>
        <button className="btn-icon" onClick={onClose} title="Close">✕</button>
      </div>

      <div className="detail-meta">
        <div><strong>State:</strong> {cell.state}</div>
        <div><strong>Started:</strong> {timeAgo(cell.started_at)}</div>
        <div><strong>Expires:</strong> {cell.end_at ? timeAgo(cell.end_at) : '—'}</div>
        {cell.metadata && Object.keys(cell.metadata).length > 0 && (
          <div>
            <strong>Metadata:</strong>
            {Object.entries(cell.metadata).map(([k, v]) => (
              <span key={k} className="tag">{k}={v}</span>
            ))}
          </div>
        )}
      </div>

      <div className="detail-actions">
        {cell.state === 'running' && (
          <>
            <button className="btn-warning" onClick={pauseCell}>⏸ Pause</button>
            <button className="btn-danger" onClick={killCell}>⏹ Kill</button>
          </>
        )}
      </div>

      {/* Command runner */}
      <div className="command-runner">
        <h3>Execute Command</h3>
        <div className="cmd-input-row">
          <input
            value={cmd}
            onChange={e => setCmd(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runCommand()}
            placeholder="ls -la /data"
          />
          <button className="btn-primary" onClick={runCommand}>Run</button>
        </div>
        {cmdResult && <pre className="cmd-output">{cmdResult}</pre>}
      </div>

      {/* Logs */}
      <div className="log-section">
        <h3>Logs</h3>
        <div className="log-viewer" ref={logsRef}>
          {logs.length === 0 ? (
            <div className="log-empty">No logs available</div>
          ) : (
            logs.map((line, i) => <div key={i} className="log-line">{line}</div>)
          )}
        </div>
      </div>
    </div>
  );
}

// ─── Main App ───────────────────────────────────────────────────

type Page = 'sandboxes' | 'templates' | 'metrics' | 'settings';

function App() {
  const [page, setPage] = useState<Page>('sandboxes');
  const [cells, setCells] = useState<CellInfo[]>([]);
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [health, setHealth] = useState<GatewayHealth | null>(null);
  const [selectedCell, setSelectedCell] = useState<CellInfo | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setError('');
      const [h, c] = await Promise.all([
        api<GatewayHealth>('GET', '/v1/health').catch(() => null),
        api<{ items: CellInfo[] }>('GET', '/v1/cells').catch(() => ({ items: [] })),
      ]);
      if (h) setHealth(h);
      setCells(Array.isArray(c) ? c : (c.items || []));
      setLoading(false);
    } catch (e: any) {
      setError(e.message);
      setLoading(false);
    }
  }, []);

  const loadTemplates = useCallback(async () => {
    try {
      const t = await api<TemplateInfo[]>('GET', '/v1/templates');
      setTemplates(Array.isArray(t) ? t : []);
    } catch { /* gateway may not have templates endpoint */ }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 5000);
    return () => clearInterval(interval);
  }, [refresh]);

  useEffect(() => {
    if (page === 'templates') loadTemplates();
  }, [page, loadTemplates]);

  const running = cells.filter(c => c.state === 'running').length;
  const paused = cells.filter(c => c.state === 'paused').length;

  return (
    <div className="app">
      {/* Sidebar */}
      <nav className="sidebar">
        <div className="brand">
          <div className="brand-icon">⬡</div>
          <span className="brand-text">Synapse</span>
        </div>

        <div className="nav-section">
          <button
            className={`nav-item ${page === 'sandboxes' ? 'active' : ''}`}
            onClick={() => setPage('sandboxes')}
          >
            <span className="nav-icon">◉</span> Sandboxes
            {running > 0 && <span className="nav-badge">{running}</span>}
          </button>
          <button
            className={`nav-item ${page === 'templates' ? 'active' : ''}`}
            onClick={() => setPage('templates')}
          >
            <span className="nav-icon">⧉</span> Templates
          </button>
          <button
            className={`nav-item ${page === 'metrics' ? 'active' : ''}`}
            onClick={() => setPage('metrics')}
          >
            <span className="nav-icon">◫</span> Metrics
          </button>
        </div>

        <div className="nav-footer">
          <button className="nav-item" onClick={() => setShowSettings(true)}>
            <span className="nav-icon">⚙</span> Settings
          </button>
          <div className="gateway-status">
            <span className={`gw-dot ${health ? 'online' : 'offline'}`} />
            {health ? `v${health.version}` : 'Disconnected'}
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main className="main">
        {/* Top Bar */}
        <header className="topbar">
          <h1>
            {page === 'sandboxes' && 'Sandboxes'}
            {page === 'templates' && 'Templates'}
            {page === 'metrics' && 'Metrics'}
          </h1>
          <div className="topbar-actions">
            <button className="btn-ghost" onClick={refresh} title="Refresh">↻</button>
          </div>
        </header>

        {error && (
          <div className="error-banner">
            <span>⚠ {error}</span>
            <button onClick={() => setShowSettings(true)}>Configure</button>
          </div>
        )}

        {/* Stats Row */}
        {page === 'sandboxes' && (
          <div className="stats-row">
            <StatCard label="Active" value={running} accent="#10b981" />
            <StatCard label="Paused" value={paused} accent="#f59e0b" />
            <StatCard label="Total" value={cells.length} />
            {health && (
              <StatCard
                label="Gateway Uptime"
                value={formatUptime(health.uptime_s || 0)}
                sub={health.status}
              />
            )}
          </div>
        )}

        {/* Sandboxes Page */}
        {page === 'sandboxes' && (
          <div className="content-grid">
            <div className={`cell-list ${selectedCell ? 'with-detail' : ''}`}>
              {loading ? (
                <div className="empty-state">
                  <div className="spinner" />
                  <p>Connecting to gateway...</p>
                </div>
              ) : cells.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-icon">◉</div>
                  <h3>No sandboxes running</h3>
                  <p>Create one with <code>synapse sandbox create</code></p>
                </div>
              ) : (
                <table className="cell-table">
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>Template</th>
                      <th>State</th>
                      <th>Started</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cells.map(cell => (
                      <tr
                        key={cell.cell_id}
                        className={selectedCell?.cell_id === cell.cell_id ? 'selected' : ''}
                        onClick={() => setSelectedCell(cell)}
                      >
                        <td className="mono">{cell.cell_id.slice(0, 12)}...</td>
                        <td><span className="template-badge">{cell.template_id}</span></td>
                        <td><StatusDot state={cell.state} /> {cell.state}</td>
                        <td>{timeAgo(cell.started_at)}</td>
                        <td>
                          <button
                            className="btn-sm btn-danger"
                            onClick={async (e) => {
                              e.stopPropagation();
                              await api('DELETE', `/v1/cells/${cell.cell_id}`).catch(() => {});
                              refresh();
                            }}
                          >
                            Kill
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            {selectedCell && (
              <CellDetail
                cell={selectedCell}
                onClose={() => setSelectedCell(null)}
                onAction={refresh}
              />
            )}
          </div>
        )}

        {/* Templates Page */}
        {page === 'templates' && (
          <div className="templates-grid">
            {templates.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">⧉</div>
                <h3>No templates registered</h3>
                <p>Create one with <code>synapse template create my-env</code></p>
              </div>
            ) : (
              templates.map(t => (
                <div key={t.name} className="template-card">
                  <div className="template-card-header">
                    <h3>{t.name}</h3>
                    <span className={`compile-badge ${t.compiled ? 'compiled' : 'pending'}`}>
                      {t.compiled ? '✓ compiled' : '◌ pending'}
                    </span>
                  </div>
                  <div className="template-runtime">{t.runtime}</div>
                  {t.description && <p className="template-desc">{t.description}</p>}
                  {t.packages?.length > 0 && (
                    <div className="template-packages">
                      {t.packages.slice(0, 5).map(p => (
                        <span key={p} className="tag">{p}</span>
                      ))}
                      {t.packages.length > 5 && <span className="tag">+{t.packages.length - 5}</span>}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        )}

        {/* Metrics Page */}
        {page === 'metrics' && (
          <div className="metrics-page">
            <div className="stats-row">
              <StatCard label="Active Cells" value={running} accent="#10b981" />
              <StatCard label="Total Created" value={health?.cells_total || cells.length} />
              <StatCard
                label="Gateway Uptime"
                value={health ? formatUptime(health.uptime_s) : '—'}
              />
              <StatCard label="SDK Version" value="0.5.2" />
            </div>
            <div className="metrics-chart-placeholder">
              <div className="empty-icon">◫</div>
              <h3>Usage Over Time</h3>
              <p>Time-series metrics will appear here once cells are running.</p>
              <div className="metrics-bar-chart">
                {Array.from({ length: 24 }, (_, i) => (
                  <div
                    key={i}
                    className="metrics-bar"
                    style={{ height: `${Math.random() * 80 + 10}%` }}
                  />
                ))}
              </div>
              <div className="metrics-bar-labels">
                <span>24h ago</span>
                <span>12h ago</span>
                <span>now</span>
              </div>
            </div>
          </div>
        )}
      </main>

      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  );
}

export default App;
