"""Developer tool: write the seller's "Publishing an Update" guide PDF (with pictures).

    python tools/make_update_guide.py     ->  ..\\Guides\\Fleet Manager Pro - Publishing an Update.pdf

Needs:  python -m pip install reportlab
"""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.platypus import (ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
VERSION = open(os.path.join(APP, "VERSION"), encoding="utf-8").read().strip()
OUT = os.path.join(os.path.dirname(APP), "Guides", "Fleet Manager Pro - Publishing an Update.pdf")
SUBTITLE = "Publishing an Update"
NAVY, NAVY2, CRIMSON, MUTED, PALE = (colors.HexColor(c) for c in ("#0a1a33", "#15325e", "#c8102e", "#4c6283", "#eef2f8"))

ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=ss["Heading1"], textColor=NAVY, fontSize=17, spaceBefore=14, spaceAfter=6)
H2 = ParagraphStyle("H2", parent=ss["Heading2"], textColor=CRIMSON, fontSize=12.5, spaceBefore=10, spaceAfter=4)
P = ParagraphStyle("P", parent=ss["BodyText"], fontSize=10.5, leading=15, textColor=colors.HexColor("#1b2a41"))
SMALL = ParagraphStyle("S", parent=P, fontSize=9, leading=12.5, textColor=MUTED)
CODE = ParagraphStyle("C", parent=P, fontName="Courier", fontSize=9, leading=12.5, backColor=PALE, borderPadding=6,
                      leftIndent=6, rightIndent=6, spaceBefore=4, spaceAfter=8)
NOTE = ParagraphStyle("N", parent=P, backColor=colors.HexColor("#fbe3e7"), borderColor=CRIMSON, borderWidth=0.8,
                      borderPadding=7, spaceBefore=14, spaceAfter=12, leftIndent=4, rightIndent=4)
TIP = ParagraphStyle("T", parent=NOTE, backColor=PALE, borderColor=NAVY2)


def steps(items):
    return ListFlowable([ListItem(Paragraph(t, P), leftIndent=18) for t in items], bulletType="1",
                        bulletFontName="Helvetica-Bold", bulletColor=CRIMSON, leftIndent=18)


def bullets(items):
    return ListFlowable([ListItem(Paragraph(t, P), leftIndent=14) for t in items], bulletType="bullet",
                        bulletColor=CRIMSON, leftIndent=14)


