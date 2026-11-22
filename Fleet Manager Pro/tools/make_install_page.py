"""Developer tool: write the installation guide as one self-contained web page (no scripts, no outside files).

    python tools/make_install_page.py [output file]      ->  ..\\Guides\\install.html

The words come from make_install_guide.py (the PDF's source), so the page and the PDF always say the same thing:
its build() is run with stand-ins that write HTML instead of PDF parts.
Needs:  python -m pip install reportlab      (make_install_guide imports it)
"""
import html
import os
import re
import sys

import make_install_guide as guide

OUT = os.path.join(os.path.dirname(guide.APP), "Guides", "install.html")
KINDS = {"H1": "h1", "H2": "h2", "P": "p", "S": "small", "C": "code", "N": "note", "T": "tip", "th": "p"}
story, sections = [], []


def inline(t):
    """The guide's own markup is <b>, <i> and <br/>; everything else is text."""
    t = re.sub(r"&(?!#?\w+;)", "&amp;", t)
    t = re.sub(r"<(?!/?(?:b|i|br)\s*/?>)", "&lt;", t)
    t = t.replace("<br/>", "<br>")
    return re.sub(r"(http://fleetmanager\.servehttp\.com:8750/apply/\w+|https?://(?:127\.0\.0\.1|192\.168\.1\.20):8760)",
                  r"<code>\1</code>", t)


class Paragraph:
    def __init__(self, text, style):
        self.text, self.kind = text, KINDS[style.name]

    def html(self):
        t = inline(self.text)
        if self.kind == "h1":
            plain = html.unescape(re.sub(r"<[^>]+>", "", t))
            sid = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")
            sections.append((sid, plain))
            return f'<h2 id="{sid}">{t}</h2>'
        if self.kind == "h2":
            return f"<h3>{t}</h3>"
        if self.kind == "code":
            return f"<pre><code>{t}</code></pre>"
        return f"<p>{t}</p>" if self.kind == "p" else f'<p class="{self.kind}">{t}</p>'


class ListItem:
    def __init__(self, para, **_kw):
        self.para = para


class ListFlowable:
    def __init__(self, items, bulletType="1", **_kw):
        self.items, self.tag = items, "ol" if bulletType == "1" else "ul"

    def html(self):
        return f"<{self.tag}>" + "".join(f"<li>{inline(i.para.text)}</li>" for i in self.items) + f"</{self.tag}>"


class Table:
    def __init__(self, rows, **_kw):
        self.rows = rows

    def setStyle(self, _style):
        pass

    def html(self):
        head, body = self.rows[0], self.rows[1:]
        names = [html.escape(html.unescape(re.sub(r"<[^>]+>", "", c.text)), quote=True) for c in head]
        out = "<thead><tr>" + "".join(f'<th scope="col">{inline(c.text)}</th>' for c in head) + "</tr></thead><tbody>"
        for r in body:
            out += "<tr>" + "".join(f'<td data-label="{n}">{inline(c.text)}</td>' for n, c in zip(names, r)) + "</tr>"
        return f"<table>{out}</tbody></table>"


class KeepTogether:
    def __init__(self, parts):
        self.parts = parts

    def html(self):
        return "\n".join(p.html() for p in self.parts)


class Nothing:
    def __init__(self, *_a, **_kw):
        pass

    def html(self):
        return ""


class Collector:
    def __init__(self, *_a, **_kw):
        pass

    def build(self, parts, **_kw):
        story.extend(parts)


