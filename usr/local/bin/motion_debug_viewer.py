#!/usr/bin/env python3

import json
import os
import posixpath
import urllib.parse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

DEBUG_ROOT = os.environ.get("MOTION_DEBUG_ROOT", "/var/lib/motion/debug_scoring")

GALLERY_HTML = r"""<!doctype html>
<html>
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Motion debug scoring (latest)</title>
    <style>
      :root { color-scheme: dark; }
      body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:#0b0f14; color:#e6edf3; }
      header { position: sticky; top: 0; background: rgba(11,15,20,0.9); backdrop-filter: blur(8px);
               border-bottom: 1px solid #223; padding: 12px 14px; display:flex; gap:12px; align-items:center; }
      header .pill { padding: 6px 10px; border: 1px solid #2a3; border-radius: 999px; font-size: 13px; color:#cfe; }
      header button { background:#16202b; color:#e6edf3; border:1px solid #334; border-radius:10px; padding:8px 10px; cursor:pointer; }
      header button:hover { background:#1b2735; }
      header input { width: 90px; background:#0f1720; color:#e6edf3; border:1px solid #334; border-radius:10px; padding:8px 10px; }
      header a { color:#8ab4ff; text-decoration:none; }
      #grid { padding: 12px; display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
      .card { background:#0f1720; border:1px solid #223; border-radius: 12px; overflow:hidden; }
      .card img { width:100%; height: 170px; object-fit: cover; display:block; cursor: zoom-in; }
      .card .meta { padding: 8px 10px; font-size: 12px; color:#b7c0cc; display:flex; justify-content:space-between; gap:8px; }
      .card .meta .name { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }

      /* Lightbox */
      #lightbox { position:fixed; inset:0; background: rgba(0,0,0,0.75); display:none; align-items:center; justify-content:center; padding: 18px; }
      #lightbox.open { display:flex; }
      #lightbox .panel { width:min(1200px, 100%); height:min(92vh, 100%); background:#0b0f14; border:1px solid #334; border-radius: 14px; overflow:hidden; display:flex; flex-direction:column; }
      #lightbox .bar { padding: 10px 12px; border-bottom:1px solid #223; display:flex; gap:10px; align-items:center; justify-content:space-between; }
      #lightbox .bar .left { display:flex; gap:10px; align-items:center; }
      #lightbox .bar .title { font-size: 13px; color:#c9d1d9; max-width: 55vw; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
      #lightbox .bar .hint { font-size: 12px; color:#95a1b3; }
      #lightbox .bar button { background:#16202b; color:#e6edf3; border:1px solid #334; border-radius:10px; padding:8px 10px; cursor:pointer; }
      #lightbox .imgwrap { flex:1; display:flex; align-items:center; justify-content:center; background:#000; }
      #lightbox .imgwrap img { max-width:100%; max-height:100%; object-fit:contain; }
      #status { padding: 10px 12px; font-size: 12px; color:#95a1b3; }
    </style>
  </head>
  <body>
    <header>
      <div class="pill">latest</div>
      <button id="reload">Reload</button>
      <label>Auto (s): <input id="auto" type="number" min="0" step="1" value="2" /></label>
      <div style="flex:1"></div>
      <a href="./api/latest" target="_blank">API</a>
    </header>
    <div id="status">Loading…</div>
    <div id="grid"></div>

    <div id="lightbox" role="dialog" aria-modal="true">
      <div class="panel">
        <div class="bar">
          <div class="left">
            <button id="prev">Prev</button>
            <button id="next">Next</button>
            <div class="title" id="lbTitle"></div>
          </div>
          <div class="hint">Esc to close • ←/→ to navigate • click outside to close</div>
          <button id="close">Close</button>
        </div>
        <div class="imgwrap"><img id="lbImg" alt="" /></div>
      </div>
    </div>

    <script>
      const grid = document.getElementById('grid');
      const statusEl = document.getElementById('status');
      const autoEl = document.getElementById('auto');

      const lb = document.getElementById('lightbox');
      const lbImg = document.getElementById('lbImg');
      const lbTitle = document.getElementById('lbTitle');

      let files = [];
      let openIdx = -1;
      let timer = null;

      function fmtTime(ts) {
        try { return new Date(ts * 1000).toLocaleTimeString(); } catch { return ''; }
      }

      function openAt(idx) {
        if (!files.length) return;
        openIdx = Math.max(0, Math.min(files.length - 1, idx));
        const f = files[openIdx];
        lbTitle.textContent = f.name;
        lbImg.src = './latest/' + encodeURIComponent(f.name) + '?t=' + Date.now();
        lb.classList.add('open');
      }

      function closeLb() {
        lb.classList.remove('open');
        lbImg.src = '';
        openIdx = -1;
      }

      async function load() {
        try {
          const r = await fetch('./api/latest', { cache: 'no-store' });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          const data = await r.json();
          files = data.files || [];
          statusEl.textContent = `Files: ${files.length}` + (data.updated_ts ? ` • Updated: ${fmtTime(data.updated_ts)}` : '') + (data.latest_dir ? ` • Dir: ${data.latest_dir}` : '');

          grid.innerHTML = '';
          for (let i = 0; i < files.length; i++) {
            const f = files[i];
            const card = document.createElement('div');
            card.className = 'card';

            const img = document.createElement('img');
            img.loading = 'lazy';
            img.src = './latest/' + encodeURIComponent(f.name);
            img.alt = f.name;
            img.addEventListener('click', () => openAt(i));

            const meta = document.createElement('div');
            meta.className = 'meta';
            meta.innerHTML = `<div class="name">${f.name}</div><div>${f.bytes ? Math.round(f.bytes/1024) + ' KB' : ''}</div>`;

            card.appendChild(img);
            card.appendChild(meta);
            grid.appendChild(card);
          }
        } catch (e) {
          statusEl.textContent = 'Failed to load: ' + e;
        }
      }

      function setAuto() {
        const s = Math.max(0, parseInt(autoEl.value || '0', 10));
        if (timer) clearInterval(timer);
        timer = null;
        if (s > 0) timer = setInterval(load, s * 1000);
      }

      document.getElementById('reload').addEventListener('click', load);
      autoEl.addEventListener('change', setAuto);

      document.getElementById('prev').addEventListener('click', () => openAt(openIdx - 1));
      document.getElementById('next').addEventListener('click', () => openAt(openIdx + 1));
      document.getElementById('close').addEventListener('click', closeLb);

      lb.addEventListener('click', (e) => { if (e.target === lb) closeLb(); });
      window.addEventListener('keydown', (e) => {
        if (!lb.classList.contains('open')) return;
        if (e.key === 'Escape') closeLb();
        if (e.key === 'ArrowLeft') openAt(openIdx - 1);
        if (e.key === 'ArrowRight') openAt(openIdx + 1);
      });

      setAuto();
      load();
    </script>
  </body>
</html>"""


