#!/usr/bin/env python3
"""
Companion display script for cryosparc_storage_scan.py

Usage:
cryosparc_storage_display.py output.csv

Vibe coded by Rob Stass
"""
import csv
import argparse
import os
import html
import json
import shutil
import subprocess
import webbrowser
from collections import defaultdict
import plotly.graph_objects as go

# -------- helpers --------
def read_rows(csvfile):
    rows = []
    with open(csvfile, newline='') as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                r['bytes'] = int(r.get('bytes', 0))
            except Exception:
                r['bytes'] = 0
            r['job_type'] = r.get('job_type', '') or "unknown"
            # New scanner output uses lowercase 'owner'. Accept 'Owner' too,
            # and remain usable with older CSVs that did not contain it.
            r['owner'] = r.get('owner', '') or r.get('Owner', '') or "unknown"
            rows.append(r)
    return rows

def human_readable_bytes(n):
    if n < 1024:
        return f"{n} B"
    units = ["KiB","MiB","GiB","TiB","PiB"]
    v = float(n) / 1024.0
    for u in units:
        if v < 1024.0:
            return f"{v:.2f} {u}"
        v /= 1024.0
    return f"{v:.2f} EiB"

def bytes_to_gib(n):
    return n / (1024.0**3)

def parse_colormap_argument(s, n_colors):
    if not s:
        return None
    s = s.strip()
    if ',' in s:
        parts = [p.strip() for p in s.split(',') if p.strip()]
        out = []
        i = 0
        while len(out) < n_colors:
            out.append(parts[i % len(parts)])
            i += 1
        return out[:n_colors]
    try:
        import matplotlib.cm as cm, matplotlib.colors as mcolors
        cmap = cm.get_cmap(s)
        colors = []
        for i in range(n_colors):
            frac = i / max(1, n_colors-1)
            rgba = cmap(frac)
            hexc = mcolors.to_hex(rgba)
            colors.append(hexc)
        return colors
    except Exception:
        return None

def assign_owner_colors(rows, colormap_arg):
    owners = []
    seen = set()
    for r in rows:
        owner = r.get('owner', '') or 'unknown'
        if owner not in seen:
            seen.add(owner)
            owners.append(owner)
    n = len(owners)
    colors = None
    if colormap_arg:
        colors = parse_colormap_argument(colormap_arg, n)
    if not colors:
        try:
            from plotly.express import colors as pxcols
            palette = pxcols.qualitative.Plotly
            colors = [palette[i % len(palette)] for i in range(n)]
        except Exception:
            base = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd","#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf"]
            colors = [base[i % len(base)] for i in range(n)]
    return {owner: colors[i] for i, owner in enumerate(owners)}

