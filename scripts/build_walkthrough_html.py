"""Render concept_walkthrough.md into a polished, self-contained HTML page.

Produces `concept_walkthrough.html`: a single file (inline CSS + JS, no external
dependencies) designed to be *read by a human* — sidebar navigation with
scroll-spy, a reading-progress bar, collapsible interview Q&A (reveal the answer
after you've tried it), and live search. It preserves all content from the
Markdown source.

    python scripts/build_walkthrough_html.py
"""

from __future__ import annotations

import html as _html
import re
from pathlib import Path

SRC = Path("concept_walkthrough.md")
OUT = Path("concept_walkthrough.html")

SUB_CLASS = {
    "What was built": "built",
    "Key ideas": "ideas",
    "Mistakes & fixes": "mistakes",
    "Interview Q&A": "qa",
}
SUB_ICON = {
    "What was built": "🛠",
    "Key ideas": "💡",
    "Mistakes & fixes": "🐞",
    "Interview Q&A": "🎤",
}


# --------------------------------------------------------------------------- #
# Inline + block rendering
# --------------------------------------------------------------------------- #
def inline(text: str) -> str:
    """Convert inline markdown (code, bold, italic, links) to HTML."""
    codes: list[str] = []

    def _stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00C{len(codes) - 1}\x00"

    text = re.sub(r"`([^`]+)`", _stash, text)  # protect code spans first
    text = _html.escape(text, quote=False)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        r'<a href="\2" target="_blank" rel="noopener">\1</a>',
        text,
    )
    text = re.sub(
        r"\x00C(\d+)\x00",
        lambda m: f"<code>{_html.escape(codes[int(m.group(1))], quote=False)}</code>",
        text,
    )
    return text


def split_blocks(lines: list[str]) -> list[list[str]]:
    """Split lines into blocks separated by blank lines."""
    blocks, cur = [], []
    for line in lines:
        if line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)
    return blocks


def render_prose(lines: list[str]) -> str:
    """Render a mix of wrapped paragraphs and bullet sub-lists."""
    out, para, bullets = [], [], []

    def flush_para() -> None:
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    def flush_bullets() -> None:
        if bullets:
            items = "".join(f"<li>{inline(b)}</li>" for b in bullets)
            out.append(f"<ul>{items}</ul>")
            bullets.clear()

    for line in lines:
        s = line.strip()
        if s.startswith(("* ", "- ")):
            flush_para()
            bullets.append(s[2:])
        else:
            flush_bullets()
            para.append(s)
    flush_para()
    flush_bullets()
    return "".join(out)


def render_generic(lines: list[str]) -> str:
    """Render paragraphs, flat lists, and blockquotes (used for the intro)."""
    out, para, bullets, quote = [], [], [], []

    def flush_para():
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para.clear()

    def flush_bullets():
        if bullets:
            items = "".join(f"<li>{inline(b)}</li>" for b in bullets)
            out.append(f"<ul>{items}</ul>")
            bullets.clear()

    def flush_quote():
        if quote:
            out.append(f"<blockquote>{inline(' '.join(quote))}</blockquote>")
            quote.clear()

    for line in lines:
        s = line.strip()
        if s.startswith("> "):
            flush_para()
            flush_bullets()
            quote.append(s[2:])
        elif s.startswith(("- ", "* ")):
            flush_para()
            flush_quote()
            bullets.append(s[2:])
        elif s == "":
            flush_para()
            flush_bullets()
            flush_quote()
        else:
            flush_bullets()
            flush_quote()
            para.append(s)
    flush_para()
    flush_bullets()
    flush_quote()
    return "".join(out)


def _join_wrapped(lines: list[str]) -> list[str]:
    """Merge wrapped continuation lines into their bullet's logical line."""
    out: list[str] = []
    for line in lines:
        s = line.lstrip(" ")
        if s.startswith(("- ", "* ")) or not out:
            out.append(line.rstrip())
        else:
            out[-1] = out[-1].rstrip() + " " + s.rstrip()
    return out


def render_list_tree(lines: list[str]) -> str:
    """Render a (possibly nested) bullet list, preserving indentation depth."""
    joined = _join_wrapped([line for line in lines if line.strip()])
    root: list = []
    stack = [(-1, root)]
    for line in joined:
        s = line.lstrip(" ")
        indent = len(line) - len(s)
        item = {"text": s[2:], "children": []}
        while stack[-1][0] >= indent:
            stack.pop()
        stack[-1][1].append(item)
        stack.append((indent, item["children"]))

    def render(items: list) -> str:
        parts = ["<ul>"]
        for it in items:
            child = render(it["children"]) if it["children"] else ""
            parts.append(f"<li>{inline(it['text'])}{child}</li>")
        parts.append("</ul>")
        return "".join(parts)

    return render(root)


