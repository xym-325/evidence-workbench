"""本地交互网页: 纯标准库 http.server + 内嵌单页前端。

只做本地词表查询、组配与检索式拼装, 不接 PubMed API, 不联网。
    python -m mesh_query.web --port 8765
    python -m mesh_query --serve
"""

from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, pipeline
from .compose import ComposeOptions, parse_subhead_arg
from .lexicon import Lexicon


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    db: str | None = None
    record: bool = True
    protocol_version = "HTTP/1.1"

    # ---- 基础响应 ----
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, payload: object) -> None:
        self._send(code, _json_bytes(payload), "application/json; charset=utf-8")

    def log_message(self, fmt: str, *args) -> None:  # 静默, 免得刷屏
        pass

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/api/meta":
            try:
                with Lexicon(self.db) as lex:
                    quals = [
                        {"abbr": r["abbr"], "name": r["name"]}
                        for r in lex.conn.execute("SELECT ui, name, abbr FROM quals ORDER BY name")
                    ]
                    self._send_json(
                        200,
                        {
                            "version": __version__,
                            "mesh_year": lex.meta.get("mesh_year"),
                            "index": {
                                "descriptor_count": lex.meta.get("descriptor_count"),
                                "term_count": lex.meta.get("term_count"),
                                "built_at": lex.meta.get("built_at"),
                            },
                            "quals": quals,
                        },
                    )
            except FileNotFoundError as exc:
                self._send_json(404, {"error": str(exc)})
            return
        self._send_json(404, {"error": f"未知路径: {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path != "/api/analyze":
            self._send_json(404, {"error": f"未知路径: {path}"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "请求体不是合法 JSON"})
            return

        try:
            text = str(payload.get("text") or "").strip()
            if not text:
                raise ValueError("输入为空: 请给一个医学概念或一句临床问题")
            options = _options_from_payload(payload)
            top = int(payload.get("top") or 3)
            with Lexicon(self.db) as lex:
                result = pipeline.analyze(text, lex, top=max(1, min(top, 10)), compose_options=options)
                if payload.get("record", True) and self.record:
                    pipeline.save_record(result)
            self._send_json(200, result)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except FileNotFoundError as exc:
            self._send_json(404, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})


def _options_from_payload(payload: dict) -> ComposeOptions:
    spec = str(payload.get("subhead") or "").strip()
    all_specs, by_index = parse_subhead_arg(spec) if spec else ([], {})
    return ComposeOptions(
        subhead_all=all_specs,
        subhead_by_index=by_index,
        auto_subhead=bool(payload.get("auto_subhead")),
        noexp=bool(payload.get("noexp")),
        major=bool(payload.get("major")),
        free_syn=max(0, int(payload.get("free_syn") or 0)),
        ec=bool(payload.get("ec")),
    )


def serve(port: int = 8765, db: str | None = None, open_browser: bool = True) -> int:
    Handler.db = db
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"[mesh-query] 本地网页已启动: {url}")
    print("[mesh-query] 仅本地词表查询, 不接 PubMed API; Ctrl+C 结束")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mesh-query] 已停止")
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mesh_query.web", description="本地交互网页")
    parser.add_argument("--port", type=int, default=8765, help="端口 (默认 8765)")
    parser.add_argument("--db", help="词表索引路径 (默认 data/mesh.db)")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args(argv)
    return serve(port=args.port, db=args.db, open_browser=not args.no_open)


# 注意: 模块入口必须放在 PAGE 之后再执行, 否则 serve_forever() 阻塞时 PAGE 尚未定义。