# -------- figure builders --------
def build_jobs_bar(rows, top_n, owner_color_map, total_bytes):
    top = sorted(rows, key=lambda r: r['bytes'], reverse=True)[:top_n]
    labels = [ f"{r['job']}<br><span style='font-size:0.9em;color:gray'>({html.escape(r['project'])})</span>" for r in top ]
    y = [ bytes_to_gib(r['bytes']) for r in top ]
    hover = [ f"Path: {html.escape(r.get('path',''))}<br>Owner: {html.escape(r.get('owner','unknown'))}<br>TLD: {html.escape(r.get('tld',''))}<br>Project: {html.escape(r.get('project',''))}<br>Job: {html.escape(r.get('job',''))}<br>Job type: {html.escape(r.get('job_type','unknown'))}<br>Size: {human_readable_bytes(r['bytes'])}" for r in top ]
    colors = [ owner_color_map.get(r.get('owner','unknown'), None) for r in top ]

    trace = go.Bar(x=labels, y=y, marker=dict(color=colors), hovertext=hover)
    fig = go.Figure(trace)
    fig.update_traces(hoverinfo='text', hovertemplate='%{hovertext}<extra></extra>')
    fig.update_layout(title=f"Top {len(top)} Jobs by Size (GiB)",
                      yaxis_title="GiB",
                      xaxis_tickangle=-45,
                      margin=dict(b=220,t=110,l=80,r=40),
                      height=600,
                      autosize=True,
                      hovermode='closest')

    # add customdata (paths)
    fig.data[0].customdata = [ r.get('path','') for r in top ]

    # compute displayed sum and percent of total, format and add as an annotation under title
    subset_bytes = sum(r['bytes'] for r in top)
    pct = (subset_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
    summary = f"Displayed = {human_readable_bytes(subset_bytes)} ({pct:.2f}% of total)"
    fig.add_annotation(dict(
        text=summary,
        xref="paper", yref="paper",
        x=0, y=1.06,           # slightly below the title
        showarrow=False,
        align="left",
        font=dict(size=12, color="black")
    ))

    return fig

def build_projects_bar(rows, top_n, owner_color_map, total_bytes):
    sums = defaultdict(int); rep_path={}; project_owner={}; project_tld={}
    for r in rows:
        key=(r.get('tld',''), r.get('project',''))
        sums[key] += r['bytes']
        if key not in rep_path:
            rep_path[key] = r.get('path','')
            project_owner[key] = r.get('owner','unknown')
            project_tld[key] = r.get('tld','')
    items = sorted(sums.items(), key=lambda x: x[1], reverse=True)[:top_n]
    labels=[ f"{html.escape(proj)}<br><span style='font-size:0.9em;color:gray'>({html.escape(project_owner.get((tld,proj),'unknown'))})</span>" for (tld,proj),_ in items ]
    y=[ bytes_to_gib(size) for (_,size) in items ]
    hover=[ f"Owner: {html.escape(project_owner.get((tld,proj),'unknown'))}<br>TLD: {html.escape(tld)}<br>Project: {html.escape(proj)}<br>Total size: {human_readable_bytes(size)}" for (tld,proj),size in items ]
    colors=[ owner_color_map.get(project_owner.get((tld,proj),'unknown'), None) for (tld,proj),_ in items ]
    trace = go.Bar(x=labels,y=y,marker=dict(color=colors),hovertext=hover)
    fig = go.Figure(trace)
    fig.update_traces(hoverinfo='text', hovertemplate='%{hovertext}<extra></extra>')
    fig.update_layout(title=f"Top {len(items)} Projects (GiB)",
                      yaxis_title="GiB",
                      xaxis_tickangle=-45,
                      margin=dict(b=200,t=110,l=80,r=40),
                      height=600,
                      autosize=True,
                      hovermode='closest')

    fig.data[0].customdata = [ rep_path[(tld,proj)] for (tld,proj),_ in items ]

    subset_bytes = sum(size for (_, size) in items)
    pct = (subset_bytes / total_bytes * 100.0) if total_bytes > 0 else 0.0
    summary = f"Displayed = {human_readable_bytes(subset_bytes)} ({pct:.2f}% of total)"
    fig.add_annotation(dict(
        text=summary,
        xref="paper", yref="paper",
        x=0, y=1.06,
        showarrow=False,
        align="left",
        font=dict(size=12, color="black")
    ))

    return fig

def build_jobs_pie(rows, owner_color_map, pie_top):
    # group top pie_top jobs, aggregate rest into Other
    items = sorted(rows, key=lambda r: r['bytes'], reverse=True)
    labels = []
    values = []
    hover = []
    colors = []
    customdata = []

    top_items = items[:pie_top]
    other_items = items[pie_top:]

    for r in top_items:
        labels.append(f"{r['project']}/{r['job']}")
        values.append(r['bytes'])
        hover.append(f"Path: {html.escape(r.get('path',''))}<br>Owner: {html.escape(r.get('owner','unknown'))}<br>Job type: {html.escape(r.get('job_type','unknown'))}<br>Size: {human_readable_bytes(r['bytes'])}")
        colors.append(owner_color_map.get(r.get('owner','unknown'), None))
        customdata.append(r.get('path',''))

    if other_items:
        other_sum = sum(r['bytes'] for r in other_items)
        labels.append("Other")
        values.append(other_sum)
        hover.append(f"Other (combined) — {human_readable_bytes(other_sum)}")
        colors.append('#dddddd')
        customdata.append("")  # no single path for Other

    trace = go.Pie(labels=labels, values=values, hovertext=hover,
                   marker=dict(colors=colors, line=dict(color='#ffffff', width=2)),
                   textinfo='percent', textposition='outside', sort=False)
    fig = go.Figure(trace)
    fig.update_traces(hoverinfo='text', hovertemplate='%{hovertext}<extra></extra>')
    fig.update_layout(title=f"Jobs (top {pie_top} + Other) — distribution", height=520, autosize=True, hovermode='closest', showlegend=False)
    fig.data[0].customdata = customdata
    return fig

def build_projects_pie(rows, owner_color_map, pie_top):
    sums = defaultdict(int); rep_path={}; project_owner={}
    for r in rows:
        key=(r.get('tld',''), r.get('project',''))
        sums[key] += r['bytes']
        if key not in rep_path:
            rep_path[key] = r.get('path','')
            project_owner[key] = r.get('owner','unknown')
    items = sorted(list(sums.items()), key=lambda x: x[1], reverse=True)
    labels=[]
    values=[]
    hover=[]
    colors=[]
    customdata=[]

    top_items = items[:pie_top]
    other_items = items[pie_top:]

    for (tld, proj), size in top_items:
        owner = project_owner.get((tld,proj), 'unknown')
        labels.append(proj)
        values.append(size)
        hover.append(f"Project: {html.escape(proj)}<br>Owner: {html.escape(owner)}<br>TLD: {html.escape(tld)}<br>Total size: {human_readable_bytes(size)}")
        colors.append(owner_color_map.get(owner, None))
        customdata.append(rep_path.get((tld,proj), ""))

    if other_items:
        other_sum = sum(size for (_, size) in other_items)
        labels.append("Other")
        values.append(other_sum)
        hover.append(f"Other (combined) — {human_readable_bytes(other_sum)}")
        colors.append('#dddddd')
        customdata.append("")

    trace = go.Pie(labels=labels, values=values, hovertext=hover,
                   marker=dict(colors=colors, line=dict(color='#ffffff', width=2)),
                   textinfo='percent', textposition='outside', sort=False)
    fig = go.Figure(trace)
    fig.update_traces(hoverinfo='text', hovertemplate='%{hovertext}<extra></extra>')
    fig.update_layout(title=f"Projects (top {pie_top} + Other) — distribution", height=520, autosize=True, hovermode='closest', showlegend=False)
    fig.data[0].customdata = customdata
    return fig


def build_owner_pie(rows, owner_color_map):
    """Aggregate total measured storage by CryoSPARC project owner."""
    sums = defaultdict(int)
    counts = defaultdict(set)
    job_counts = defaultdict(int)
    for r in rows:
        owner = r.get('owner', '') or 'unknown'
        sums[owner] += r.get('bytes', 0)
        counts[owner].add((r.get('tld',''), r.get('project','')))
        job_counts[owner] += 1

    items = sorted(sums.items(), key=lambda x: x[1], reverse=True)
    labels = [owner for owner, _ in items]
    values = [size for _, size in items]
    hover = [
        f"Owner: {html.escape(owner)}<br>Projects: {len(counts[owner])}"
        f"<br>Jobs: {job_counts[owner]}<br>Size: {human_readable_bytes(size)}"
        for owner, size in items
    ]
    colors = [owner_color_map.get(owner, None) for owner, _ in items]

    trace = go.Pie(
        labels=labels, values=values, hovertext=hover,
        marker=dict(colors=colors, line=dict(color='#ffffff', width=2)),
        textinfo='label+percent', textposition='auto', sort=False
    )
    fig = go.Figure(trace)
    fig.update_traces(hoverinfo='text', hovertemplate='%{hovertext}<extra></extra>')
    fig.update_layout(
        title="Storage by Owner", height=560, autosize=True,
        hovermode='closest', showlegend=True
    )
    return fig

def build_jobtype_pie(rows, colormap_map=None, type_top=10):
    """
    Aggregate bytes by job_type and return a Pie figure showing each type's share.
    Groups smaller types beyond top_n into an 'Other' slice.
    """
    sums = defaultdict(int)
    counts = defaultdict(int)
    for r in rows:
        jt = (r.get('job_type') or "unknown")
        sums[jt] += r.get('bytes', 0)
        counts[jt] += 1

    # sort by total bytes
    items = sorted(sums.items(), key=lambda x: x[1], reverse=True)
    top_items = items[:type_top]
    other_items = items[type_top:]

    labels, values, hover, colors = [], [], [], []
    customdata = []  # no paths, just for consistency

    for jt, size in top_items:
        labels.append(jt)
        values.append(size)
        hover.append(f"Job type: {html.escape(jt)}<br>Count: {counts.get(jt,0)}"
                     f"<br>Size: {human_readable_bytes(size)}")
        colors.append(None)  # will be filled later
        customdata.append("")

    if other_items:
        other_sum = sum(size for _, size in other_items)
        labels.append("Other")
        values.append(other_sum)
        hover.append(f"Other (combined {len(other_items)} types) — {human_readable_bytes(other_sum)}")
        colors.append('#dddddd')
        customdata.append("")

    # pick color palette
    try:
        from plotly.express import colors as pxcols
        palette = pxcols.qualitative.Plotly
    except Exception:
        palette = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
                   "#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf"]
    # fill colors for visible slices
    for i in range(min(len(top_items), len(palette))):
        colors[i] = palette[i % len(palette)]

    trace = go.Pie(
        labels=labels,
        values=values,
        hovertext=hover,
        marker=dict(colors=colors, line=dict(color='#ffffff', width=2)),
        textinfo='percent',
        textposition='outside',
        sort=False
    )
    fig = go.Figure(trace)
    fig.update_traces(hoverinfo='text', hovertemplate='%{hovertext}<extra></extra>')
    fig.update_layout(title=f"Storage by job_type — top {type_top} + Other",
                      height=520, autosize=True, hovermode='closest', showlegend=False)
    return fig


