"""Developer tool: write the seller's guide PDF (for you, never for buyers).

    python tools/make_seller_guide.py            ->  ..\\Guides\\Fleet Manager Pro - Seller Guide.pdf

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

OUT = os.path.join(os.path.dirname(APP), "Guides", "Fleet Manager Pro - Seller Guide.pdf")
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
    canvas.drawRightString(w - 0.75 * inch, h - 0.36 * inch, f"Seller Guide (private)  -  version {VERSION}")
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(w / 2, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def cover(canvas, doc):
    _cover(canvas, doc, "Seller Guide - keep private")


def build():
    doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.95 * inch,
                            bottomMargin=0.8 * inch, title="Fleet Manager Pro - Seller Guide", author="Fleet Manager Pro")
    s = [PageBreak()]
    A = s.append
    A(Paragraph("This guide is for you, the seller. It explains your folders, how to make a sale, look after the licence "
                "server, update everything after a change, and keep your secrets safe. Buyers only ever get a buyer zip and "
                "the Installation Guide.", NOTE))

    A(Paragraph("1. Your folders", H1))
    A(table([["Folder", "What it is", "Share?"],
             ["D:\\Fleet Manager Pro\\Fleet Manager Pro RC 1.3 Alpha\\Development\\Fleet Manager Pro", "Development copy: source, tests, tools. Change and test here.", "No"],
             ["...\\Fleet Manager License Host", "Licence server + License Manager WITH your signing key, admin token, purchases and certificate key.", "<b>Never</b>"],
             ["...RC 1.3 Alpha\\Build Output\\For Buyer - FMP-....zip", "<b>What you send a buyer</b>: their sealed copy, a single "
              "Fleet Manager Pro.exe.", "Yes, to that buyer"],
             ["...RC 1.3 Alpha\\License Host (PRIVATE - never share)", "Backup of your licence host with its secrets.", "<b>Never</b>"],
             ["...RC 1.3 Alpha\\Zips", "Old 1.3-alpha zips; the program and Source Code zips hold readable source.", "No"],
             ["D:\\FMP_tester", "A spare copy for testing activation like a buyer would.", "No"]],
            [2.6 * inch, 3.6 * inch, 0.7 * inch]))

    A(Paragraph("2. Making a sale", H1))
    A(steps(["Make sure the licence server is running on the server PC (192.168.1.29): the blue <b>LICENCE SERVER</b> window.",
             "If you changed anything since the last build, rebuild the buyer copy (section 4).",
             "On the server PC open <b>License Manager.bat</b> and choose <b>2) New purchase</b>. Type the buyer's name "
             "(email optional). Copy the key it shows: <b>FMP-XXXX-XXXX-XXXX-XXXX</b>. It works on 2 PCs.",
             "On this PC double-click <b>Development\\Build Buyer Copy.bat</b>. Paste the buyer's key and confirm. "
             "A few minutes later it opens Build Output.",
             "Send the buyer the zip it made, <b>For Buyer - FMP-....zip</b>, and the key. Everything they need, "
             "including the Installation Guide, is inside the zip."]))
    A(Paragraph("<b>Why one build per buyer:</b> each copy carries a hidden build tag made from that buyer's key, and its pages "
                "are encrypted so only the licence server can unlock them (a cracked copy that skips the licence check shows "
                "no pages). If a copy leaks, License Manager <b>10) Identify a copy</b> tells you whose it was. "
                "<b>Development\\Buyer Builds (PRIVATE).csv</b> lists every copy you built and for whom - keep it private.", SMALL))
    A(Paragraph("The buyer unzips it, double-clicks <b>Fleet Manager Pro.exe</b>, types the key on the Activate screen and "
                "creates their company. Nothing needs installing.", P))

    A(Paragraph("Selling it online", H2))
    A(Paragraph("Because every copy is built for one buyer, you need their <b>name and email</b> before you build - and your licence "
                "keys already capture both (step 3 above asks for the buyer's name and email and stores them with the key; they show "
                "up in <b>3) List</b> and <b>10) Identify a copy</b>). So any store that gives you the buyer's name and email at "
                "checkout fits. The loop is always the same:", P))
    A(steps(["Buyer pays on your store; you receive their <b>name and email</b>.",
             "License Manager <b>2) New purchase</b> - enter that name and email - gives you the key.",
             "<b>Build Buyer Copy.bat</b> - paste the key - builds their sealed copy.",
             "Send them their <b>For Buyer - FMP-...</b> zip and the key."]))
    A(table([["Where to sell", "Best for"],
             ["<b>Gumroad</b>", "Simplest start. One-time sales; gives you the buyer's name and email; can auto-deliver a download."],
             ["<b>Lemon Squeezy</b> / <b>Paddle</b>", "More professional: they handle sales tax / VAT for you and have a licence-key "
                                                      "API for later automation."],
             ["<b>itch.io</b>", "Reaches the trucking-sim and modding community; supports pay-what-you-want pricing."],
             ["<b>Ko-fi</b>", "Low fees; one-time shop items or small memberships."],
             ["<b>Patreon</b>", "Recurring support and updates - less suited to one-off key delivery; not ideal as your only store."]], W))
    A(Paragraph("<b>Pricing.</b> Price it like an indie sim tool, not business software. A one-time <b>$10-$20</b> (around $15) suits a "
                "companion app like this, and each key activates 2 PCs. A small <b>$2-$4 / month</b> membership is an option if you "
                "prefer recurring income, but one-time usually sells better here. These are rough market figures, not a promise.", SMALL))

    A(Paragraph("3. Looking after buyers' licences", H1))
    A(table([["Situation", "What to do (License Manager)"],
             ["Buyer moves to a new PC", "They click Settings &gt; Licence &gt; <b>Deactivate this PC</b> on the old one first. "
                                         "If they can't: <b>4) Release a PC</b> (needs the Machine ID from 3) List)."],
             ["Old PC died / Windows reinstalled", "<b>7) Reset a purchase's uses</b>, type YES: frees both PCs. Resets are counted in 3) List."],
             ["Refund, chargeback or abuse", "<b>5) Revoke</b>: every PC on that key locks at its next check (within 6 hours)."],
             ["Changed your mind", "<b>6) Restore</b> a revoked purchase."],
             ["Remove a purchase for good", "<b>8) Delete a purchase</b> (active or revoked), type DELETE. Its PCs lock at their next "
                                            "check and the key never works again. A copy is kept in deleted_purchases.jsonl."],
             ["Clean up old revoked keys", "<b>9) Delete ALL revoked purchases</b>, type DELETE."],
             ["Who has what", "<b>3) List purchases + PCs</b>."],
             ["List shows <b>! LEAK?</b>", "That PC runs a copy built for another buyer. Check with that buyer; revoke (5) "
                                           "the key if the copy was shared without permission."],
             ["Found a copy online", "Run it with <b>--build-tag</b> (see the menu) and use <b>10) Identify a copy</b> to see "
                                     "whose it was."]], W))
    A(Paragraph("Copies keep working for up to 7 days when they can't reach the server, so a short outage doesn't lock anyone out. "
                "License Manager only works on the server PC or your home network.", SMALL))

    A(Paragraph("4. After you change the program", H1))
    A(Paragraph("Work in the development copy, then:", P))
    A(Paragraph('python tests\\e2e_test.py<br/>python tests\\security_test.py<br/>python tests\\fuzz_test.py', CODE))
    A(Paragraph("All three must end with <b>ALL PASSED</b>. Then bump <b>Fleet Manager Pro\\VERSION</b> and, in the Development "
                "folder, double-click <b>Build and Publish Update (All Buyers).bat</b>. It rebuilds every active buyer's copy "
                "(the buyer list comes from the licence server) and publishes each one as an update; copy the new files in "
                "License Host\\updates to the server PC and every buyer sees <b>Update available</b>.", P))
    A(Paragraph("The build compiles the program to native machine code with <b>Nuitka</b>, so buyers get no Python source or "
                "bytecode. The build PC needs, once: <b>python -m pip install nuitka</b> and Visual Studio (or its Build Tools) with "
                "<i>Desktop development with C++</i>. Each buyer's copy takes about 2-3 minutes.", SMALL))
    A(bullets(["Refresh the guides with <b>python tools\\make_install_guide.py</b> and <b>python tools\\make_seller_guide.py</b>.",
               "After a game update or new DLC: <b>python tools\\build_cargo.py</b> refreshes the cargo, company and city lists."]))

    A(Paragraph("5. The licence server", H1))
    A(table([["", ""],
             ["Server PC", "192.168.1.29, folder with License Server.bat (keep its window open)"],
             ["Public address", "https://fleetmanager.servehttp.com:8750 (No-IP name; keep the No-IP DUC running)"],
             ["Router", "forward TCP 8750 only, to 192.168.1.29"],
             ["Firewall", "inbound TCP 8750 allowed on the server PC"],
             ["Also hosts", "every company's online recruitment Apply page"]], W, head=False))
    A(Paragraph("<b>Updating the server PC:</b> copy license_server.py, license_tool.py, reset_licences.py, the .bat files and the "
                "README over the old ones, then close and reopen License Server.bat. <b>Never</b> copy licenses.db, host_key.json, "
                "server_config.json or the .pem files over the server's own.", P))
    A(Paragraph("<b>Reset Licences.bat</b> wipes every purchase (it backs up licenses.db first and makes you type RESET). Only use it to "
                "start over; every buyer would have to be given a new key.", SMALL))

    A(Paragraph("6. Keep these secret", H1))
    A(bullets(["<b>host_key.json</b> - your signing key. Anyone with it can make working licences.",
               "<b>server_config.json</b> - the admin token for License Manager.",
               "<b>tls_key.pem</b> - the server's certificate key. Lose it and new copies need a new certificate.",
               "<b>licenses.db</b> - your buyers and their PCs.",
               "Any <b>data</b> folder - a company's database, its licence (licence.json) and its session key (secret.key).",
               "Back all of these up somewhere offline. None of them is in any zip the build scripts or packaging make."]))
    A(Paragraph("Never zip a running copy's folder by hand to send it: it would include its data folder. Use the buyer zips.", NOTE))

    A(Paragraph("7. Copyright and the licence agreement", H1))
    A(bullets(["Fleet Manager Pro is <b>(c) 2026 Jeff Poblocki. All rights reserved.</b> The notice is on every page of the app.",
               "The licence agreement is <b>Fleet Manager Pro\\fmpro\\EULA.txt</b>. It ships in every buyer zip as "
               "<b>Licence Agreement.txt</b> and is shown in the app (link on every page).",
               "Buyers must tick <b>I agree to the licence agreement</b> to activate. The app records when and with which key "
               "(Settings &gt; Audit log: <i>licence agreement accepted</i>).",
               "It forbids copying, sharing, reselling, reverse engineering and getting round the licence check. If you find a "
               "copy online: revoke the key it uses (License Manager <b>5) Revoke</b>) and send the site a DMCA takedown notice.",
               "To change the terms, edit EULA.txt and rebuild. Have a lawyer look at it if you sell a lot."]))
    A(Paragraph("<b>Antivirus and code signing.</b> Until the program is code-signed, Microsoft Defender may wrongly flag it "
                "(Fleet Manager Pro.exe). The Installation Guide has a section, marked temporary, on restoring and allowing it. "
                "Report each new release as a false positive at microsoft.com/wdsi/filesubmission (Software developer, "
                "Incorrectly detected). Once you have a code-signing certificate (for example Microsoft's Artifact Signing), "
                "add signing to the .exe build and remove the temporary section from the guide.", SMALL))
    A(Paragraph("Only ever send a buyer their own <b>For Buyer - FMP-...</b> zip. <b>Fleet Manager Pro 1.3-alpha.zip</b> and the <b>Source Code</b> zip "
                "contain the readable source.", NOTE))

    A(Paragraph("8. Older guides", H1))
    A(Paragraph("<b>Running the Licence Server at Home</b>, <b>Resetting a Licence's Uses</b>, <b>Kamatera Hosting Guide</b> and "
                "<b>Putting Fleet Manager on GitHub</b> were written for the original Fleet Manager. Their server, router, No-IP, "
                "cloud and GitHub steps still apply; where they say <i>For_Seller</i> read <i>For Buyer - FMP-....zip</i>, where "
                "they say <i>FM-</i> keys read <i>FMP-</i>, and where they mention <i>convoy.db</i> read <i>data\\fleet.db</i>. "
                "The guides for the original app are kept in <b>Guides\\Older (classic Fleet Manager)</b>.", P))
    doc.build(s, onFirstPage=cover, onLaterPages=page)
    print("written:", OUT, f"({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    build()