def _safe_realpath(p: str) -> str:
    rp = os.path.realpath(p)
    rr = os.path.realpath(DEBUG_ROOT)
    if rp == rr or rp.startswith(rr + os.sep):
        return rp
    return rr


def list_latest_files():
    latest = os.path.join(DEBUG_ROOT, "latest")
    latest_real = _safe_realpath(latest)
    out = []
    try:
        for name in sorted(os.listdir(latest_real)):
            if not name.lower().endswith(".jpg"):
                continue
            full = os.path.join(latest_real, name)
            try:
                st = os.stat(full)
                out.append({"name": name, "bytes": int(st.st_size), "mtime": float(st.st_mtime)})
            except Exception:
                out.append({"name": name, "bytes": 0, "mtime": 0.0})
    except Exception:
        pass

    out.sort(key=lambda x: x.get("mtime", 0.0), reverse=True)
    return latest_real, out


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DEBUG_ROOT, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _serve_gallery_headers(self, body_len: int):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(body_len))
        self.end_headers()

    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            body = GALLERY_HTML.encode("utf-8")
            self._serve_gallery_headers(len(body))
            return
        if path == "/api/latest":
            latest_real, files = list_latest_files()
            payload = {
                "latest_dir": os.path.basename(latest_real),
                "updated_ts": __import__("time").time(),
                "files": files,
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        return super().do_HEAD()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            body = GALLERY_HTML.encode("utf-8")
            self._serve_gallery_headers(len(body))
            self.wfile.write(body)
            return

        if path == "/api/latest":
            latest_real, files = list_latest_files()
            payload = {
                "latest_dir": os.path.basename(latest_real),
                "updated_ts": __import__("time").time(),
                "files": files,
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        return super().do_GET()

    def translate_path(self, path: str) -> str:
        path = urllib.parse.urlparse(path).path
        path = posixpath.normpath(urllib.parse.unquote(path))
        return super().translate_path(path)


def main():
    os.makedirs(DEBUG_ROOT, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", 8765), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