def table(rows, widths, head=True):
    t = Table([[Paragraph(c, P if i or not head else ParagraphStyle("th", parent=P, textColor=colors.white,
                                                                    fontName="Helvetica-Bold"))
                for c in r] for i, r in enumerate(rows)], colWidths=widths, repeatRows=1 if head else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d3dcea")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("ROWBACKGROUNDS", (0, 1 if head else 0), (-1, -1), [colors.white, PALE]),
          ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    if head:
        st.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    t.setStyle(TableStyle(st))
    return t


# ------------------------------------------------------------------ pictures (drawn, no external files)
def flow_diagram():
    W, H = 6.9 * inch, 1.35 * inch
    d = Drawing(W, H)
    boxes = [("1.  YOU", "Publish once to your", "licence server (one click)"),
             ("2.  BUYER'S APP", "Checks every day and shows", 'an “Update available” banner'),
             ("3.  BUYER", "Clicks Update now – it", "downloads and installs itself")]
    bw, bh = 2.05 * inch, 1.0 * inch
    gap = (W - 3 * bw) / 2
    y = (H - bh) / 2
    for i, (title, l1, l2) in enumerate(boxes):
        x = i * (bw + gap)
        d.add(Rect(x, y, bw, bh, rx=8, ry=8, fillColor=PALE, strokeColor=NAVY, strokeWidth=1))
        d.add(String(x + bw / 2, y + bh - 0.28 * inch, title, fontName="Helvetica-Bold", fontSize=10,
                     fillColor=CRIMSON, textAnchor="middle"))
        d.add(String(x + bw / 2, y + 0.42 * inch, l1, fontSize=8.5, fillColor=NAVY, textAnchor="middle"))
        d.add(String(x + bw / 2, y + 0.24 * inch, l2, fontSize=8.5, fillColor=NAVY, textAnchor="middle"))
        if i < 2:
            x1, x2, my = x + bw + 3, x + bw + gap - 3, y + bh / 2
            d.add(Line(x1, my, x2 - 4, my, strokeColor=CRIMSON, strokeWidth=2))
            d.add(Polygon([x2, my, x2 - 7, my + 4.5, x2 - 7, my - 4.5], fillColor=CRIMSON, strokeColor=CRIMSON))
    return d


def banner_mock():
    W, H = 6.9 * inch, 0.72 * inch
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, rx=8, ry=8, fillColor=colors.HexColor("#fdf1f3"), strokeColor=colors.HexColor("#f0c4cc"),
               strokeWidth=1))
    d.add(Rect(2, 4, 5, H - 8, fillColor=CRIMSON, strokeColor=CRIMSON))                 # accent bar
    ty = H / 2 - 3
    d.add(Polygon([0.24 * inch, ty + 9, 0.19 * inch, ty - 1, 0.29 * inch, ty - 1], fillColor=CRIMSON,
                  strokeColor=CRIMSON))                                                  # little up-arrow
    d.add(String(0.42 * inch, ty, "Fleet Manager Pro 1.4 is available (you have 1.3).", fontName="Helvetica-Bold",
                 fontSize=10.5, fillColor=NAVY))
    bw, bh = 1.15 * inch, 0.36 * inch
    bx, by = W - bw - 0.18 * inch, (H - bh) / 2
    d.add(Rect(bx, by, bw, bh, rx=6, ry=6, fillColor=CRIMSON, strokeColor=CRIMSON))
    d.add(String(bx + bw / 2, by + bh / 2 - 3.5, "Update now", fontName="Helvetica-Bold", fontSize=9.5,
                 fillColor=colors.white, textAnchor="middle"))
    return d


def card_mock():
    W, H = 3.5 * inch, 2.05 * inch
    d = Drawing(W, H)
    d.add(Rect(0, 0, W, H, rx=8, ry=8, fillColor=colors.white, strokeColor=colors.HexColor("#d3dcea"), strokeWidth=1))
    d.add(String(0.22 * inch, H - 0.36 * inch, "Updates", fontName="Helvetica-Bold", fontSize=12.5, fillColor=NAVY))
    bw = 1.0 * inch
    d.add(Rect(W - bw - 0.2 * inch, H - 0.42 * inch, bw, 0.26 * inch, rx=7, ry=7,
               fillColor=colors.HexColor("#fde7b4"), strokeColor=colors.HexColor("#dcb24a")))
    d.add(String(W - bw / 2 - 0.2 * inch, H - 0.34 * inch, "update ready", fontSize=8,
                 fillColor=colors.HexColor("#6b4e00"), textAnchor="middle"))
    rows = [("Installed", "Fleet Manager Pro 1.3", False), ("Available", "1.4   (35 MB)", True)]
    ry = H - 0.78 * inch
    for label, value, bold in rows:
        d.add(String(0.22 * inch, ry, label, fontSize=9.5, fillColor=MUTED))
        d.add(String(1.25 * inch, ry, value, fontName="Helvetica-Bold" if bold else "Helvetica", fontSize=9.5,
                     fillColor=NAVY))
        ry -= 0.26 * inch
    d.add(String(0.22 * inch, ry - 0.02 * inch, "Installs only when you click Update now; your data is untouched.",
                 fontSize=7.6, fillColor=MUTED))
    for i, (lbl, fill, txt, border) in enumerate([("Check for updates", PALE, NAVY, NAVY),
                                                  ("Update now", CRIMSON, colors.white, CRIMSON)]):
        bw2 = 1.45 * inch
        bx = 0.22 * inch + i * (bw2 + 0.14 * inch)
        d.add(Rect(bx, 0.22 * inch, bw2, 0.36 * inch, rx=6, ry=6, fillColor=fill, strokeColor=border))
        d.add(String(bx + bw2 / 2, 0.33 * inch, lbl, fontName="Helvetica-Bold", fontSize=8.5, fillColor=txt,
                     textAnchor="middle"))
    return d