def render_key_ideas(lines: list[str]) -> str:
    """Render numbered 'key idea' cards."""
    out = []
    for block in split_blocks(lines):
        m = re.match(r"\*\*(\d+)\.\s*(.*?)\*\*\s*$", block[0])
        if m:
            num, heading = m.group(1), m.group(2)
            body = render_prose(block[1:])
            out.append(
                f'<div class="idea"><span class="idea-num">{num}</span>'
                f'<div class="idea-body"><h4>{inline(heading)}</h4>{body}</div></div>'
            )
        else:
            out.append(f'<div class="idea-plain">{render_prose(block)}</div>')
    return "".join(out)


def render_qa(lines: list[str]) -> str:
    """Render Q&A as collapsible cards (answer hidden until expanded)."""
    out = []
    for block in split_blocks(lines):
        q = re.sub(r"^\*\*Q:\s*", "", block[0])
        q = re.sub(r"\*\*$", "", q)
        ans = block[1:]
        if ans:
            ans = list(ans)
            ans[0] = re.sub(r"^\s*A:\s*", "", ans[0])
        answer = render_prose(ans)
        out.append(
            '<details class="qa-item"><summary>'
            f'<span class="q-badge">Q</span><span>{inline(q)}</span>'
            f'</summary><div class="answer">{answer}</div></details>'
        )
    return "".join(out)


def render_subsection(name: str, lines: list[str]) -> str:
    """Dispatch a subsection to the right renderer."""
    if name == "Key ideas":
        body = render_key_ideas(lines)
    elif name == "Interview Q&A":
        body = render_qa(lines)
    elif name in ("What was built", "Mistakes & fixes"):
        body = render_list_tree(lines)
    else:
        body = render_generic(lines)
    cls = SUB_CLASS.get(name, "generic")
    icon = SUB_ICON.get(name, "•")
    return (
        f'<section class="sub {cls}">'
        f'<h3><span class="sub-icon">{icon}</span>{_html.escape(name)}</h3>'
        f"{body}</section>"
    )


# --------------------------------------------------------------------------- #
# Parse the document
# --------------------------------------------------------------------------- #
def parse(md: str):
    """Parse the markdown into (title, intro_lines, [chunks])."""
    title, intro, chunks = None, [], []
    cur = None
    sub = None
    for line in md.split("\n"):
        if title is None and line.startswith("# "):
            title = line[2:].strip()
        elif line.startswith("## "):
            cur = {"title": line[3:].strip(), "subs": []}
            chunks.append(cur)
            sub = None
        elif line.startswith("### ") and cur is not None:
            sub = {"name": line[4:].strip(), "lines": []}
            cur["subs"].append(sub)
        elif line.strip() == "---":
            continue
        else:
            if sub is not None:
                sub["lines"].append(line)
            elif cur is None:
                intro.append(line)
    return title, intro, chunks


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_html(md: str) -> str:
    title, intro, chunks = parse(md)
    title = title or "Concept Walkthrough"

    qa_total = sum(
        len(split_blocks(s["lines"]))
        for c in chunks
        for s in c["subs"]
        if s["name"] == "Interview Q&A"
    )

    nav, sections = [], []
    nav.append('<a class="nav-link" href="#overview">Overview</a>')
    for c in chunks:
        cid = slug(c["title"])
        m = re.match(r"Chunk\s+(\d+)\s*[—-]\s*(.*)", c["title"])
        num, short = (m.group(1), m.group(2)) if m else ("", c["title"])
        nav.append(
            f'<a class="nav-link" href="#{cid}">'
            f'<span class="nav-num">{num}</span>{_html.escape(short)}</a>'
        )
        subs_html = "".join(render_subsection(s["name"], s["lines"]) for s in c["subs"])
        sections.append(
            f'<article class="chunk" id="{cid}">'
            f'<header class="chunk-head"><span class="chunk-num">{num}</span>'
            f"<h2>{_html.escape(short)}</h2></header>{subs_html}</article>"
        )

    intro_html = render_generic(intro)

    return (
        _TEMPLATE.replace("__TITLE__", _html.escape(title))
        .replace("__NCHUNKS__", str(len(chunks)))
        .replace("__NQA__", str(qa_total))
        .replace("__NAV__", "\n".join(nav))
        .replace("__INTRO__", intro_html)
        .replace("__SECTIONS__", "\n".join(sections))
    )


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#0f1720; --panel:#16212e; --panel2:#1d2a3a; --border:#243447;
  --text:#e6edf3; --muted:#8aa0b2; --accent:#4cc9f0; --up:#3ddc97;
  --down:#ff6b6b; --amber:#f4a261; --radius:12px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);
  font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.65}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code{font-family:"SF Mono",ui-monospace,Menlo,Consolas,monospace;font-size:.86em;
  background:#0b1220;border:1px solid var(--border);border-radius:6px;
  padding:.08em .38em;color:#cfe8ff}
#progress{position:fixed;top:0;left:0;height:3px;width:0;background:var(--accent);
  z-index:100;transition:width .1s ease-out}
