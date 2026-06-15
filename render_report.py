#!/usr/bin/env python3
"""
render_report.py

Reads ./data/samples.csv (raw per-poll samples produced by
claude_quota_logger.py) and DERIVES per-window history from it:

  * 5-hour windows  — grouped by five_hour.resets_at
  * 7-day windows   — grouped by seven_day.resets_at

For each window: final = utilization of the latest sample in it, peak = max
utilization seen. Windows whose reset time is in the past are "completed"; the
one window whose reset is in the future is the live/current one.

Writes a single self-contained, offline HTML report:
    ./data/report.html

Usage:
    python3 render_report.py
    python3 render_report.py --open      # also open it in the browser
"""

import csv
import json
import sys
import datetime
import webbrowser
import pathlib

DATA_DIR = pathlib.Path(__file__).resolve().parent / "data"
SAMPLES_PATH = DATA_DIR / "samples.csv"
HTML_PATH = DATA_DIR / "report.html"


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _norm_reset(iso):
    """The API jitters resets_at on every call, and the jitter straddles the
    whole-second boundary (e.g. 12:30:00.5 vs 12:29:59.99 for the same window).
    Reset times always land on a whole minute, so round to the nearest minute
    (UTC) — otherwise truncating to seconds would split one window in two."""
    try:
        d = datetime.datetime.fromisoformat(iso)
        d = (d + datetime.timedelta(seconds=30)).replace(second=0, microsecond=0)
        return d.isoformat()
    except Exception:
        return iso


