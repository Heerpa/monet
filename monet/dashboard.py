"""
    monet/dashboard.py
    ~~~~~~~~~~~~~~~~~~

    Interactive web dashboard for the Monet calibration database.
    Mounted at /dashboard by server.py (imported at the bottom of that file
    to avoid circular-import issues).

    :authors: Heinrich Grabmayr, 2024
    :copyright: Copyright (c) 2024 Jungmann Lab, MPI of Biochemistry
"""
import json
from typing import List, Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select

from monet.models import Calibration

# Imported lazily inside each handler to avoid a circular-import at module
# load time (server.py imports this module at its very bottom).
import monet.server as _server  # noqa: E402

router = APIRouter(prefix='/dashboard', tags=['dashboard'])


# ── Pydantic schema ────────────────────────────────────────────────────────────

class TimeseriesRequest(BaseModel):
    devices: Optional[List[str]] = None
    wavelengths: Optional[List[float]] = None
    laser_powers: Optional[List[float]] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None


# ── API endpoints ──────────────────────────────────────────────────────────────

@router.get('/api/filters')
def get_filters():
    """Return unique filter values and date range for sidebar population."""
    with _server._get_session() as session:
        rows = session.execute(select(Calibration)).scalars().all()

    if not rows:
        return {
            'devices': [],
            'wavelengths': [],
            'laser_powers': [],
            'date_min': None,
            'date_max': None,
        }

    devices = sorted(set(r.device_name for r in rows))
    wavelengths = sorted(set(r.wavelength_nm for r in rows))
    laser_powers = sorted(set(r.laser_power_mw for r in rows))
    dates = [r.calibration_date for r in rows]
    return {
        'devices': devices,
        'wavelengths': wavelengths,
        'laser_powers': laser_powers,
        'date_min': min(dates),
        'date_max': max(dates),
    }


@router.post('/api/timeseries')
def get_timeseries(req: TimeseriesRequest):
    """Return filtered calibration records for the dashboard charts."""
    with _server._get_session() as session:
        stmt = select(Calibration).order_by(
            Calibration.device_name,
            Calibration.wavelength_nm,
            Calibration.laser_power_mw,
            Calibration.calibration_date,
            Calibration.calibration_time,
        )
        if req.devices:
            stmt = stmt.where(Calibration.device_name.in_(req.devices))
        if req.wavelengths:
            stmt = stmt.where(Calibration.wavelength_nm.in_(req.wavelengths))
        if req.laser_powers:
            stmt = stmt.where(Calibration.laser_power_mw.in_(req.laser_powers))
        if req.date_from:
            stmt = stmt.where(Calibration.calibration_date >= req.date_from)
        if req.date_to:
            stmt = stmt.where(Calibration.calibration_date <= req.date_to)

        rows = session.execute(stmt).scalars().all()

    records = []
    for r in rows:
        dt = f'{r.calibration_date}T{r.calibration_time}:00'
        records.append({
            'device': r.device_name,
            'wavelength': r.wavelength_nm,
            'laser_power': r.laser_power_mw,
            'date': r.calibration_date,
            'time': r.calibration_time,
            'dt': dt,
            'parameters': json.loads(r.parameters_json),
        })
    return {'records': records}