# -------- HTML builder: serialize figs and newPlot --------
def build_combined_html_serial(figs_with_ids, outpath, total_bytes):
    serialized = []
    for div_id, fig, title in figs_with_ids:
        fig_json = fig.to_plotly_json()
        serialized.append({
            "div_id": div_id,
            "title": title,
            "figure": fig_json
        })

    html_parts = []
    html_parts.append("<!doctype html><html><head><meta charset='utf-8'>")
    html_parts.append(f"<title>Storage report</title>")
    html_parts.append("""<style>
      body{font-family:Arial,Helvetica,sans-serif;margin:20px}
      nav{margin-bottom:10px;padding-bottom:6px;border-bottom:1px solid #ddd}
      nav a{margin-right:12px;text-decoration:none;color:#0366d6}
      section{margin-top:30px}
      .plot-container{width:100%;min-height:520px;margin-bottom:30px}
      .plot-container .plotly-graph-div{width:100% !important;height:100% !important;pointer-events:auto}
    </style>""")
    html_parts.append("</head><body>")
    html_parts.append(f"<h1>Storage report - {human_readable_bytes(total_bytes)}</h1>")
    html_parts.append("<nav>")
    for obj in serialized:
        html_parts.append(f"<a href='#{obj['div_id']}'>{html.escape(obj['title'])}</a>")
    html_parts.append("</nav>")

    for obj in serialized:
        html_parts.append(f"<section id='{obj['div_id']}'><h2>{html.escape(obj['title'])}</h2>")
        html_parts.append(f"<div class='plot-container'><div id='{obj['div_id']}_plot'></div></div></section>")

    html_parts.append("<script src='https://cdn.plot.ly/plotly-latest.min.js'></script>")
    html_parts.append("<script>")
    html_parts.append("const FIGS = " + json.dumps(serialized) + ";")

    js_boot = r"""
function makeToast(msg) {
  let t = document.getElementById('__global_toast');
  if(!t) {
    t = document.createElement('div'); t.id='__global_toast';
    t.style.position='fixed'; t.style.bottom='20px'; t.style.right='20px';
    t.style.padding='8px 12px'; t.style.background='rgba(0,0,0,0.8)';
    t.style.color='white'; t.style.borderRadius='4px'; t.style.fontFamily='sans-serif';
    document.body.appendChild(t);
  }
  t.textContent = msg; t.style.display='block';
  setTimeout(function(){ t.style.display='none'; }, 1500);
}
function fallbackCopy(text) {
  try { var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); makeToast('Copied path to clipboard'); }
  catch(e){ console.error('fallback copy failed', e); makeToast('Copy failed'); }
}
function extractCustomDataFromPoint(pt) {
  try {
    if(pt.customdata) return pt.customdata;
    if(pt.data && pt.pointIndex!==undefined && pt.data.customdata && pt.data.customdata[pt.pointIndex]) return pt.data.customdata[pt.pointIndex];
    if(pt.fullData && pt.pointIndex!==undefined && pt.fullData.customdata && pt.fullData.customdata[pt.pointIndex]) return pt.fullData.customdata[pt.pointIndex];
    if(pt.data && pt.pointNumber!==undefined && pt.data.customdata && pt.data.customdata[pt.pointNumber]) return pt.data.customdata[pt.pointNumber];
    if(pt.fullData && pt.pointNumber!==undefined && pt.fullData.customdata && pt.fullData.customdata[pt.pointNumber]) return pt.fullData.customdata[pt.pointNumber];
    if(pt.data && pt.data.customdata && pt.data.customdata.length) {
      var idx = (pt.pointIndex!==undefined)?pt.pointIndex:((pt.pointNumber!==undefined)?pt.pointNumber:0);
      var cd = pt.data.customdata[idx]; if(cd) return cd;
    }
  } catch(e) { console.warn('extract error', e); }
  return null;
}
function attachClickCopyToDiv(gd) {
  if(!gd || !gd.on) return;
  gd.on('plotly_click', function(eventData) {
    try {
      console.log('plotly_click fired on', gd.id, eventData);
      var pt = eventData.points[0];
      var val = extractCustomDataFromPoint(pt);
      if(!val) {
        if(pt.label) val = pt.label;
        else if(pt.x && typeof pt.x === 'string') val = pt.x;
      }
      if(!val) { makeToast('No path available to copy'); return; }
      if(navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(val).then(function(){ makeToast('Copied path to clipboard'); }, function(err){ console.warn('clipboard failed', err); fallbackCopy(val); });
      } else { fallbackCopy(val); }
    } catch(e) { console.error('click copy error', e); makeToast('Copy failed'); }
  });
}
function createPlots() {
  for(let i=0;i<FIGS.length;i++){
    let item = FIGS[i];
    let divId = item.div_id + "_plot";
    let fig = item.figure;
    try {
      Plotly.newPlot(divId, fig.data, fig.layout, {responsive:true});
      let gd = document.getElementById(divId);
      // attach copy handler
      attachClickCopyToDiv(gd);
    } catch(e) {
      console.error('Plot creation error for', divId, e);
    }
  }
}
document.addEventListener('DOMContentLoaded', function(){ setTimeout(createPlots, 50); });
"""
    html_parts.append(js_boot)
    html_parts.append("</script>")
    html_parts.append("</body></html>")

    with open(outpath, 'w', encoding='utf-8') as fh:
        fh.write("\n".join(html_parts))

