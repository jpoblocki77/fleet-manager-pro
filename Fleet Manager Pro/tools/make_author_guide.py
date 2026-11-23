"""Developer tool: write the author's guide PDF (private: for you, not for users).

    python tools/make_author_guide.py            ->  ..\\Guides\\Fleet Manager Pro - Author Guide.pdf

Needs:  python -m pip install reportlab
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_install_guide import (APP, CODE, CRIMSON, H1, H2, MUTED, NAVY, NOTE, P, SMALL, TIP, VERSION, bullets,  # noqa: E402
                                cover as _cover, steps, table)
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate  # noqa: E402

OUT = os.path.join(os.path.dirname(APP), "Guides", "Fleet Manager Pro - Author Guide.pdf")
W = [2.2 * inch, 4.7 * inch]


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
    canvas.drawRightString(w - 0.75 * inch, h - 0.36 * inch, f"Author Guide (private)  -  version {VERSION}")
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(w / 2, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def cover(canvas, doc):
    _cover(canvas, doc, "Author Guide - keep private")


def build():
    doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.95 * inch,
                            bottomMargin=0.8 * inch, title="Fleet Manager Pro - Author Guide", author="Fleet Manager Pro")
    s = [PageBreak()]
    A = s.append
    SITE = "http://fleetmanager.servehttp.com:8080"
    A(Paragraph("This guide is for you, the author. Fleet Manager Pro is <b>free</b>: one public copy for everyone, no "
                "licence keys and no activation. It explains your folders, how to ship a new version, the website "
                "download, the Fleet Manager server and the secrets to keep safe. Users only ever get the public copy "
                "and the Installation Guide.", NOTE))

    A(Paragraph("1. Your folders", H1))
    A(table([["Folder", "What it is", "Share?"],
             ["D:\\Fleet Manager Pro\\Fleet Manager Pro RC ...\\Development\\Fleet Manager Pro", "Development copy: source, tests, tools. Change and test here.", "No"],
             ["...\\Development\\Fleet Manager License Host", "Fleet Manager server + License Manager WITH your signing key, admin token, old purchases and certificate key.", "<b>Never</b>"],
             ["...\\Build Output\\Installers\\Public\\Fleet Manager Pro Setup.exe", "<b>The download</b>: the one-file installer everyone gets.", "Yes, anyone"],
             ["...\\Build Output\\For Everyone.zip", "The same copy as a zip (no installer).", "Yes, anyone"],
             ["...\\Build Output\\updates-for-cloud-....zip", "What you upload to the cloud server after shipping.", "No"],
             ["D:\\Fleet Manager Pro\\Website", "The website (index.html, screens, download\\).", "It's public"],
             ["...\\License Host (PRIVATE - never share)", "Backup of your server folder with its secrets.", "<b>Never</b>"]],
            [2.6 * inch, 3.6 * inch, 0.7 * inch]))

    A(Paragraph("2. Shipping a new version", H1))
    A(steps(["Change and test the development copy: <b>tests\\e2e_test.py</b>, <b>security_test.py</b> and "
             "<b>fuzz_test.py</b> must all end with <b>ALL PASSED</b>.",
             "Raise <b>Fleet Manager Pro\\VERSION</b> (for example 1.5.29-beta to 1.5.30-beta).",
             "Double-click <b>Development\\Ship Update.bat</b> and type one line saying what's new. It builds the copy once, "
             "publishes it to everyone, makes the installer (and copies it to the website's download folder) and packs "
             "<b>Build Output\\updates-for-cloud-&lt;version&gt;-&lt;date&gt;.zip</b>. About 10 minutes.",
             "Upload that zip to the cloud server and unzip it into its <b>Fleet Manager License Host</b> folder. Every copy "
             "sees <b>Update available</b> at its next check (start-up or within a day).",
             "Upload the website's <b>download\\Fleet Manager Pro Setup.exe</b>, refresh the guides "
             "(<b>python tools\\make_install_guide.py</b>, <b>make_install_page.py</b>, <b>make_features_page.py</b>), "
             "commit and push."]))
    A(Paragraph("<b>Publishing an Update.pdf</b> has the details and pictures. After a game update or new DLC: "
                "<b>python tools\\build_cargo.py</b> refreshes the cargo, company and city lists.", SMALL))
    A(Paragraph("The build compiles the program to native machine code with <b>Nuitka</b> (no Python source or bytecode). "
                "The build PC needs, once: <b>python -m pip install nuitka</b>, Visual Studio (or its Build Tools) with "
                "<i>Desktop development with C++</i>, and Inno Setup 6 for the installer.", SMALL))

    A(Paragraph("3. Telling a bad version to update", H1))
    A(Paragraph("If a version has a serious bug, every copy still on it can show a red <b>Please update</b> banner. "
                "In the License Host folder run, for example:", P))
    A(Paragraph('python update_notice.py 1.5.30-beta "Jobs can be paid twice in this version. Please update now."', CODE))
    A(Paragraph("Then upload <b>updates\\notices.json</b> to the cloud server. <b>--clear 1.5.30-beta</b> removes it. It "
                "is only a message: nothing is locked, and the app keeps working.", SMALL))

    A(Paragraph("4. The website and the download", H1))
    A(bullets([f"The website is <b>{SITE}</b>, from the <b>D:\\Fleet Manager Pro\\Website</b> folder. Its <b>Download</b> "
               "button points to <b>download/Fleet Manager Pro Setup.exe</b>.",
               "Ship Update copies the new Setup file into that folder; upload the website afterwards.",
               "The install guide, features page and FAQ are made from the development copy (Guides folder)."]))

    A(Paragraph("5. Looking after the online features", H1))
    A(Paragraph("Each company gets its own online ID (<b>FMC-XXXX-XXXX-XXXX-XXXX</b>) the first time it uses the Apply "
                "page, Global Leaderboard or VTC Finder. It is random and kept in the company's own database. Copies that "
                "were activated with a key before 1.5.29 hand their online data over to their new ID by themselves.", P))
    A(table([["Situation", "What to do (License Manager, on the cloud box)"],
             ["A company misuses the online features", "<b>5) Block a company</b>: type its company ID (or an old purchase key). "
                                                       "It loses the Apply page, leaderboard and VTC Finder; the app itself keeps working."],
             ["Changed your mind", "<b>6) Unblock a company</b>."],
             ["A VTC Finder listing is reported", "<b>11) VTC Finder listings</b>: hide or show it, or forget its reports."],
             ["Who is it?", "The company ID shows in Settings &gt; About of that copy and in the server log."]], W))
    A(Paragraph("The old purchase records (2, 3, 4, 7-10) only matter for copies sold before Fleet Manager Pro went free.", SMALL))

    A(Paragraph("6. The Fleet Manager server", H1))
    A(table([["", ""],
             ["Server", "cloud box (AnyDesk / Remote Utilities in), folder with License Server.bat (keep its window open)"],
             ["Public address", "https://fleetmanager.servehttp.com:8750 (No-IP name; keep the No-IP DUC running)"],
             ["Admin", "License Manager runs on the cloud box itself (admin is local-only)"],
             ["Firewall", "inbound TCP 8750 open on the cloud box"],
             ["Hosts", "updates, every company's Apply page, the Global Leaderboard and the VTC Finder"]], W, head=False))
    A(Paragraph("<b>Updating the server:</b> copy license_server.py, license_tool.py, update_notice.py, publish_update.py, "
                "the .bat files and the README over the old ones, then close and reopen License Server.bat. <b>Never</b> "
                "copy licenses.db, host_key.json, server_config.json or the .pem files over the server's own.", P))

    A(Paragraph("7. Keep these secret", H1))
    A(bullets(["<b>host_key.json</b> - your signing key. Anyone with it could sign an update every copy would install.",
               "<b>server_config.json</b> - the admin token for License Manager.",
               "<b>tls_key.pem</b> - the server's certificate key. Every copy is pinned to its certificate.",
               "<b>licenses.db</b> - companies' online IDs, old purchases and what they published.",
               "Any <b>data</b> folder - a company's database, its online secret and its session key (secret.key).",
               "Back all of these up somewhere offline. None of them is in any zip the build scripts make."]))
    A(Paragraph("Never zip a running copy's folder by hand to send it: it would include its data folder. Use the Setup file.", NOTE))

    A(Paragraph("8. Copyright and the licence agreement", H1))
    A(bullets(["Fleet Manager Pro is <b>(c) 2026 Jeff Poblocki. All rights reserved.</b> The notice is on every page of the app.",
               "The licence agreement is <b>Fleet Manager Pro\\fmpro\\EULA.txt</b>. It ships as <b>Licence Agreement.txt</b>, "
               "the installer shows it, and the app opens on it until <b>I agree</b> is ticked (recorded in Settings &gt; "
               "Audit log: <i>licence agreement accepted</i>).",
               "It lets people use it and pass on unchanged copies for free; it forbids selling it, changing it and "
               "reverse engineering. If you find a changed or paid copy online, send the site a DMCA takedown notice.",
               "To change the terms, edit EULA.txt and ship a new version."]))
    A(Paragraph("<b>Antivirus and code signing.</b> Until the program is code-signed, Microsoft Defender may wrongly flag it. "
                "The Installation Guide has a section, marked temporary, on restoring and allowing it. Report each new "
                "release as a false positive at microsoft.com/wdsi/filesubmission (Software developer, Incorrectly "
                "detected). Once you have a code-signing certificate, add signing to the build and remove that section.", SMALL))

    A(Paragraph("9. Older guides", H1))
    A(Paragraph("<b>Running the Licence Server at Home</b>, <b>Resetting a Licence's Uses</b>, <b>Kamatera Hosting Guide</b> and "
                "<b>Putting Fleet Manager on GitHub</b> were written for the original Fleet Manager. Their server, router, No-IP, "
                "cloud and GitHub steps still apply; the key and activation parts no longer do. The guides for the original "
                "app are kept in <b>Guides\\Older (classic Fleet Manager)</b>.", P))
    doc.build(s, onFirstPage=cover, onLaterPages=page)
    print("written:", OUT, f"({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    build()