# ── HTML page ──────────────────────────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Monet Dashboard</title>
  <link rel="stylesheet"
        href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
  <style>
    body { overflow-x: hidden; font-size: 0.9rem; }
    #sidebar {
      width: 230px; min-width: 230px; height: 100vh;
      position: sticky; top: 0; overflow-y: auto;
      background: #f8f9fa; border-right: 1px solid #dee2e6;
      padding: 1rem; flex-shrink: 0;
    }
    #main { flex: 1; overflow-y: auto; padding: 1.25rem; min-width: 0; }
    select[multiple] { min-height: 88px; font-size: 0.8rem; }
    .tab-btn {
      cursor: pointer; border: none; background: none;
      padding: 0.45rem 1rem; border-bottom: 2px solid transparent;
      color: #555;
    }
    .tab-btn.active { border-bottom-color: #0d6efd; color: #0d6efd; font-weight: 600; }
    .tab-pane { display: none; }
    .tab-pane.active { display: block; }
    .chart-card {
      background: #fff; border: 1px solid #dee2e6; border-radius: 6px;
      padding: 0.75rem; margin-bottom: 1rem;
    }
    #loading-overlay {
      display: none; position: fixed; top: 0; left: 0;
      width: 100%; height: 100%; background: rgba(255,255,255,0.78);
      z-index: 9999; align-items: center; justify-content: center;
    }
    #loading-overlay.show { display: flex; }
    .wl-dot {
      display: inline-block; width: 10px; height: 10px;
      border-radius: 50%; margin-right: 4px; vertical-align: middle;
    }
    .params-cell {
      max-width: 280px; overflow: hidden; text-overflow: ellipsis;
      white-space: nowrap; cursor: help; font-family: monospace; font-size: 0.75rem;
    }
    .plotly-chart { min-height: 160px; }
  </style>
</head>
<body>

<!-- Loading overlay -->
<div id="loading-overlay">
  <div class="spinner-border text-primary" role="status">
    <span class="visually-hidden">Loading…</span>
  </div>
</div>

<div class="d-flex" style="height:100vh;">

  <!-- ── Sidebar ──────────────────────────────────────────────────────────── -->
  <div id="sidebar">
    <h6 class="fw-bold mb-3 text-primary">Monet Dashboard</h6>

    <label class="form-label small fw-semibold mb-1">Microscopes</label>
    <div class="d-flex gap-1 mb-1">
      <button class="btn btn-outline-secondary btn-sm py-0 px-1"
              onclick="selectAll('sel-devices')">All</button>
      <button class="btn btn-outline-secondary btn-sm py-0 px-1"
              onclick="selectNone('sel-devices')">None</button>
    </div>
    <select id="sel-devices" multiple class="form-select mb-3"></select>

    <label class="form-label small fw-semibold mb-1">Wavelengths (nm)</label>
    <div class="d-flex gap-1 mb-1">
      <button class="btn btn-outline-secondary btn-sm py-0 px-1"
              onclick="selectAll('sel-wavelengths')">All</button>
      <button class="btn btn-outline-secondary btn-sm py-0 px-1"
              onclick="selectNone('sel-wavelengths')">None</button>
    </div>
    <select id="sel-wavelengths" multiple class="form-select mb-3"></select>

    <label class="form-label small fw-semibold mb-1">Laser Power (mW)</label>
    <div class="d-flex gap-1 mb-1">
      <button class="btn btn-outline-secondary btn-sm py-0 px-1"
              onclick="selectAll('sel-powers')">All</button>
      <button class="btn btn-outline-secondary btn-sm py-0 px-1"
              onclick="selectNone('sel-powers')">None</button>
    </div>
    <select id="sel-powers" multiple class="form-select mb-3"></select>

    <label class="form-label small fw-semibold mb-1">Date from</label>
    <input type="date" id="date-from" class="form-control form-control-sm mb-2">
    <label class="form-label small fw-semibold mb-1">Date to</label>
    <input type="date" id="date-to"   class="form-control form-control-sm mb-3">

    <button class="btn btn-primary btn-sm w-100 mb-3" onclick="update()">Update</button>

    <div class="small text-muted">
      <div>Microscopes: <span id="stat-devices"  class="fw-semibold text-dark">—</span></div>
      <div>Records:     <span id="stat-records"  class="fw-semibold text-dark">—</span></div>
    </div>
  </div>

  <!-- ── Main ─────────────────────────────────────────────────────────────── -->
  <div id="main">

    <!-- Tab bar -->
    <div class="border-bottom mb-3">
      <button class="tab-btn"        data-tab="param-history"
              onclick="switchTab('param-history')">Parameter History</button>
      <button class="tab-btn active" data-tab="power-range"
              onclick="switchTab('power-range')">Power History</button>
      <button class="tab-btn"        data-tab="latest-table"
              onclick="switchTab('latest-table')">Latest Calibrations</button>
    </div>

    <div id="tab-param-history" class="tab-pane">
      <div id="charts-param"></div>
    </div>
    <div id="tab-power-range"   class="tab-pane active">
      <div id="charts-power"></div>
    </div>
    <div id="tab-latest-table"  class="tab-pane">
      <div id="charts-latest"></div>
      <div id="table-latest"  class="mt-3"></div>
    </div>

  </div>