CSS = """
:root{--bg:#f4f7fb;--card:#fff;--ink:#1b2a41;--muted:#4c6283;--line:#d3dcea;--pale:#eef2f8;--navy:#0a1a33;--accent:#c8102e;--note:#fbe3e7}
@media (prefers-color-scheme:dark){:root{--bg:#071327;--card:#0d203d;--ink:#e8eef8;--muted:#93a7c6;--line:#1d3a66;--pale:#12294c;--accent:#f0556d;--note:#3a1424}}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:16px}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 "Segoe UI",system-ui,sans-serif}
header{background:linear-gradient(135deg,#050e1c,#15325e);border-bottom:5px solid #c8102e;color:#fff;padding:48px 20px;text-align:center}
header h1{margin:0;font-size:clamp(28px,5vw,42px);line-height:1.15}header p{margin:8px auto 0;color:#b9cbe6;max-width:60ch}
.wrap{max-width:1120px;margin:0 auto;padding:28px 20px;display:grid;grid-template-columns:230px minmax(0,1fr);gap:28px;align-items:start}
nav{position:sticky;top:16px;font-size:14.5px}nav b{display:block;margin-bottom:6px}
nav a{display:block;padding:5px 10px;color:var(--ink);text-decoration:none;border-left:3px solid var(--line)}
nav a:hover,nav a:focus-visible{border-color:var(--accent);color:var(--accent)}
main{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:8px 30px 30px;min-width:0}
h2{font-size:24px;margin:30px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}h3{font-size:18px;margin:22px 0 6px;color:var(--accent)}
li{margin:7px 0}li::marker{color:var(--accent);font-weight:700}.small{color:var(--muted);font-size:14px}
.note,.tip{border-left:4px solid var(--accent);background:var(--note);padding:12px 16px;border-radius:8px}.tip{border-color:#3d6bb0;background:var(--pale)}
code{font-family:Consolas,monospace;font-size:.92em;overflow-wrap:anywhere}pre{background:var(--pale);border:1px solid var(--line);border-radius:8px;padding:12px 14px;overflow-x:auto;white-space:pre}
pre code{overflow-wrap:normal}table{width:100%;border-collapse:collapse;margin:12px 0}
th{background:#0a1a33;color:#fff;text-align:left}th,td{padding:9px 12px;border:1px solid var(--line);vertical-align:top}tbody tr:nth-child(even){background:var(--pale)}
td{overflow-wrap:anywhere}td:first-child{font-weight:600;width:28%}
@media (max-width:760px){.wrap{grid-template-columns:minmax(0,1fr);padding:16px}nav{position:static}main{padding:4px 16px 20px}
thead{position:absolute;left:-9999px}table,tbody,tr,td{display:block;width:100%}td:first-child{width:100%;border-bottom:0}tr{margin-bottom:10px}}
@media print{nav{display:none}.wrap{display:block;padding:0}main{border:0}h2,h3{break-after:avoid}}
"""


def build(out=OUT):
    for name, cls in (("Paragraph", Paragraph), ("ListItem", ListItem), ("ListFlowable", ListFlowable), ("Table", Table),
                      ("KeepTogether", KeepTogether), ("Spacer", Nothing), ("PageBreak", Nothing),
                      ("SimpleDocTemplate", Collector)):
        setattr(guide, name, cls)
    guide.OUT = __file__                 # build() only reports this file's size; the PDF itself is not written
    guide.print = lambda *_a: None
    story.clear()
    sections.clear()
    guide.build()
    body = "\n".join(h for h in (p.html() for p in story) if h)
    toc = "".join(f'<a href="#{sid}">{html.escape(t)}</a>' for sid, t in sections)
    version = html.escape(guide.VERSION)
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Installation Guide - Fleet Manager Pro</title>
<meta name="description" content="How to install, activate and set up Fleet Manager Pro for American Truck Simulator and Euro Truck Simulator 2.">
<style>{CSS}</style>
</head>
<body>
<header><h1>Fleet Manager Pro</h1><p>Installation Guide &middot; version {version}</p>
<p>Virtual trucking company manager for American Truck Simulator and Euro Truck Simulator 2</p></header>
<div class="wrap">
<nav aria-label="Sections"><b>On this page</b>{toc}</nav>
<main>
{body}
</main>
</div>
</body>
</html>
"""
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    print("written:", out, f"({len(page) // 1024} KB, {len(sections)} sections)")


if __name__ == "__main__":
    build(*sys.argv[1:2])