.layout{display:flex;max-width:1200px;margin:0 auto}
/* Sidebar */
aside{position:sticky;top:0;align-self:flex-start;height:100vh;width:270px;
  flex:0 0 270px;padding:26px 18px;overflow-y:auto;border-right:1px solid var(--border)}
aside .brand{font-weight:700;font-size:15px;letter-spacing:.02em;margin-bottom:2px}
aside .stat{color:var(--muted);font-size:12px;margin-bottom:16px}
.search{width:100%;padding:9px 11px;margin-bottom:14px;border-radius:9px;
  background:var(--panel);border:1px solid var(--border);color:var(--text);font-size:13px}
.search::placeholder{color:var(--muted)}
.nav-link{display:flex;gap:9px;align-items:center;padding:7px 10px;border-radius:8px;
  color:var(--muted);font-size:13.5px;margin-bottom:2px;border-left:2px solid transparent}
.nav-link:hover{background:var(--panel);color:var(--text);text-decoration:none}
.nav-link.active{background:var(--panel2);color:var(--text);border-left-color:var(--accent)}
.nav-num{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;
  border-radius:6px;background:var(--panel2);color:var(--accent);font-size:11px;font-weight:700;flex:0 0 auto}
.nav-link.active .nav-num{background:var(--accent);color:#04121b}
/* Main */
main{flex:1;min-width:0;padding:40px 48px 120px}
.page-title{font-size:32px;margin:0 0 6px}
.page-sub{color:var(--muted);margin:0 0 22px}
.toolbar{display:flex;gap:10px;margin:18px 0 30px;flex-wrap:wrap}
.btn{background:var(--panel);border:1px solid var(--border);color:var(--text);
  padding:7px 13px;border-radius:9px;font-size:12.5px;cursor:pointer}
.btn:hover{background:var(--panel2);border-color:var(--accent)}
blockquote{margin:14px 0;padding:10px 16px;border-left:3px solid var(--accent);
  background:var(--panel);border-radius:0 9px 9px 0;color:var(--muted)}
.intro ul{padding-left:20px}
.chunk{margin:0 0 46px;scroll-margin-top:20px}
.chunk-head{display:flex;align-items:center;gap:14px;margin:34px 0 18px;
  padding-bottom:12px;border-bottom:1px solid var(--border)}
.chunk-num{display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;
  border-radius:11px;background:linear-gradient(135deg,#1d2a3a,#26384c);
  color:var(--accent);font-size:20px;font-weight:800;flex:0 0 auto;border:1px solid var(--border)}
.chunk-head h2{margin:0;font-size:23px}
.sub{margin:0 0 14px;padding:16px 18px 6px;border-radius:var(--radius);
  background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--muted)}
.sub h3{margin:0 0 10px;font-size:15px;letter-spacing:.04em;text-transform:uppercase;
  display:flex;align-items:center;gap:9px;color:var(--muted)}
.sub-icon{font-size:15px}
.sub.built{border-left-color:var(--accent)}
.sub.ideas{border-left-color:var(--up)}
.sub.mistakes{border-left-color:var(--down)}
.sub.qa{border-left-color:var(--amber)}
.sub p{margin:.5em 0}
.sub ul{padding-left:22px;margin:.4em 0}
.sub li{margin:.28em 0}
.sub li ul{margin:.2em 0}
/* Key ideas */
.idea{display:flex;gap:13px;padding:11px 0;border-top:1px dashed var(--border)}
.idea:first-of-type{border-top:none}
.idea-num{display:inline-flex;align-items:center;justify-content:center;width:25px;height:25px;
  border-radius:50%;background:var(--panel2);color:var(--up);font-weight:700;font-size:13px;
  flex:0 0 auto;margin-top:2px}
.idea-body{min-width:0}
.idea-body h4{margin:.1em 0 .3em;font-size:15px;color:var(--text)}
.idea-body p{margin:.35em 0;color:#cdd9e3}
.idea-body ul{color:#cdd9e3}
.idea-plain{padding:8px 0;color:#cdd9e3}
/* Q&A */
.qa-item{border:1px solid var(--border);border-radius:10px;margin:9px 0;background:var(--panel2);
  overflow:hidden}
.qa-item summary{cursor:pointer;padding:12px 14px;font-weight:600;list-style:none;
  display:flex;gap:10px;align-items:flex-start;color:var(--text)}
.qa-item summary::-webkit-details-marker{display:none}
.qa-item summary:hover{background:#22344a}
.q-badge{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;
  border-radius:6px;background:var(--amber);color:#1a1205;font-weight:800;font-size:12px;flex:0 0 auto}
.qa-item[open] summary{border-bottom:1px solid var(--border)}
.qa-item .answer{padding:6px 14px 12px 46px;color:#cdd9e3}
.qa-item .answer p{margin:.5em 0}
.hidden{display:none !important}
#toTop{position:fixed;right:22px;bottom:22px;width:42px;height:42px;border-radius:50%;
  background:var(--accent);color:#04121b;border:none;font-size:20px;cursor:pointer;
  display:none;align-items:center;justify-content:center;box-shadow:0 4px 14px rgba(0,0,0,.4)}
#toTop.show{display:flex}
@media(max-width:860px){
  aside{display:none}
  main{padding:26px 18px 100px}
}
</style>
</head>
<body>
<div id="progress"></div>
<div class="layout">
  <aside>
    <div class="brand">📈 Concept Walkthrough</div>
    <div class="stat">__NCHUNKS__ chunks · __NQA__ interview Q&amp;As</div>
    <input class="search" id="search" type="search" placeholder="Search the walkthrough…">
    <nav id="nav">
      __NAV__
    </nav>
  </aside>
  <main>
    <h1 class="page-title">__TITLE__</h1>
    <p class="page-sub">Commodity Price Analytics Dashboard — how it was built, and why.</p>
    <section id="overview" class="intro">__INTRO__</section>
    <div class="toolbar">
      <button class="btn" id="expandAll">Expand all answers</button>
      <button class="btn" id="collapseAll">Collapse all answers</button>
    </div>
    __SECTIONS__
  </main>
</div>
<button id="toTop" title="Back to top">↑</button>
<script>
const docEl=document.documentElement;
const bar=document.getElementById('progress');
const toTop=document.getElementById('toTop');
function onScroll(){
  const h=docEl.scrollHeight-docEl.clientHeight;
  bar.style.width=(h>0?(docEl.scrollTop/h*100):0)+'%';
  toTop.classList.toggle('show',docEl.scrollTop>500);
}
document.addEventListener('scroll',onScroll,{passive:true});onScroll();
toTop.onclick=()=>window.scrollTo({top:0,behavior:'smooth'});

// Scroll-spy
const links=[...document.querySelectorAll('.nav-link')];
const map={};links.forEach(l=>map[l.getAttribute('href').slice(1)]=l);
const spy=new IntersectionObserver(es=>{
  es.forEach(e=>{if(e.isIntersecting){
    links.forEach(l=>l.classList.remove('active'));
    const a=map[e.target.id];if(a)a.classList.add('active');
  }});
},{rootMargin:'-15% 0px -75% 0px'});
document.querySelectorAll('article.chunk,#overview').forEach(s=>spy.observe(s));

// Expand / collapse all
document.getElementById('expandAll').onclick=()=>
  document.querySelectorAll('details.qa-item').forEach(d=>d.open=true);
document.getElementById('collapseAll').onclick=()=>
  document.querySelectorAll('details.qa-item').forEach(d=>d.open=false);

// Live search: hide chunks that don't match; auto-open matching Q&A
const search=document.getElementById('search');
search.addEventListener('input',()=>{
  const q=search.value.trim().toLowerCase();
  document.querySelectorAll('article.chunk').forEach(ch=>{
    const hit=!q||ch.textContent.toLowerCase().includes(q);
    ch.classList.toggle('hidden',!hit);
  });
  if(q){document.querySelectorAll('details.qa-item').forEach(d=>{
    d.open=d.textContent.toLowerCase().includes(q);});}
});
</script>
</body>
</html>
"""


def main() -> None:
    """Read the markdown and write the HTML page."""
    html_out = build_html(SRC.read_text())
    OUT.write_text(html_out)
    print(f"Wrote {OUT} ({len(html_out):,} bytes)")


if __name__ == "__main__":
    main()