</div>

<script>
'use strict';

// ── Wavelength → color ────────────────────────────────────────────────────────
function wlColor(nm) {
  nm = parseFloat(nm);
  if (nm <= 415) return '#9B30FF';  // violet  ~405
  if (nm <= 460) return '#2255FF';  // blue    ~445
  if (nm <= 510) return '#00AAFF';  // cyan    ~488
  if (nm <= 548) return '#00CC44';  // green   ~532
  if (nm <= 580) return '#AACC00';  // yellow  ~561
  if (nm <= 620) return '#FF8800';  // orange  ~594
  return '#FF2222';                 // red     ~638/647+
}

// ── Model-type detection ──────────────────────────────────────────────────────
function modelType(params) {
  if ('bkg' in params && 'phi' in params) return 'sinusoidal';
  if ('p0'  in params)                    return 'polynomial';
  if (Object.keys(params).length === 1 && 'amp' in params) return 'point';
  return 'unknown';
}

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === name);
  });
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  // Allow the browser to paint before resizing Plotly charts
  setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
}

// ── Sidebar helpers ───────────────────────────────────────────────────────────
function selectAll(id)  { for (const o of document.getElementById(id).options) o.selected = true;  }
function selectNone(id) { for (const o of document.getElementById(id).options) o.selected = false; }
function getSelected(id) {
  return Array.from(document.getElementById(id).selectedOptions).map(o => o.value);
}

function populate(id, values, labelFn, valueFn) {
  const sel = document.getElementById(id);
  sel.innerHTML = '';
  values.forEach(v => {
    const opt = document.createElement('option');
    opt.value = valueFn ? String(valueFn(v)) : String(v);
    opt.textContent = labelFn(v);
    sel.appendChild(opt);
  });
}

// ── Initialise sidebar ────────────────────────────────────────────────────────
async function init() {
  showLoading(true);
  try {
    const res  = await fetch('/dashboard/api/filters');
    const data = await res.json();
    populate('sel-devices',     data.devices,      v => v,           v => v);
    populate('sel-wavelengths', data.wavelengths,  v => v + '\\u202fnm', v => v);
    populate('sel-powers',      data.laser_powers, v => v + '\\u202fmW', v => v);
    if (data.date_min) document.getElementById('date-from').value = data.date_min;
    if (data.date_max) document.getElementById('date-to').value   = data.date_max;
    selectAll('sel-devices');
    selectAll('sel-wavelengths');
    selectAll('sel-powers');
    await update();
  } catch (err) {
    console.error('init error', err);
  } finally {
    showLoading(false);
  }
}