# ------------------------------------------------------------------ page furniture (matches the other guides)
def page(canvas, doc):
    w, h = letter
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, h - 0.55 * inch, w, 0.55 * inch, stroke=0, fill=1)
    canvas.setFillColor(CRIMSON)
    canvas.rect(0, h - 0.6 * inch, w, 0.05 * inch, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 11)
    canvas.drawString(0.75 * inch, h - 0.36 * inch, "Fleet Manager Pro")
    canvas.setFont("Helvetica", 9)
    canvas.drawRightString(w - 0.75 * inch, h - 0.36 * inch, f"{SUBTITLE}  -  version {VERSION}")
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(w / 2, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def cover(canvas, doc):
    w, h = letter
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, w, h, stroke=0, fill=1)
    canvas.setFillColor(NAVY2)
    canvas.rect(0, h * 0.36, w, h * 0.3, stroke=0, fill=1)
    canvas.setFillColor(CRIMSON)
    canvas.rect(0, h * 0.36 - 6, w, 6, stroke=0, fill=1)
    cx, cy = w / 2, h * 0.78
    p = canvas.beginPath()
    p.moveTo(cx, cy + 52); p.lineTo(cx + 46, cy + 34); p.lineTo(cx + 46, cy + 2)
    p.curveTo(cx + 46, cy - 30, cx + 22, cy - 46, cx, cy - 54)
    p.curveTo(cx - 22, cy - 46, cx - 46, cy - 30, cx - 46, cy + 2); p.lineTo(cx - 46, cy + 34); p.close()
    canvas.setStrokeColor(CRIMSON); canvas.setLineWidth(5); canvas.setFillColor(NAVY2)
    canvas.drawPath(p, stroke=1, fill=1)
    canvas.setFillColor(colors.white)
    canvas.rect(cx - 30, cy - 14, 34, 22, stroke=0, fill=1)
    t = canvas.beginPath()
    t.moveTo(cx + 4, cy - 14); t.lineTo(cx + 4, cy + 4); t.lineTo(cx + 18, cy + 4); t.lineTo(cx + 30, cy - 6)
    t.lineTo(cx + 30, cy - 14); t.close()
    canvas.drawPath(t, stroke=0, fill=1)
    canvas.setFillColor(CRIMSON)
    canvas.circle(cx - 18, cy - 17, 6, stroke=0, fill=1); canvas.circle(cx + 18, cy - 17, 6, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 38)
    canvas.drawCentredString(w / 2, h * 0.55, "Fleet Manager Pro")
    canvas.setFont("Helvetica", 18)
    canvas.drawCentredString(w / 2, h * 0.49, SUBTITLE)
    canvas.setFillColor(colors.HexColor("#8fa8cc"))
    canvas.setFont("Helvetica", 12)
    canvas.drawCentredString(w / 2, h * 0.42, "How a new version reaches everyone who bought a copy")
    canvas.drawCentredString(w / 2, h * 0.2, f"Version {VERSION}")
    canvas.restoreState()


