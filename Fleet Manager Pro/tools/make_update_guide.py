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
    boxes = [("1.  YOU", "Ship Update.bat, then upload", "one zip to the server"),
             ("2.  EVERY COPY", "Checks every day and shows", 'an “Update available” banner'),
             ("3.  THE USER", "Clicks Update now – it", "downloads and installs itself")]
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
    rows = [("Installed", "Fleet Manager Pro 1.3", False), ("Available", "1.4   (28 MB)", True)]
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
    canvas.drawCentredString(w / 2, h * 0.42, "How a new version reaches everyone who uses Fleet Manager Pro")
    canvas.drawCentredString(w / 2, h * 0.2, f"Version {VERSION}")
    canvas.restoreState()


def build():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.95 * inch,
                            bottomMargin=0.8 * inch, title=f"Fleet Manager Pro - {SUBTITLE}", author="Fleet Manager Pro")
    s = [PageBreak()]
    a = s.append

    a(Paragraph("How updates reach everyone", H1))
    a(Paragraph("Fleet Manager Pro updates itself. You build a new version <b>once</b>, put it on the Fleet Manager "
                "server, and every copy downloads it from there by itself. Nobody needs to be sent a file.", P))
    a(Spacer(1, 6))
    a(flow_diagram())
    a(Spacer(1, 6))
    a(Paragraph("There is one build for everyone (the public copy). Copies sold before Fleet Manager Pro went free each "
                "have their own update channel; the same package is published to those channels too, and once they "
                "install it they are on the public channel like everyone else.", SMALL))

    a(Paragraph("Shipping a new version", H1))
    a(steps(["<b>Run the tests</b> in the development copy: <font face='Courier'>tests\\e2e_test.py</font>, "
             "<font face='Courier'>security_test.py</font> and <font face='Courier'>fuzz_test.py</font> must all say "
             "<b>ALL PASSED</b>.",
             "<b>Bump the version.</b> Open <font face='Courier'>Fleet Manager Pro\\VERSION</font> and raise it "
             "(for example <b>1.5.29-beta</b> to <b>1.5.30-beta</b>). The server only offers a version <i>higher</i> "
             "than what a copy runs.",
             "<b>Double-click</b> <font face='Courier'>Ship Update.bat</font> in the Development folder and type one "
             "line saying what's new. In about 10 minutes it builds the copy, publishes it to every channel, makes the "
             "installer (and copies it into the website's <font face='Courier'>downloads</font> folder) and packs "
             "<font face='Courier'>Build Output\\updates-for-cloud-&lt;version&gt;-&lt;date&gt;.zip</font>.",
             "<b>Upload the zip</b> to the cloud server (AnyDesk / Remote Utilities) and unzip it into the "
             "<font face='Courier'>Fleet Manager License Host</font> folder there. It only adds files under "
             "<font face='Courier'>updates\\</font>. No restart is needed.",
             "<b>Publish the new GitHub release</b> (the zip named Fleet-Manager-Pro-Windows.zip, so the website's download "
             "button gets it) and upload the website's index.html, then commit the release "
             "notes and push."]))
    a(Paragraph("From a terminal:", P))
    a(Paragraph('python "Ship Update.py" --notes "Adds X, fixes Y"<br/>'
                'python "Ship Update.py" --dry-run        (just show what it would do)<br/>'
                'python "Ship Update.py" --only-public    (skip the older buyer channels)', CODE))
    a(Paragraph("<b>Tip:</b> the older buyer channels come from the server when License Manager can reach it, "
                "otherwise from <font face='Courier'>Buyer Builds (PRIVATE).csv</font>.", TIP))

    a(PageBreak())
    a(Paragraph("Where the update files live on the server", H1))
    a(Paragraph("The server hands out updates from the <font face='Courier'>updates</font> folder "
                "<b>right next to <font face='Courier'>license_server.py</font></b>. There is one folder per update "
                "channel, named by its build tag, and an optional list of bad versions:", P))
    a(Paragraph("Fleet Manager License Host\\<br/>"
                "&nbsp;&nbsp;license_server.py<br/>"
                "&nbsp;&nbsp;updates\\<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;FMPB-ARM3-W2HW-T3FS-TGY3\\&nbsp;&nbsp;&lt;- the public channel<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Fleet Manager Pro 1.5.30-beta.zip<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;notes-1.5.30.txt<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;FMPB-....\\&nbsp;&nbsp;&lt;- one per older buyer copy<br/>"
                "&nbsp;&nbsp;&nbsp;&nbsp;notices.json&nbsp;&nbsp;&lt;- \"please update\" messages (optional)", CODE))
    a(Paragraph("Ship Update fills these folders on this PC and packs them into the upload zip, so you never build them "
                "by hand. Until the zip is unpacked on the cloud server, nobody is offered the new version.", P))

    a(Paragraph("Telling a bad version to update", H1))
    a(Paragraph("If a version turns out to have a serious bug, every copy still on it can be told to update. It sees a "
                "red <b>Please update</b> banner on every page, with <b>Update now</b> for the Owner and Managers. "
                "Nothing is locked or switched off: it is only a message. In the License Host folder:", P))
    a(Paragraph('python update_notice.py 1.5.30-beta "Jobs can be paid twice in this version. Please update now."<br/>'
                'python update_notice.py "&lt;1.5.30" "This version is too old. Please update."<br/>'
                'python update_notice.py              (list them)<br/>'
                'python update_notice.py --clear 1.5.30-beta      --clear-all', CODE))
    a(Paragraph("It writes <font face='Courier'>updates\\notices.json</font>; upload that file to the same place on the "
                "cloud server (the next upload zip carries it too). Copies see it at their next check. Versions before "
                "1.5.30 don't show the banner.", SMALL))

    a(PageBreak())
    a(Paragraph("What people see", H1))
    a(Paragraph("A banner appears at the top of the app and the <b>Updates</b> page shows the same with a "
                "<b>Check for updates</b> button. <b>Update now</b> downloads the new version from the server, checks "
                "its digital signature and checksum, then closes and reopens on the new version. The company's data is "
                "never touched. With <b>Install updates automatically</b> on, it installs as soon as nobody is driving.", P))
    a(Spacer(1, 8))
    a(Paragraph("The banner every page shows:", H2))
    a(banner_mock())
    a(Spacer(1, 12))
    a(Paragraph("The Updates page:", H2))
    a(card_mock())

    a(Paragraph("If something goes wrong", H1))
    a(table([["Problem", "What to check"],
             ["Nobody sees the update",
              "Did you raise <font face='Courier'>VERSION</font>? Did you unzip the upload zip into the License Host "
              "folder on the cloud server? Is <font face='Courier'>license_server.py</font> running? A copy can force a "
              "check with <b>Updates &gt; Check for updates</b>, or by restarting."],
             ["“Update available” but Update now fails",
              "The PC couldn't reach the server. Check the server is running and that "
              "<font face='Courier'>fleetmanager.servehttp.com:8750</font> is reachable from outside."],
             ["Where are the logs?",
              "The app writes to its <font face='Courier'>data\\logs</font> folder "
              "(<font face='Courier'>fleetmanager.log</font>, <font face='Courier'>errors.log</font>). The server writes "
              "<font face='Courier'>logs\\server.log</font> next to it."]],
            [1.9 * inch, 4.9 * inch]))
    a(Paragraph("<b>Keep <font face='Courier'>host_key.json</font> private.</b> It signs every update: anyone who has it "
                "could make one that every copy would install.", NOTE))

    doc.build(s, onFirstPage=cover, onLaterPages=page)
    print("Wrote", OUT)


if __name__ == "__main__":
    build()