// ── Fetch and render ──────────────────────────────────────────────────────────
async function update() {
  showLoading(true);
  try {
    const devs = getSelected('sel-devices');
    const wls  = getSelected('sel-wavelengths').map(Number);
    const pows = getSelected('sel-powers').map(Number);
    const body = {
      devices:      devs.length  ? devs  : null,
      wavelengths:  wls.length   ? wls   : null,
      laser_powers: pows.length  ? pows  : null,
      date_from: document.getElementById('date-from').value || null,
      date_to:   document.getElementById('date-to').value   || null,
    };
    const res  = await fetch('/dashboard/api/timeseries', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data    = await res.json();
    const records = data.records || [];

    const uniqDevices = new Set(records.map(r => r.device));
    document.getElementById('stat-devices').textContent = uniqDevices.size;
    document.getElementById('stat-records').textContent = records.length;

    renderParamHistory(records);
    renderPowerRange(records);
    renderLatestTable(records);
  } catch (err) {
    console.error('update error', err);
  } finally {
    showLoading(false);
  }
}

function showLoading(show) {
  document.getElementById('loading-overlay').classList.toggle('show', show);
}

// ── Utility ───────────────────────────────────────────────────────────────────
function groupBy(arr, keyFn) {
  return arr.reduce((acc, item) => {
    const k = keyFn(item);
    if (!acc[k]) acc[k] = [];
    acc[k].push(item);
    return acc;
  }, {});
}

function emptyMsg(text) {
  const p = document.createElement('p');
  p.className = 'text-muted';
  p.textContent = text;
  return p;
}

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 1 — Parameter History
// ═══════════════════════════════════════════════════════════════════════════════
function renderParamHistory(records) {
  const container = document.getElementById('charts-param');
  container.innerHTML = '';
  if (!records.length) { container.appendChild(emptyMsg('No data.')); return; }

  const byDevice = groupBy(records, r => r.device);

  for (const [device, recs] of Object.entries(byDevice)) {
    const card     = document.createElement('div');
    card.className = 'chart-card';
    const h6 = document.createElement('h6');
    h6.className   = 'fw-semibold mb-2';
    h6.textContent = device;
    card.appendChild(h6);

    const sinRecs  = recs.filter(r => modelType(r.parameters) === 'sinusoidal');
    const ptRecs   = recs.filter(r => modelType(r.parameters) === 'point');
    const polyRecs = recs.filter(r => modelType(r.parameters) === 'polynomial');

    // Polynomial-only: direct to table tab
    if (!sinRecs.length && !ptRecs.length && polyRecs.length) {
      card.appendChild(emptyMsg(
        'Polynomial calibrations — see the Latest Calibrations tab for parameters.'
      ));
      container.appendChild(card);
      continue;
    }

    const traces = [];
    const layout = {
      margin: { t: 10, b: 45, l: 55, r: 15 },
      hovermode: 'x unified',
      legend: { orientation: 'h', y: -0.18, font: { size: 11 } },
    };

    if (sinRecs.length) {
      // Stacked 3-subplot layout: bkg (top) / amp (mid) / phi (bot)
      layout.yaxis  = { domain: [0.70, 1.00], title: { text: 'bkg', standoff: 4 } };
      layout.yaxis2 = { domain: [0.36, 0.64], title: { text: 'amp', standoff: 4 } };
      layout.yaxis3 = { domain: [0.00, 0.30], title: { text: 'phi', standoff: 4 } };
      layout.xaxis  = { anchor: 'y3' };
      layout.height = 500;

      const sinByWL = groupBy(sinRecs, r => r.wavelength);
      for (const [wl, wlRecs] of Object.entries(sinByWL)) {
        const color = wlColor(wl);
        const xs    = wlRecs.map(r => r.dt);
        const name  = wl + '\\u202fnm';
        const base  = { x: xs, name, legendgroup: name,
                         mode: 'lines+markers', line: { color }, marker: { color } };
        traces.push({ ...base, y: wlRecs.map(r => r.parameters.bkg), yaxis: 'y',
                                showlegend: true });
        traces.push({ ...base, y: wlRecs.map(r => r.parameters.amp), yaxis: 'y2',
                                showlegend: false });
        traces.push({ ...base, y: wlRecs.map(r => r.parameters.phi), yaxis: 'y3',
                                showlegend: false });
      }
    }

    if (ptRecs.length) {
      if (!sinRecs.length) {
        layout.yaxis  = { title: { text: 'amp' } };
        layout.height = 280;
      }
      const ptByWL = groupBy(ptRecs, r => r.wavelength);
      for (const [wl, wlRecs] of Object.entries(ptByWL)) {
        const color = wlColor(wl);
        traces.push({
          x: wlRecs.map(r => r.dt),
          y: wlRecs.map(r => r.parameters.amp),
          name: wl + '\\u202fnm', legendgroup: wl + '\\u202fnm',
          mode: 'lines+markers', line: { color }, marker: { color },
          yaxis: sinRecs.length ? 'y' : 'y',
          showlegend: true,
        });
      }
    }

    const chartDiv       = document.createElement('div');
    chartDiv.className   = 'plotly-chart';
    card.appendChild(chartDiv);
    container.appendChild(card);

    if (traces.length) {
      Plotly.newPlot(chartDiv, traces, layout, { responsive: true, displayModeBar: false });
    } else {
      chartDiv.appendChild(emptyMsg('No plottable records for this device.'));
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 2 — Power Range
// ═══════════════════════════════════════════════════════════════════════════════
function renderPowerRange(records) {
  const container = document.getElementById('charts-power');
  container.innerHTML = '';
  if (!records.length) { container.appendChild(emptyMsg('No data.')); return; }

  const byDevice = groupBy(records, r => r.device);

  for (const [device, recs] of Object.entries(byDevice)) {
    const card     = document.createElement('div');
    card.className = 'chart-card';
    const h6 = document.createElement('h6');
    h6.className   = 'fw-semibold mb-2';
    h6.textContent = device;
    card.appendChild(h6);

    const traces = [];
    const byKey  = groupBy(recs, r => r.wavelength + '__' + r.laser_power);

    for (const [, kRecs] of Object.entries(byKey)) {
      const wl    = kRecs[0].wavelength;
      const lp    = kRecs[0].laser_power;
      const color = wlColor(wl);
      const xs    = kRecs.map(r => r.dt);
      const mt    = modelType(kRecs[0].parameters);
      const name  = wl + '\\u202fnm / ' + lp + '\\u202fmW';

      if (mt === 'sinusoidal') {
        traces.push({
          x: xs, y: kRecs.map(r => r.parameters.bkg + r.parameters.amp),
          name: name + ' max', legendgroup: name,
          mode: 'lines+markers', line: { color }, marker: { color },
          showlegend: true,
        });
        traces.push({
          x: xs, y: kRecs.map(r => r.parameters.bkg),
          name: name + ' bkg', legendgroup: name,
          mode: 'lines+markers', line: { color, dash: 'dot' }, marker: { color },
          showlegend: true,
        });
      } else if (mt === 'point') {
        traces.push({
          x: xs, y: kRecs.map(r => r.parameters.amp),
          name, legendgroup: name,
          mode: 'markers', marker: { color, symbol: 'diamond', size: 9 },
          showlegend: true,
        });
      }
    }

    const chartDiv       = document.createElement('div');
    chartDiv.className   = 'plotly-chart';
    card.appendChild(chartDiv);
    container.appendChild(card);

    if (traces.length) {
      Plotly.newPlot(chartDiv, traces, {
        margin: { t: 10, b: 45, l: 55, r: 15 },
        hovermode: 'x unified',
        legend: { orientation: 'h', y: -0.22, font: { size: 11 } },
        yaxis:  { title: 'Power (mW)' },
        height: 300,
      }, { responsive: true, displayModeBar: false });
    } else {
      chartDiv.appendChild(emptyMsg('No plottable records (polynomial models not shown here).'));
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// TAB 3 — Latest Calibrations Table
// ═══════════════════════════════════════════════════════════════════════════════
// ── Latest Calibrations: parameters vs laser-power charts ─────────────────────
function renderLatestCharts(rows) {
  const container = document.getElementById('charts-latest');
  container.innerHTML = '';
  if (!rows.length) return;

  // One chart per wavelength; each series = one microscope.
  // X-axis: laser power setting (mW).  Y-axis: output power (bkg+amp or amp).
  const byWL = groupBy(rows, r => r.wavelength);
  const sortedWLs = Object.keys(byWL).map(Number).sort((a, b) => a - b);

  for (const wl of sortedWLs) {
    const wlRecs = byWL[wl].slice().sort((a, b) => a.laser_power - b.laser_power);

    const card = document.createElement('div');
    card.className = 'chart-card';
    const h6 = document.createElement('h6');
    h6.className = 'fw-semibold mb-2';
    h6.style.color = wlColor(wl);
    h6.textContent = wl + '\\u202fnm';
    card.appendChild(h6);

    const traces = [];
    const byDevice = groupBy(wlRecs, r => r.device);

    for (const [device, recs] of Object.entries(byDevice)) {
      recs.sort((a, b) => a.laser_power - b.laser_power);
      const xs      = recs.map(r => r.laser_power);
      const sinRecs = recs.filter(r => modelType(r.parameters) === 'sinusoidal');
      const ptRecs  = recs.filter(r => modelType(r.parameters) === 'point');

      if (sinRecs.length) {
        const sxs = sinRecs.map(r => r.laser_power);
        traces.push({
          x: sxs, y: sinRecs.map(r => r.parameters.bkg + r.parameters.amp),
          name: device + ' max', legendgroup: device,
          mode: 'lines+markers', showlegend: true,
        });
        traces.push({
          x: sxs, y: sinRecs.map(r => r.parameters.bkg),
          name: device + ' bkg', legendgroup: device,
          mode: 'lines+markers', line: { dash: 'dot' }, showlegend: true,
        });
      }
      if (ptRecs.length) {
        traces.push({
          x: ptRecs.map(r => r.laser_power), y: ptRecs.map(r => r.parameters.amp),
          name: device, legendgroup: device,
          mode: 'markers', marker: { symbol: 'diamond', size: 9 }, showlegend: true,
        });
      }
    }

    const chartDiv = document.createElement('div');
    chartDiv.className = 'plotly-chart';
    card.appendChild(chartDiv);
    container.appendChild(card);

    if (traces.length) {
      Plotly.newPlot(chartDiv, traces, {
        margin: { t: 10, b: 50, l: 55, r: 15 },
        hovermode: 'x unified',
        legend: { orientation: 'h', y: -0.22, font: { size: 11 } },
        xaxis: { title: 'Laser power setting (mW)' },
        yaxis: { title: 'Power (mW)' },
        height: 300,
      }, { responsive: true, displayModeBar: false });
    } else {
      chartDiv.appendChild(emptyMsg('No plottable records for this wavelength.'));
    }
  }
}

function renderLatestTable(records) {
  const container = document.getElementById('table-latest');
  container.innerHTML = '';
  if (!records.length) {
    document.getElementById('charts-latest').innerHTML = '';
    container.appendChild(emptyMsg('No data.')); return;
  }

  // Keep only the most recent record per (device, wavelength, laser_power)
  const latest = {};
  for (const r of records) {
    const key = r.device + '__' + r.wavelength + '__' + r.laser_power;
    if (!latest[key] || r.dt > latest[key].dt) latest[key] = r;
  }
  const rows = Object.values(latest).sort((a, b) =>
    a.device.localeCompare(b.device) ||
    a.wavelength  - b.wavelength     ||
    a.laser_power - b.laser_power
  );

  renderLatestCharts(rows);

  const wrap = document.createElement('div');
  wrap.className = 'table-responsive';

  const table = document.createElement('table');
  table.className = 'table table-sm table-hover table-bordered align-middle';
  table.innerHTML = `<thead class="table-light"><tr>
    <th>Microscope</th><th>Wavelength</th><th>Power (mW)</th>
    <th>Date</th><th>Time</th><th>Model</th><th>Parameters</th>
  </tr></thead>`;

  const tbody = document.createElement('tbody');
  for (const r of rows) {
    const color    = wlColor(r.wavelength);
    const mt       = modelType(r.parameters);
    const paramStr = JSON.stringify(r.parameters);
    const shortP   = paramStr.length > 58 ? paramStr.slice(0, 55) + '\\u2026' : paramStr;
    const safePS   = paramStr.replace(/&/g,'&amp;').replace(/"/g,'&quot;');

    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${r.device}</td>
      <td><span class="wl-dot" style="background:${color}"></span>${r.wavelength}</td>
      <td>${r.laser_power}</td>
      <td>${r.date}</td>
      <td>${r.time}</td>
      <td><span class="badge bg-secondary">${mt}</span></td>
      <td class="params-cell" title="${safePS}">${shortP}</td>`;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  container.appendChild(wrap);
}

// ── Boot ──────────────────────────────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>"""


@router.get('/', response_class=HTMLResponse)
def get_dashboard():
    """Serve the interactive dashboard HTML page."""
    return HTMLResponse(content=_DASHBOARD_HTML)