def build():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.95 * inch,
                            bottomMargin=0.8 * inch, title=f"Fleet Manager Pro - {SUBTITLE}", author="Fleet Manager Pro")
    s = [PageBreak()]
    a = s.append

    a(Paragraph("How updates reach your buyers", H1))
    a(Paragraph("Fleet Manager Pro updates itself. You publish a new version <b>once</b> to your own licence "
                "server, and every buyer's app downloads it from there by itself. After the first sale you never "
                "send a buyer a file again.", P))
    a(Spacer(1, 6))
    a(flow_diagram())
    a(Spacer(1, 6))
    a(Paragraph("Each buyer's copy has its pages sealed to that buyer, so a new version is still built for each "
                "buyer – but you never do that by hand. One tool rebuilds and publishes for everyone.", SMALL))

    a(Paragraph("The easy way: one command for everyone", H1))
    a(steps(["<b>Bump the version.</b> Open <font face='Courier'>Fleet Manager Pro\\VERSION</font> and raise it "
             "(for example <b>1.3</b> to <b>1.4</b>). The server only offers a version <i>higher</i> than what a "
             "buyer is running, so this is what makes an update “new”.",
             "<b>Make sure the licence server is running</b> (<font face='Courier'>license_server.py</font> on the "
             "licence-server PC, 192.168.1.29).",
             "<b>Double-click</b> <font face='Courier'>Build and Publish Update (All Buyers).bat</font> in the "
             "Development folder and type optional release notes."]))
    a(Paragraph("It rebuilds every buyer's sealed copy and publishes each one to your server. Each buyer then sees "
                "an <b>Update available</b> banner the next time their app checks (at start-up, or within a day).", P))
    a(Paragraph("From a terminal you can also run:", P))
    a(Paragraph('python "Build and Publish Update (All Buyers).py" --notes "Adds X, fixes Y"<br/>'
                'python "Build and Publish Update (All Buyers).py" --keys FMP-AAAA-... FMP-BBBB-...   (only these)<br/>'
                'python "Build and Publish Update (All Buyers).py" --dry-run   (just list who would be updated)', CODE))
    a(Paragraph("<b>Tip:</b> the list of buyers comes from your private ledger "
                "(<font face='Courier'>Buyer Builds (PRIVATE).csv</font>). Add <font face='Courier'>--stock</font> to "
                "also refresh a stock copy.", TIP))

    a(PageBreak())
    a(Paragraph("Where the update files live on your server", H1))
    a(Paragraph("Your licence server hands out updates from a folder called <font face='Courier'>updates</font> "
                "that sits <b>right next to <font face='Courier'>license_server.py</font></b> on the PC that runs "
                "the server. Inside it there is one folder per buyer, named by that copy's hidden build tag, plus an "
                "optional <font face='Courier'>STOCK</font> folder for stock copies:", P))
    a(Paragraph("Fleet Manager License Host\\<br/>"
                "&nbsp;&nbsp;license_server.py<br/>"
                "&nbsp;&nbsp;updates\\<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;FMPB-XXXX-XXXX-XXXX-XXXX\\&nbsp;&nbsp;&lt;- one folder per buyer<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Fleet Manager Pro 1.4.zip<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;STOCK\\&nbsp;&nbsp;&lt;- for stock copies<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Fleet Manager Pro 1.4.zip", CODE))
    a(Paragraph("The publish tools above <b>create and fill these folders for you</b> – you never build them by "
                "hand or rename the zip. You only need to know this folder exists because of <i>where</i> the tools "
                "put it.", P))
    a(Paragraph("Building on the same PC as the server? You're done.", H2))
    a(Paragraph("If you build and publish on the very PC that runs <font face='Courier'>license_server.py</font>, "
                "the zips drop straight into the right place and buyers can download them right away. Nothing else "
                "to do.", P))
    a(Paragraph("Building on a different PC from the server?", H2))
    a(Paragraph("The publish tools write to the <font face='Courier'>updates</font> folder <b>on whatever PC you "
                "run them on</b>. So if you build on one PC and the server runs on another (for example the server "
                "on 192.168.1.29), the new zips will not reach the server on their own. Do one of these:", P))
    a(bullets(["<b>Copy it across.</b> After publishing, copy the whole "
               "<font face='Courier'>Fleet Manager License Host\\updates</font> folder onto the server PC, into its "
               "own <font face='Courier'>Fleet Manager License Host</font> folder (next to "
               "<font face='Courier'>license_server.py</font>). Choose “replace the files in the destination” when "
               "Windows asks.",
               "<b>Run the publishing on the server PC</b> instead, if it has the project folder and Python.",
               "<b>Share one folder.</b> Point both PCs at a single shared "
               "<font face='Courier'>updates</font> folder (a mapped network drive or a UNC path)."]))
    a(Paragraph("<b>Check this first:</b> a buyer can only download an update when the server is running <i>and</i> "
                "<font face='Courier'>fleetmanager.servehttp.com:8750</font> still points to the server PC's current "
                "address. If you have changed networks or PCs, confirm that before you publish.", NOTE))

    a(PageBreak())
    a(Paragraph("What your buyer sees", H1))
    a(Paragraph("After you publish, a banner appears at the top of the buyer's app, and "
                "<b>Settings &gt; Licence &gt; Updates</b> shows the same with a <b>Check for updates</b> button. "
                "When they click <b>Update now</b>, the app downloads the new version from your server, checks its "
                "digital signature and checksum, then closes and reopens on the new version. Their company data is "
                "never touched.", P))
    a(Spacer(1, 8))
    a(Paragraph("The banner every page shows:", H2))
    a(banner_mock())
    a(Spacer(1, 12))
    a(Paragraph("The Settings &gt; Licence &gt; Updates panel:", H2))
    a(card_mock())

    a(PageBreak())
    a(Paragraph("Updating just one buyer", H1))
    a(Paragraph("If you only need to update a single buyer:", P))
    a(steps(["Bump <font face='Courier'>Fleet Manager Pro\\VERSION</font>.",
             "Build that buyer's copy with <font face='Courier'>Build Buyer Copy.bat</font> – enter their "
             "purchase key and choose <b>Single program (.exe)</b>.",
             "Publish it to your server:"]))
    a(Paragraph('python publish_update.py --zip "...\\Build Output\\For Buyer - FMP-XXXX-XXXX-XXXX-XXXX.zip" '
                '--key FMP-XXXX-XXXX-XXXX-XXXX', CODE))
    a(bullets(["<font face='Courier'>--stock</font> for a stock copy (no buyer).",
               "<font face='Courier'>--tag FMPB-...</font> to target a build tag from the build output.",
               "<font face='Courier'>--notes \"...\"</font> for release notes the buyer sees."]))
    a(Paragraph("<b>publish_update.py</b> lives in the <b>Fleet Manager License Host</b> folder, next to "
                "<font face='Courier'>license_server.py</font>.", SMALL))

    a(Paragraph("If something goes wrong", H1))
    a(table([["Problem", "What to check"],
             ["A buyer doesn't see the update",
              "Did you raise <font face='Courier'>VERSION</font> above what they run? Is "
              "<font face='Courier'>license_server.py</font> running? They can force a check with "
              "<b>Settings &gt; Updates &gt; Check for updates</b>, or by restarting the app."],
             ["“Update available” but Update now fails",
              "Their PC couldn't reach your server. Check the server is running and that "
              "<font face='Courier'>fleetmanager.servehttp.com:8750</font> is reachable from outside."],
             ["Where are the logs?",
              "The app writes to its <font face='Courier'>data\\logs</font> folder "
              "(<font face='Courier'>fleetmanager.log</font>, <font face='Courier'>errors.log</font>, "
              "<font face='Courier'>access.log</font>). The licence server writes "
              "<font face='Courier'>logs\\server.log</font> next to it. Ask the buyer to send "
              "<font face='Courier'>fleetmanager.log</font> and <font face='Courier'>errors.log</font>."]],
            [1.9 * inch, 4.9 * inch]))
    a(Paragraph("<b>Keep <font face='Courier'>host_key.json</font> private.</b> It signs both licences and "
                "updates – anyone who has it could issue either.", NOTE))

    doc.build(s, onFirstPage=cover, onLaterPages=page)
    print("Wrote", OUT)


if __name__ == "__main__":
    build()