# -------- main --------
def main():
    parser = argparse.ArgumentParser(description="Combined Plotly report with pies grouped into 'Other'")
    parser.add_argument('csvfile', help='CSV file (tld,project,owner,job,bytes,job_type,path)')
    parser.add_argument('--top', type=int, default=10, help='Top N for bars and labeled pie slices')
    parser.add_argument('--pie-top', type=int, default=20, help='Top M slices shown in pies before grouping rest into Other (default 20)')
    parser.add_argument('--type-top', type=int, default=10, help='Top K job types shown before grouping rest into Other (default 10)')
    parser.add_argument('--out', default='report.html', help='Output HTML file')
    parser.add_argument('--colormap', default=None, help='Colormap name or comma-separated CSS colors')
    parser.add_argument('--no-open', action='store_true', help='Do not auto-open HTML')
    parser.add_argument('--owner', action='append', default=[], metavar='OWNER',
                        help='Restrict report to this owner. Repeat --owner for multiple owners; comma-separated names are also accepted.')
    args = parser.parse_args()

    rows = read_rows(args.csvfile)
    if not rows:
        print("No rows in CSV."); return

    # Optional owner restriction. Matching is case-insensitive but otherwise exact.
    requested_owners = []
    for value in args.owner:
        requested_owners.extend(part.strip() for part in value.split(',') if part.strip())
    if requested_owners:
        wanted = {owner.casefold() for owner in requested_owners}
        available = sorted({(r.get('owner') or 'unknown') for r in rows}, key=str.casefold)
        rows = [r for r in rows if (r.get('owner') or 'unknown').casefold() in wanted]
        if not rows:
            parser.error(
                "No rows matched --owner %s. Available owners: %s"
                % (", ".join(requested_owners), ", ".join(available))
            )
        print(f"Owner filter: {', '.join(requested_owners)}")

    total_bytes = sum(r['bytes'] for r in rows)
    print(f"Loaded {len(rows)} rows. Total {human_readable_bytes(total_bytes)} ({total_bytes:,} bytes)")

    owner_color_map = assign_owner_colors(rows, args.colormap)

    jobs_bar = build_jobs_bar(rows, args.top, owner_color_map, total_bytes)
    projects_bar = build_projects_bar(rows, args.top, owner_color_map, total_bytes)
    jobs_pie = build_jobs_pie(rows, owner_color_map, args.pie_top)
    projects_pie = build_projects_pie(rows, owner_color_map, args.pie_top)
    owner_pie = build_owner_pie(rows, owner_color_map)
    jobtype_pie = build_jobtype_pie(rows, owner_color_map, args.type_top)



    top_rows = sorted(rows, key=lambda r: r['bytes'], reverse=True)[:args.top]
    top_sum = sum(r['bytes'] for r in top_rows)
    pct = (top_sum/total_bytes*100.0) if total_bytes>0 else 0.0
    hrs = human_readable_bytes(top_sum)

    figs = [
        ("jobs_bar", jobs_bar, f"Top {args.top} Jobs (bar)."),
        ("projects_bar", projects_bar, f"Top {args.top} Projects (bar)"),
        ("jobs_pie", jobs_pie, f"Jobs (top {args.pie_top} + Other)"),
        ("projects_pie", projects_pie, f"Projects (top {args.pie_top} + Other)"),
        ("owner_pie", owner_pie, "Storage by Owner"),
        ("jobtype_pie", jobtype_pie, "Job type contribution (pie)"),
    ]

    outdir = os.path.dirname(args.out) or '.'
    os.makedirs(outdir, exist_ok=True)
    build_combined_html_serial(figs, args.out, total_bytes)
    print("Wrote report to", args.out)

    if not args.no_open:
        firefox = shutil.which("firefox")
        try:
            if firefox:
                subprocess.Popen([firefox, args.out], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                webbrowser.open('file://' + os.path.abspath(args.out))
        except Exception as e:
            print("Could not auto-open:", e)

if __name__ == '__main__':
    main()