# ---------------------------------------------------------------- 前端页面
PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>mesh-query · 本地 MeSH 检索式生成器</title>
<style>
:root{
  --bg:#f5f7fa; --card:#ffffff; --line:#e2e8f0; --ink:#1a2233; --muted:#64748b;
  --blue:#2563eb; --blue-bg:#eff6ff; --green:#15803d; --green-bg:#f0fdf4;
  --amber:#b45309; --amber-bg:#fffbeb; --gray:#475569; --gray-bg:#f1f5f9;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.65 "Microsoft YaHei","Segoe UI",system-ui,sans-serif}
.wrap{max-width:940px;margin:0 auto;padding:24px 16px 64px}
header h1{margin:0 0 4px;font-size:22px}
header p{margin:0;color:var(--muted);font-size:13px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;
  background:var(--gray-bg);color:var(--gray);margin-left:6px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;margin-top:16px}
label{font-size:13px;color:var(--muted);display:block;margin-bottom:6px}
textarea{width:100%;min-height:76px;padding:10px 12px;font:inherit;color:var(--ink);
  border:1px solid var(--line);border-radius:8px;resize:vertical;background:#fcfdff}
textarea:focus,input:focus{outline:2px solid var(--blue);outline-offset:1px;border-color:var(--blue)}
.examples{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px}
.examples button{font-size:12px;padding:4px 10px;border:1px solid var(--line);
  background:#fff;border-radius:999px;cursor:pointer;color:var(--muted)}
.examples button:hover{border-color:var(--blue);color:var(--blue)}
.opts{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:12px 18px}
.opt{font-size:14px}
.opt .hint{font-size:12px;color:var(--muted);margin-top:3px}
.opt input[type=checkbox]{margin-right:6px;vertical-align:-2px}
.opt input[type=text],.opt input[type=number]{width:100%;padding:6px 8px;
  border:1px solid var(--line);border-radius:6px;font:inherit}
.row{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:16px}
button.go{background:var(--blue);color:#fff;border:0;border-radius:8px;
  padding:9px 26px;font-size:15px;cursor:pointer}
button.go:hover{filter:brightness(1.08)}
button.go[disabled]{opacity:.6;cursor:wait}
.err{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;border-radius:8px;
  padding:10px 12px;margin-top:14px;display:none;white-space:pre-wrap}
.querybox{background:#0f172a;color:#e2e8f0;border-radius:8px;padding:14px 16px;
  font:14px/1.7 Consolas,"Courier New",monospace;white-space:pre-wrap;word-break:break-word}
.qhead{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:8px}
.qhead b{font-size:15px}
.copy{background:#1e293b;color:#e2e8f0;border:1px solid #334155;border-radius:6px;
  padding:5px 14px;font-size:13px;cursor:pointer}
.copy:hover{background:#334155}
.chips{margin-top:10px;display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12px;background:var(--blue-bg);color:var(--blue);
  border-radius:6px;padding:3px 9px}
.note{font-size:12.5px;color:var(--muted);margin-top:10px}
.card h3{margin:0;font-size:15px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.tag{font-size:11.5px;padding:1px 8px;border-radius:999px}
.tag.ok{background:var(--green-bg);color:var(--green)}
.tag.pending{background:var(--amber-bg);color:var(--amber)}
.kv{font-size:13.5px;margin-top:6px;color:#334155}
.kv .k{color:var(--muted);display:inline-block;min-width:52px}
.cand{background:var(--gray-bg);border-radius:8px;padding:9px 12px;margin-top:9px;font-size:13.5px}
.cand .why{color:var(--muted);font-size:12.5px;margin-top:3px}
details{margin-top:5px}
summary{cursor:pointer;font-size:12.5px;color:var(--blue)}
details div{color:#334155;font-size:12.5px;margin-top:4px}
.comp{margin-top:9px;font-size:13px}
.comp .line{padding:2px 0}
.comp .applied{color:var(--green)}
.comp .skip{color:var(--amber)}
.comp .ec{color:var(--blue)}
.comp .free{color:var(--gray)}
.muted{color:var(--muted)}
.empty{color:var(--muted);font-size:14px;display:none}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>mesh-query <span class="badge" id="meta">词表载入中…</span></h1>
    <p>自然语言医学问题 &rarr; PubMed 可粘贴 MeSH 检索式。本地词表查询 + 组配, 不接 PubMed API。</p>
  </header>

  <div class="card">
    <label for="text">输入一个医学概念, 或一句临床问题</label>
    <textarea id="text" placeholder="例: Does metformin reduce mortality in type 2 diabetes?"></textarea>
    <div class="examples">
      <button data-x="heart failure">heart failure</button>
      <button data-x="Does metformin reduce mortality in type 2 diabetes?">metformin / mortality 问题</button>
      <button data-x="metformin adverse effects">metformin adverse effects</button>
      <button data-x="高血压 和 2型糖尿病">中文: 高血压 和 2型糖尿病</button>
    </div>
  </div>

  <div class="card">
    <div class="opts">
      <div class="opt">
        <label>副主题词 (手动)</label>
        <input type="text" id="subhead" list="quallist" placeholder="留空 / TU,AE / 1:AE,2:TU">
        <div class="datalist-note hint">只允许该词在词表里真实可用的副主题词, 不可用即跳过</div>
      </div>
      <div class="opt">
        <label><input type="checkbox" id="auto"> 自动配副主题词(按意图词)</label>
        <div class="hint">识别 治疗/诊断/不良反应/死亡率… 后自动组配</div>
      </div>
      <div class="opt">
        <label><input type="checkbox" id="noexp"> 不爆炸下位词 (:noexp)</label>
        <div class="hint">关掉 MeSH 自动向下扩检</div>
      </div>
      <div class="opt">
        <label><input type="checkbox" id="major"> 仅主要主题 ([majr])</label>
        <div class="hint">只要把该词列为主要主题的文献</div>
      </div>
      <div class="opt">
        <label><input type="checkbox" id="ec"> 采用 Entry Combination</label>
        <div class="hint">按词表"组配改写"建议替换词形(默认仅提示)</div>
      </div>
      <div class="opt">
        <label>自由词同义词个数</label>
        <input type="number" id="freesyn" min="0" max="5" value="0">
        <div class="hint">每个概念往 [Title/Abstract] 追加 N 个入口词同义词</div>
      </div>
      <div class="opt">
        <label>候选词数量</label>
        <input type="number" id="top" min="1" max="10" value="3">
        <div class="hint">每个片段展示几个候选 MeSH 词</div>
      </div>
    </div>
    <div class="row">
      <button class="go" id="go">生成检索式</button>
      <span class="muted" style="font-size:12.5px" id="state"></span>
    </div>
    <div class="err" id="err"></div>
    <datalist id="quallist"></datalist>
  </div>

  <div class="empty" id="empty">结果会显示在这里。</div>

  <div class="card" id="result" style="display:none">
    <div class="qhead">
      <b>检索式 (可直接粘贴 PubMed)</b>
      <button class="copy" id="copy">复制</button>
    </div>
    <div class="querybox" id="query"></div>
    <div class="chips" id="overview"></div>
    <div class="note" id="tagline"></div>
    <div class="note" id="record"></div>
  </div>

  <div id="chunks"></div>
</div>

<script>
const $ = id => document.getElementById(id);

async function loadMeta(){
  try{
    const r = await fetch('/api/meta');
    if(!r.ok) throw new Error((await r.json()).error || r.status);
    const m = await r.json();
    $('meta').textContent = 'MeSH ' + (m.mesh_year || '?') + ' · 描述词 ' +
      Number(m.index.descriptor_count||0).toLocaleString() + ' · 术语 ' +
      Number(m.index.term_count||0).toLocaleString();
    $('quallist').innerHTML = (m.quals||[]).map(q =>
      `<option value="${q.abbr || ''}">${q.name}</option>`).join('');
  }catch(e){ $('meta').textContent = '词表载入失败: ' + e.message; }
}

function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function candCard(c){
  const d = c.detail || {};
  const trees = d.tree_numbers || [];
  const chain = (d.broader_chain||[]).map(x => x.name + ' (' + x.tree_number + ')').join(' &gt; ');
  const quals = (d.qualifiers||[]).map(q => '/' + q.name + ' (' + q.abbr + ')').join(', ');
  const lines = [];
  if(trees.length) lines.push('<div><b>树号</b>: ' + esc(trees.join('; ')) + '</div>');
  if(chain) lines.push('<div><b>上位</b>: ' + chain + (d.broader_total ? ' … 共 ' + d.broader_total + ' 个' : '') + '</div>');
  if(d.narrower_total) lines.push('<div><b>下位</b>: 共 ' + d.narrower_total + ' 个, 例: ' + esc((d.narrower_examples||[]).join(', ')) + '</div>');
  if(quals) lines.push('<div><b>可用副主题词</b>: ' + esc(quals) + '</div>');
  if(d.scope_note) lines.push('<div><b>作用域</b>: ' + esc(d.scope_note) + '</div>');
  return `<div class="cand">
    <div><b>${esc(c.name)}</b> <span class="muted">(${esc(c.ui)})</span>
      <span class="tag ${c.status === '确定' ? 'ok' : 'pending'}">${esc(c.status)}</span>
      <span class="muted">${esc(c.kind_label || '')}</span></div>
    <div class="why">依据: ${esc(c.basis_label || '')} (词表原词: "${esc(c.matched_term || '')}")</div>
    ${lines.length ? '<details><summary>层级 / 副主题词 / 作用域</summary>' + lines.join('') + '</details>' : ''}
  </div>`;
}

function compLines(comp){
  const out = [];
  if(comp.note) return [`<div class="line muted">组配: ${esc(comp.note)}</div>`];
  (comp.applied||[]).forEach(a => out.push(
    `<div class="line applied">组配: /${esc(a.name)} (${esc(a.abbr)}) [来源: ${esc(a.source)}]</div>`));
  (comp.skipped||[]).forEach(s => out.push(
    `<div class="line skip">跳过: /${esc(s.name || s.spec)} — ${esc(s.reason)}</div>`));
  (comp.ec||[]).forEach(h => {
    const target = h.out_desc_name + (h.out_qual_name ? '/' + h.out_qual_name : '');
    const state = h.applied ? '已采用' : '仅提示, 勾选后采用';
    out.push(`<div class="line ec">组配提示: "${esc(h.in_desc_name)}/${esc(h.in_qual)}" 词表建议改用 "${esc(target)}" (${state})</div>`);
  });
  if((comp.free_terms||[]).length) out.push(
    `<div class="line free">自由词: +${comp.free_terms.length} 个同义词 (${esc(comp.free_terms.join(', '))})</div>`);
  return out;
}

function overview(result){
  const co = result.compose || {};
  const opts = co.options || {};
  const chips = ['字段 ' + (co.tag || '[MeSH Terms]')];
  (co.intents||[]).forEach(i => chips.push('意图词 ' + i.keyword));
  chips.push('自由词同义词 ' + (opts.free_syn || 0));
  chips.push('Entry Combination ' + (opts.ec ? '采用' : '仅提示'));
  const applied = (co.per_chunk||[]).reduce((n,c) => n + (c.applied||[]).length, 0);
  chips.push('已配副主题词 ' + applied + ' 个');
  $('overview').innerHTML = chips.map(c => `<span class="chip">${esc(c)}</span>`).join('');

  let tagline = (co.tag || '[MeSH Terms]') + ' = MeSH 主题词; [Title/Abstract] = 自然语言自由词';
  if(opts.noexp || opts.major){
    const extra = [];
    if(opts.noexp) extra.push(':noexp 不爆炸下位词');
    if(opts.major) extra.push('[majr] 仅主要主题');
    tagline += ' · ' + extra.join(' · ');
  }
  $('tagline').textContent = tagline + ' · 副主题词只来自词表真实可用集合, 不可用即跳过';
}

function render(result){
  $('empty').style.display = 'none';
  $('result').style.display = '';
  $('query').textContent = result.query || '(未生成)';
  overview(result);

  const pending = (result.chunks||[]).filter(c => c.status !== 'matched').map(c => c.text);
  $('record').textContent =
    '输入类型: ' + (result.input_type || '') +
    (result.skipped && result.skipped.length ? ' · 跳过结构词: ' + result.skipped.join(', ') : '') +
    (pending.length ? ' · 待核片段: ' + pending.join(' | ') : '') +
    ' · 记录已写入 records/mappings.jsonl';

  const per = ((result.compose || {}).per_chunk) || [];
  $('chunks').innerHTML = (result.chunks||[]).map((c,i) => {
    const comp = per[i] || {};
    const tag = c.status === 'matched'
      ? '<span class="tag ok">已匹配</span>' : '<span class="tag pending">待核</span>';
    const cands = (c.candidates||[]).map(candCard).join('') ||
      '<div class="cand muted">本地词表未命中, 建议改用更规范的英文术语</div>';
    const metaBits = [];
    if(c.mapped_en) metaBits.push(`<div class="kv"><span class="k">中文</span>提示词 &rarr; ${esc(c.mapped_en)}</div>`);
    if(c.note) metaBits.push(`<div class="kv"><span class="k">切分</span>${esc(c.note)}</div>`);
    return `<div class="card">
      <h3>[${i+1}/${result.chunks.length}] "${esc(c.text)}" ${tag}</h3>
      ${metaBits.join('')}
      ${cands}
      <div class="comp">${compLines(comp).join('') || '<div class="line muted">组配: 未做组配</div>'}</div>
    </div>`;
  }).join('');
  window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
}

async function run(){
  const text = $('text').value.trim();
  const errBox = $('err');
  errBox.style.display = 'none';
  if(!text){ errBox.textContent = '输入为空: 请给一个医学概念或一句临床问题';
    errBox.style.display = 'block'; return; }
  const btn = $('go');
  btn.disabled = true; $('state').textContent = '查询本地词表中…';
  try{
    const r = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        text,
        top: Number($('top').value) || 3,
        subhead: $('subhead').value.trim(),
        auto_subhead: $('auto').checked,
        noexp: $('noexp').checked,
        major: $('major').checked,
        free_syn: Number($('freesyn').value) || 0,
        ec: $('ec').checked
      })
    });
    const data = await r.json();
    if(!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    render(data);
    $('state').textContent = '完成 ' + new Date().toLocaleTimeString();
  }catch(e){
    errBox.textContent = e.message; errBox.style.display = 'block';
    $('state').textContent = '';
  }finally{ btn.disabled = false; }
}

$('go').addEventListener('click', run);
$('text').addEventListener('keydown', e => {
  if(e.key === 'Enter' && (e.ctrlKey || e.metaKey)) run();
});
document.querySelectorAll('.examples button').forEach(b =>
  b.addEventListener('click', () => { $('text').value = b.dataset.x; run(); }));
$('copy').addEventListener('click', async () => {
  const text = $('query').textContent;
  try{ await navigator.clipboard.writeText(text); }
  catch(e){
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  $('copy').textContent = '已复制';
  setTimeout(() => $('copy').textContent = '复制', 1500);
});
loadMeta();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