def load_samples():
    if not SAMPLES_PATH.exists():
        sys.exit(f"No data yet at {SAMPLES_PATH}. "
                 f"Run claude_quota_logger.py for a while first.")
    rows = []
    with SAMPLES_PATH.open(newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


def derive_windows(samples, reset_key, util_key):
    """Group samples by their reset timestamp -> one window each.

    Returns windows sorted by reset time ascending, each:
        {reset, final, peak, samples}
    `final` is the utilization of the latest-recorded sample in the window.
    """
    groups = {}
    for s in samples:
        reset = _norm_reset(s.get(reset_key))
        util = _num(s.get(util_key))
        if not reset or util is None:
            continue
        g = groups.setdefault(reset, {"reset": reset, "peak": util,
                                      "final": util, "_last": s.get("recorded_at_utc", ""),
                                      "samples": 0})
        g["samples"] += 1
        g["peak"] = max(g["peak"], util)
        rec = s.get("recorded_at_utc", "")
        if rec >= g["_last"]:
            g["_last"] = rec
            g["final"] = util
    out = []
    for g in sorted(groups.values(), key=lambda g: g["reset"]):
        out.append({
            "reset": g["reset"],
            "final": round(g["final"], 1),
            "peak": round(g["peak"], 1),
            "samples": g["samples"],
        })
    return out


def split_live(windows):
    """Separate the still-open window (reset in the future) from completed ones."""
    now = datetime.datetime.now(datetime.timezone.utc)

    def is_future(w):
        try:
            return datetime.datetime.fromisoformat(w["reset"]) > now
        except Exception:
            return False

    completed = [w for w in windows if not is_future(w)]
    live = next((w for w in windows if is_future(w)), None)
    return completed, live


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Quota History</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=swap');

  :root{
    --bg:#0a0e0c; --panel:#0f1512; --grid:#1c2620;
    --ink:#cfe8d8; --dim:#5f7a6c; --line:#1f2a24;
    --phos:#36e27a;        /* final utilization */
    --amber:#e0a23b;       /* peak utilization */
    --cyan:#4fd0e0;        /* 7-day */
    --danger:#ff5d5d;
    --serif:'Instrument Serif',Georgia,serif;
    --mono:'IBM Plex Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{
    background:
      radial-gradient(120% 80% at 50% -10%, #11201a 0%, var(--bg) 55%) fixed,
      var(--bg);
    color:var(--ink); font-family:var(--mono); line-height:1.5;
    padding:clamp(20px,5vw,64px); min-height:100vh;
  }
  body::before{ /* faint scanline texture */
    content:""; position:fixed; inset:0; pointer-events:none; opacity:.4; z-index:0;
    background:repeating-linear-gradient(0deg,transparent 0 3px,rgba(0,0,0,.18) 3px 4px);
  }
  .wrap{position:relative; z-index:1; max-width:1040px; margin:0 auto;}

  header{display:flex; align-items:baseline; justify-content:space-between;
    flex-wrap:wrap; gap:12px; border-bottom:1px solid var(--line); padding-bottom:18px;}
  h1{font-family:var(--serif); font-weight:400; font-size:clamp(34px,6vw,56px);
    letter-spacing:.5px; line-height:1;}
  h1 em{color:var(--phos); font-style:italic;}
  .sub{color:var(--dim); font-size:12px; letter-spacing:.16em; text-transform:uppercase;}

  h2{font-family:var(--serif); font-weight:400; font-size:26px; margin:40px 0 4px;
    color:var(--ink);}
  h2 small{font-family:var(--mono); font-size:12px; color:var(--dim); letter-spacing:.1em;}

  .stats{display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
    gap:14px; margin:20px 0 8px;}
  .stat{background:var(--panel); border:1px solid var(--line); border-radius:10px;
    padding:16px 18px;}
  .stat .k{color:var(--dim); font-size:11px; letter-spacing:.14em; text-transform:uppercase;}
  .stat .v{font-family:var(--serif); font-size:40px; line-height:1.1; margin-top:6px;}
  .stat .v small{font-family:var(--mono); font-size:14px; color:var(--dim);}
  .stat .note{color:var(--dim); font-size:11px; letter-spacing:.04em; margin-top:8px;
    padding-top:8px; border-top:1px solid var(--line);}
  .v.phos{color:var(--phos)} .v.amber{color:var(--amber)} .v.cyan{color:var(--cyan)}
  .v.danger{color:var(--danger)}

  .card{background:var(--panel); border:1px solid var(--line); border-radius:14px;
    padding:22px 20px 12px; margin-top:18px;}
  .legend{display:flex; gap:20px; font-size:12px; color:var(--dim); margin:2px 4px 14px;
    align-items:center; flex-wrap:wrap;}
  .legend i{display:inline-block; width:14px; height:3px; border-radius:2px;
    margin-right:7px; vertical-align:middle;}

  svg{width:100%; height:auto; display:block; overflow:visible;}
  .axis{stroke:var(--grid); stroke-width:1;}
  .axislbl{fill:var(--dim); font-family:var(--mono); font-size:11px;}
  .dot{cursor:pointer;}
  .tip{position:fixed; pointer-events:none; background:#05140d; color:var(--ink);
    border:1px solid var(--phos); border-radius:8px; padding:8px 11px; font-size:12px;
    opacity:0; transition:opacity .1s; z-index:5; white-space:nowrap;}
  .tip b{color:var(--phos)}

  table{width:100%; border-collapse:collapse; margin-top:18px; font-size:13px;}
  th,td{padding:9px 12px; text-align:left; border-bottom:1px solid var(--line);}
  th{color:var(--dim); font-size:11px; letter-spacing:.12em; text-transform:uppercase;
    font-weight:500;}
  td.n{text-align:right; font-variant-numeric:tabular-nums;}
  tr:hover td{background:rgba(54,226,122,.05);}
  .bar{display:inline-block; height:7px; border-radius:3px; background:var(--phos);
    vertical-align:middle; margin-left:8px;}
  .empty{color:var(--dim); padding:40px; text-align:center;}
  footer{color:var(--dim); font-size:11px; margin-top:40px; text-align:center;
    letter-spacing:.06em;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Quota&nbsp;<em>utilization</em></h1>
    <div class="sub">Claude · derived from raw samples</div>
  </header>

  <div class="stats" id="stats"></div>

  <h2>5-hour windows <small>· final % at each reset</small></h2>
  <div class="card">
    <div class="legend">
      <span><i style="background:var(--phos)"></i>Final (at reset)</span>
      <span><i style="background:var(--amber)"></i>Peak in window</span>
    </div>
    <div id="chart5"></div>
  </div>
  <div id="table5"></div>

  <h2>7-day windows <small>· weekly quota</small></h2>
  <div id="table7"></div>

  <footer>Generated locally · source: ./data/samples.csv</footer>
</div>

<div class="tip" id="tip"></div>

<script>
const DATA5 = __DATA5__;   // completed 5h windows
const LIVE5 = __LIVE5__;   // current 5h window (may be null)
const DATA7 = __DATA7__;   // completed 7d windows
const LIVE7 = __LIVE7__;   // current 7d window (may be null)

function fmt(iso){
  if(!iso) return "—";
  const d = new Date(iso);
  if(isNaN(d)) return iso;
  return d.toLocaleString([], {month:'short', day:'numeric',
    hour:'2-digit', minute:'2-digit'});
}

// ---- summary stats ----
const finals = DATA5.map(r=>r.final).filter(v=>v!=null);
const avg = finals.length ? (finals.reduce((a,b)=>a+b,0)/finals.length) : 0;
const mx  = finals.length ? Math.max(...finals) : 0;
const hot = DATA5.filter(r=>r.final!=null && r.final>=95).length;
const stats = [
  {k:"5h windows", v:DATA5.length, cls:""},
  {k:"Avg final (5h)", v:avg.toFixed(0), unit:"%", cls:"phos"},
  {k:"Highest final (5h)", v:mx.toFixed(0), unit:"%", cls:"amber"},
  {k:"Hit ≥95%", v:hot, cls:hot? "danger":""},
  {k:"Current 5h", v:LIVE5? LIVE5.final.toFixed(0):"—", unit:LIVE5?"%":"", cls:"phos",
   note:LIVE5? `resets ${fmt(LIVE5.reset)}`:""},
  {k:"Current 7d", v:LIVE7? LIVE7.final.toFixed(0):"—", unit:LIVE7?"%":"", cls:"cyan"},
];
document.getElementById('stats').innerHTML = stats.map(s=>
  `<div class="stat"><div class="k">${s.k}</div>
   <div class="v ${s.cls}">${s.v}${s.unit?`<small>${s.unit}</small>`:''}</div>
   ${s.note?`<div class="note">${s.note}</div>`:''}</div>`
).join('');

// ---- 5h chart ----
const tip = document.getElementById('tip');
function drawChart(){
  const host = document.getElementById('chart5');
  if(!DATA5.length){ host.innerHTML = '<div class="empty">No completed 5h windows recorded yet.</div>'; return; }
  const W=960, H=360, PL=46, PR=16, PT=14, PB=46;
  const iw=W-PL-PR, ih=H-PT-PB;
  const n=DATA5.length;
  const x = i => n<=1 ? PL+iw/2 : PL + (i/(n-1))*iw;
  const y = v => PT + ih - (Math.max(0,Math.min(100,v))/100)*ih;

  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="utilization over time">`;
  for(let g=0; g<=100; g+=25){
    s += `<line class="axis" x1="${PL}" y1="${y(g)}" x2="${W-PR}" y2="${y(g)}"/>`;
    s += `<text class="axislbl" x="${PL-10}" y="${y(g)+4}" text-anchor="end">${g}</text>`;
  }
  const peaks = DATA5.map((r,i)=> r.peak!=null ? `${x(i)},${y(r.peak)}` : null).filter(Boolean);
  if(peaks.length){
    const area = `${PL},${y(0)} ` + peaks.join(' ') + ` ${x(n-1)},${y(0)}`;
    s += `<polygon points="${area}" fill="rgba(224,162,59,.13)" stroke="none"/>`;
    s += `<polyline points="${peaks.join(' ')}" fill="none" stroke="var(--amber)" stroke-width="1.5" stroke-dasharray="3 3" opacity=".8"/>`;
  }
  const finPts = DATA5.map((r,i)=> r.final!=null ? `${x(i)},${y(r.final)}` : null).filter(Boolean);
  if(finPts.length>1)
    s += `<polyline points="${finPts.join(' ')}" fill="none" stroke="var(--phos)" stroke-width="2.5"/>`;
  const step = Math.ceil(n/8);
  DATA5.forEach((r,i)=>{
    if(r.final!=null){
      const col = r.final>=95 ? 'var(--danger)' : 'var(--phos)';
      s += `<circle class="dot" cx="${x(i)}" cy="${y(r.final)}" r="4" fill="${col}" data-i="${i}"/>`;
    }
    if(i%step===0){
      s += `<text class="axislbl" x="${x(i)}" y="${H-PB+20}" text-anchor="middle">${fmt(r.reset).split(',')[0]}</text>`;
    }
  });
  s += `</svg>`;
  host.innerHTML = s;

  host.querySelectorAll('.dot').forEach(d=>{
    d.addEventListener('mousemove', e=>{
      const r = DATA5[+d.dataset.i];
      tip.innerHTML = `${fmt(r.reset)}<br><b>final ${r.final}%</b>`+
        (r.peak!=null?` · peak ${r.peak}%`:'')+` · ${r.samples} samples`;
      tip.style.opacity=1;
      tip.style.left=(e.clientX+14)+'px'; tip.style.top=(e.clientY-10)+'px';
    });
    d.addEventListener('mouseleave', ()=> tip.style.opacity=0);
  });
}
drawChart();

// ---- tables ----
function renderTable(hostId, data, accent){
  const host = document.getElementById(hostId);
  if(!data.length){ host.innerHTML='<div class="empty">No completed windows yet.</div>'; return; }
  const rows = [...data].reverse().map(r=>{
    const w = r.final!=null ? Math.max(2, r.final) : 0;
    const col = r.final!=null && r.final>=95 ? 'var(--danger)' : accent;
    return `<tr>
      <td>${fmt(r.reset)}</td>
      <td class="n">${r.final!=null? r.final+'%':'—'}
        <span class="bar" style="width:${w*1.1}px;background:${col}"></span></td>
      <td class="n">${r.peak!=null? r.peak+'%':'—'}</td>
      <td class="n">${r.samples}</td>
    </tr>`;
  }).join('');
  host.innerHTML =
    `<table><thead><tr><th>Window reset</th><th>Final</th><th>Peak</th><th>Samples</th></tr></thead>
     <tbody>${rows}</tbody></table>`;
}
renderTable('table5', DATA5, 'var(--phos)');
renderTable('table7', DATA7, 'var(--cyan)');
</script>
</body>
</html>
"""


def main():
    samples = load_samples()
    win5 = derive_windows(samples, "fh_resets_at", "fh_util")
    win7 = derive_windows(samples, "wk_resets_at", "wk_util")
    completed5, live5 = split_live(win5)
    completed7, live7 = split_live(win7)

    html = (HTML_TEMPLATE
            .replace("__DATA5__", json.dumps(completed5))
            .replace("__LIVE5__", json.dumps(live5))
            .replace("__DATA7__", json.dumps(completed7))
            .replace("__LIVE7__", json.dumps(live7)))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {HTML_PATH}  "
          f"({len(completed5)} completed 5h windows, "
          f"{len(completed7)} completed 7d windows)")
    if "--open" in sys.argv:
        webbrowser.open(HTML_PATH.as_uri())


if __name__ == "__main__":
    main()
