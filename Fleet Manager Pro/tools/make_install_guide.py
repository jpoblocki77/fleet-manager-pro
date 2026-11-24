"""Developer tool: write the installation guide PDF (shipped with every copy and on the website).

    python tools/make_install_guide.py            ->  ..\\Guides\\Fleet Manager Pro - Installation Guide.pdf

Needs:  python -m pip install reportlab
"""
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
VERSION = open(os.path.join(APP, "VERSION"), encoding="utf-8").read().strip()
OUT = os.path.join(os.path.dirname(APP), "Guides", "Fleet Manager Pro - Installation Guide.pdf")
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
    return ListFlowable([ListItem(Paragraph(t, P), leftIndent=18) for t in items], bulletType="1", bulletFontName="Helvetica-Bold",
                        bulletColor=CRIMSON, leftIndent=18)


def bullets(items):
    return ListFlowable([ListItem(Paragraph(t, P), leftIndent=14) for t in items], bulletType="bullet", bulletColor=CRIMSON,
                        leftIndent=14)


def table(rows, widths, head=True):
    t = Table([[Paragraph(c, P if i or not head else ParagraphStyle("th", parent=P, textColor=colors.white, fontName="Helvetica-Bold"))
                for c in r] for i, r in enumerate(rows)], colWidths=widths, repeatRows=1 if head else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d3dcea")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("ROWBACKGROUNDS", (0, 1 if head else 0), (-1, -1), [colors.white, PALE]),
          ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    if head:
        st.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    t.setStyle(TableStyle(st))
    return t


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
    canvas.drawRightString(w - 0.75 * inch, h - 0.36 * inch, f"Installation Guide  -  version {VERSION}")
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(w / 2, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def cover(canvas, doc, subtitle="Installation Guide"):
    w, h = letter
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, 0, w, h, stroke=0, fill=1)
    canvas.setFillColor(NAVY2)
    canvas.rect(0, h * 0.36, w, h * 0.3, stroke=0, fill=1)
    canvas.setFillColor(CRIMSON)
    canvas.rect(0, h * 0.36 - 6, w, 6, stroke=0, fill=1)
    # shield logo
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
    t.moveTo(cx + 4, cy - 14); t.lineTo(cx + 4, cy + 4); t.lineTo(cx + 18, cy + 4); t.lineTo(cx + 30, cy - 6); t.lineTo(cx + 30, cy - 14); t.close()
    canvas.drawPath(t, stroke=0, fill=1)
    canvas.setFillColor(CRIMSON)
    canvas.circle(cx - 18, cy - 17, 6, stroke=0, fill=1); canvas.circle(cx + 18, cy - 17, 6, stroke=0, fill=1)
    canvas.setFillColor(colors.white)
    canvas.setFont("Helvetica-Bold", 38)
    canvas.drawCentredString(w / 2, h * 0.55, "Fleet Manager Pro")
    canvas.setFont("Helvetica", 18)
    canvas.drawCentredString(w / 2, h * 0.49, subtitle)
    canvas.setFillColor(colors.HexColor("#8fa8cc"))
    canvas.setFont("Helvetica", 12)
    canvas.drawCentredString(w / 2, h * 0.42, "Virtual trucking company manager for American Truck Simulator and Euro Truck Simulator 2")
    canvas.drawCentredString(w / 2, h * 0.2, f"Version {VERSION}")
    canvas.restoreState()


def build():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    doc = SimpleDocTemplate(OUT, pagesize=letter, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.95 * inch,
                            bottomMargin=0.8 * inch, title="Fleet Manager Pro - Installation Guide", author="Fleet Manager Pro")
    s = [PageBreak()]
    A = s.append

    A(Paragraph("What you need", H1))
    A(table([["Item", "Details"],
             ["Windows PC", "Windows 10 or 11, 64-bit. Fleet Manager Pro runs on the same PC as the game (or any PC on your network)."],
             ["The game", "American Truck Simulator and/or Euro Truck Simulator 2 (Steam)."],
             ["Internet", "Not needed to run it. Used for updates and the online features (Apply page, Global Leaderboard, VTC Finder)."],
             ["A web browser", "Edge, Chrome or Firefox. Fleet Manager Pro opens in your browser."]],
            [1.6 * inch, 5.3 * inch]))
    A(Paragraph("Fleet Manager Pro is <b>free</b>: no licence key, no activation, no account. Download <b>Fleet-Manager-Pro-Windows.zip</b> "
                "from <b>https://github.com/jpoblocki77/fleet-manager-pro/releases</b> (or the Download button on http://fleetmanager.servehttp.com:8080) and unzip it. "
                "Nothing else needs installing: no Python, no database.", SMALL))

    A(Paragraph("1. Install", H1))
    A(steps(["Unzip the download, then double-click <b>Fleet Manager Pro Setup.exe</b> inside it.",
             "Accept the licence agreement, choose whether you want a desktop shortcut, and click <b>Install</b>. "
             "Windows does not ask for an administrator password.",
             "Leave <b>Start Fleet Manager Pro</b> ticked and click <b>Finish</b>. Later, start it from the Start menu or the desktop shortcut.",
             "A black window opens (leave it open while you use the app) and your browser opens "
             "<b>http://127.0.0.1:8760</b>. If the browser does not open, type that address yourself."]))
    A(Paragraph("<b>Windows protected your PC?</b> The first time, Windows may show a blue box because the program is new. "
                "Click <b>More info</b>, then <b>Run anyway</b>.", NOTE))
    A(Paragraph("If Microsoft Defender blocks or removes Fleet Manager Pro", H2))
    A(Paragraph("<b>This is a temporary step.</b> Fleet Manager Pro is new and is not yet code-signed, so Microsoft Defender may "
                "wrongly report <b>Fleet Manager Pro Setup.exe</b> or the installed program as a threat (a \"false positive\"). "
                "The program is being submitted to Microsoft to be signed and cleared; once that is done these steps won't be needed.", NOTE))
    A(steps(["Open <b>Windows Security</b> (Start menu &gt; type <b>Windows Security</b>) &gt; <b>Virus &amp; threat protection</b> "
             "&gt; <b>Protection history</b>.",
             "Click the entry that names Fleet Manager Pro. "
             "Check the path shown is the Setup file you downloaded, or <b>AppData\\Local\\Programs\\Fleet Manager Pro</b>.",
             "Click <b>Actions</b> &gt; <b>Restore</b> (if it was quarantined) or <b>Allow on device</b> (if it was blocked). "
             "Click <b>Yes</b> if Windows asks for permission.",
             "To stop it being flagged again: <b>Virus &amp; threat protection settings</b> &gt; <b>Manage settings</b> &gt; scroll to "
             "<b>Exclusions</b> &gt; <b>Add or remove exclusions</b> &gt; <b>Add an exclusion</b> &gt; <b>Folder</b>, and pick "
             "<b>%LOCALAPPDATA%\\Programs\\Fleet Manager Pro</b> (only that folder).",
             "Start Fleet Manager Pro again. If it still does not start, run <b>Fleet Manager Pro Setup.exe</b> again."]))
    A(Paragraph("Only do this for the Fleet Manager Pro folder, never for other programs or whole drives. "
                "When you install a signed version later, remove the exclusion again (same place, click it &gt; <b>Remove</b>). "
                "Other antivirus programs (Norton, Avast, Bitdefender...) have the same options under <i>Quarantine</i> and "
                "<i>Exceptions</i>. If it keeps happening, get in touch through the Fleet Manager Pro website.", SMALL))

    A(Paragraph("2. Accept the licence agreement", H1))
    A(steps(["The first screen shows the licence agreement. Read it, tick <b>I have read and agree to the licence agreement</b> "
             "and click <b>Agree and continue</b>.",
             "That is it: no key and no activation. You are not asked again on this PC."]))

    A(Paragraph("3. Create your company", H1))
    A(steps(["Enter your company name, tag and motto, pick your main game and units. Every company starts with $300,000 capital (fixed).",
             "Create the <b>owner account</b>: a username and a password of at least 8 characters. Keep this password safe: "
             "the owner is the only account that can do everything.",
             "Click <b>Create company</b>. You are signed in and see the Dashboard."]))

    A(Paragraph("4. Install the game plugin", H1))
    A(Paragraph("Fleet Manager Pro reads the game through a small plugin, <b>scs-telemetry.dll</b> (included). It goes in the game's plugins folder.", P))
    A(Paragraph("The easy way", H2))
    A(steps(["Close the game if it is running.",
             "In Fleet Manager Pro go to <b>Settings &gt; Telemetry</b>. Under <b>Game plugin</b> your games are listed.",
             "Click <b>Install</b> next to each game. (If your game is not listed, type its folder in the box below the list and click <b>Install here</b>.)",
             "Start the game. It shows a message about <b>advanced SDK features</b>: click <b>OK</b>."]))
    A(Paragraph("By hand", H2))
    A(Paragraph("Copy <b>%LOCALAPPDATA%\\Programs\\Fleet Manager Pro\\plugin\\scs-telemetry.dll</b> into:", P))
    A(Paragraph("...\\Steam\\steamapps\\common\\American Truck Simulator\\bin\\win_x64\\plugins\\<br/>"
                "...\\Steam\\steamapps\\common\\Euro Truck Simulator 2\\bin\\win_x64\\plugins\\", CODE))
    A(Paragraph("Create the <b>plugins</b> folder if it does not exist. To find the game folder: in Steam, right-click the game &gt; "
                "Manage &gt; Browse local files.", SMALL))
    A(Paragraph("<b>Check it works:</b> with the game running and your truck on the road, the top bar of Fleet Manager Pro shows "
                "your speed and truck, and <b>Live Cockpit</b> shows the gauges.", TIP))

    A(Paragraph("5. Your first delivery", H1))
    A(steps(["Pick your game in the <b>Game</b> box at the top (ATS, ETS2 or Both). Pages then show that game's data in its units.",
             "Open <b>ELD &amp; Compliance &gt; DVIR inspections</b>, tick the eight items and submit a <b>pre-trip</b> report. "
             "(Picking up a load without one is a small fine.)",
             "Take any job in the game and deliver it. Leave Fleet Manager Pro running while you drive.",
             "When you deliver, the job appears in the <b>Logbook</b> with its route, score, pay and XP, and the money is in <b>Finance</b>."]))
    A(Paragraph("No game to hand? <b>Settings &gt; Telemetry &gt; Source &gt; Demo simulator</b> drives a truck for you so you can look around.", SMALL))

    A(Paragraph("6. More drivers (optional)", H1))
    A(Paragraph("On the same network", H2))
    A(steps(["On the PC that runs Fleet Manager Pro, open <b>Drivers &gt; Connect a driver</b> and press <b>Let other PCs in</b> (once). "
             "The page shows an address like <b>https://192.168.1.20:8760</b>. Allow it if Windows Firewall asks.",
             "Other PCs open that address in a browser. The first time, the browser warns about the certificate (it is made by your own "
             "PC): choose <b>Advanced &gt; Continue</b>. The connection is encrypted.",
             "A manager adds each driver under <b>Drivers &gt; Add member</b>, or drivers apply on your online Apply link (section 8) and a manager accepts them."]))
    A(Paragraph("Logging a driver's own game", H2))
    A(Paragraph("Each driver who plays on their own PC installs Fleet Manager Pro on that PC with the same Setup file and runs one command. The exact command, "
                "with that driver's token and your server's fingerprint, is on their <b>profile page</b> under <b>Remote telemetry</b>:", P))
    A(Paragraph('"Fleet Manager Pro.exe" agent --server https://HOST-PC:8760 --token TOKEN --pin FINGERPRINT', CODE))
    A(Paragraph("Press <b>Windows + R</b>, type <b>%LOCALAPPDATA%\\Programs\\Fleet Manager Pro</b> and press Enter. Click the folder's "
                "address bar, type <b>cmd</b>, press Enter and paste the command.", SMALL))
    A(Paragraph("They only send their game data to your copy: they don't set up a company of their own.", SMALL))

    A(Paragraph("7. Finding your way around", H1))
    A(table([["Page", "What it is for"],
             ["Game box (top bar)", "Choose <b>ATS only</b>, <b>ETS2 only</b> or <b>Both games</b>. Pages show that game's jobs, contracts, "
                                    "events, trucks and garages, in its units (miles or kilometres). Money and Hours of Service are company-wide."],
             ["Dashboard", "Balance, revenue, distance, recent jobs, top drivers, news and upcoming events."],
             ["Live Cockpit", "Speed, gear, fuel, damage, lights, the current job and Hours of Service clocks, live."],
             ["Fleet Map", "A real map (state / country borders and cities). The roads your drivers have driven are drawn in and "
                           "remembered; cities you have delivered to get a red marker."],
             ["Logbook", "Every delivery: route, pay, costs, driving score. A manager approves any job held for review."],
             ["Dispatch", "Post contracts with a bonus and deadline. Pick the <b>from / to city</b>, the <b>cargo</b> and the "
                          "<b>from / to company</b> from drop-down lists of everything in the game (the company lists only show "
                          "companies that are in the city you picked); a matching delivery completes "
                          "the contract by itself, even when the driver plays the game in another language. "
                          "<b>Send to game</b> puts the contract into the game's freight market: it makes a new save "
                          "\"Fleet Manager Pro - Contract #N\" (your saves are not changed) - load it in the game and take "
                          "the job at the pickup company. Single-player only; use a browser on the game PC."],
             ["ELD &amp; Compliance", "Electronic logbook (driving hours), pre/post-trip inspections and roadside inspections. Managers can <b>Reset</b> the logbook and reports (type RESET to confirm)."],
             ["Fleet &amp; Garages", "Trucks (added automatically when first driven), trailers, maintenance. Buy a garage from a list of every garage location in the game; edit or delete garages (managers). <b>Record maintenance</b> charges fixed prices for a service, tyres, inspection, "
                                    "upgrade and wash, and a repair costs the truck's wear &times; the repair rate. Repairing the "
                                    "defects on an inspection report costs a set price per defect. All are set in Settings &gt; "
                                    "Economy, the only place they can be changed. Selling a garage removes it from the list. Sold and returned trucks and sold "
                                    "trailers stay listed until a manager clicks <b>Delete sold</b> (or <b>Delete</b> on one of them); "
                                    "their jobs and ledger lines are kept, so the money still adds up."],
             ["Truck Leasing", "Lease trucks instead of buying: compare plans (length, miles included, down payment, interest) side by side, then pay weekly. See section 9."],
             ["Finance", "Company account: income, costs, driver pay, loans. Export to a spreadsheet with the CSV button."],
             ["Expenses", "Every cost the company paid (fuel, tolls, fines, repairs, cargo damage, payroll...). Managers add, edit and delete their own expenses with <b>Add expense</b>."],
             ["Driver Performance", "Miles driven per driver and whether each load arrived on time or late."],
             ["Drivers / Recruitment", "Members, roles and divisions; driver applications from your online Apply link (see section 8). "
                                       "<b>Fire driver</b> (on their profile, type their name to confirm) deletes them from the database; "
                                       "their deliveries and pay stay in the books as \"Former driver\". Only the Owner can fire a Manager."],
             ["Events, Leaderboard, Achievements", "Convoys with sign-up and attendance XP (managers and dispatchers can <b>Edit</b> "
                                                   "any event; drivers who signed up are told about changes); rankings; badges."],
             ["Bell (top bar)", "Your notifications. Click <b>x</b> next to one to delete it, or <b>Delete all</b> to clear them; "
                                "<b>Mark all read</b> keeps them but clears the red count."],
             ["Settings", "Company, pay and prices, telemetry source and plugin, compliance rules, Discord, About. Fuel price, "
                          "driver pay and XP are shown per gallon / mile (imperial) or per litre / km (metric), following your units."]],
            [1.9 * inch, 5.0 * inch]))
    A(Paragraph("Roles: <b>Owner</b> (everything), <b>Manager</b> (fleet, finance, members, settings), <b>Dispatcher</b> (contracts, events, "
                "announcements), <b>Driver</b> and <b>Trainee</b>.", SMALL))

    A(Paragraph("8. Recruit drivers", H1))
    A(Paragraph("Drivers apply to your company on your own <b>online Apply page</b>. It is hosted for you, so there is nothing to "
                "set up and no router or firewall to change, and it works even while your PC is off.", P))
    A(steps(["Open <b>Recruitment</b> (Company menu). A few minutes after you set up the company your link appears under "
             "<b>Your Apply link</b>, for example <i>http://fleetmanager.servehttp.com:8750/apply/k7m2p9qa</i>. "
             "Each company gets its own link.",
             "Click <b>Copy</b> and share the link on your Discord, TruckersMP page, website or socials.",
             "Drivers open it on any device and fill in their name, how to contact them (Discord or email), age, country, "
             "game, experience and a message.",
             "Fleet Manager Pro collects new applications about every 10 minutes while it runs (or press <b>Check now</b>). "
             "They appear on Recruitment marked <i>online</i>; Owners and Managers get a notification, and so does your "
             "Discord channel if a webhook is set in Settings.",
             "Choose <b>Accept</b> (creates the driver's account, optionally in a division - then send them their username "
             "and password), <b>Interview</b> or <b>Reject</b>. Internal notes stay with the application."]))
    A(Paragraph("Your page shows your company name and the text from <b>Settings &gt; Recruitment</b>. Untick "
                "<b>Accept applications</b> there to close the page; tick it again to reopen it.", P))
    A(Paragraph("<b>Privacy and limits.</b> Applications wait on the Fleet Manager server only until your copy collects them, then "
                "they are deleted there. Only your own company (its private online ID) can collect your applications. To stop spam, each person can "
                "send at most 5 applications an hour, bots are filtered out, and at most 300 applications can wait at once - "
                "so open Fleet Manager Pro regularly.", SMALL))
    A(Paragraph("<b>No link, or \"Last check failed\"?</b> The PC needs internet access. Press "
                "<b>Check now</b>; if it still fails, get in touch through the Fleet Manager Pro website.", SMALL))

    A(Paragraph("9. Lease trucks", H1))
    A(Paragraph("Instead of paying for a truck all at once, the company can lease it: a small down payment, then a fixed weekly "
                "payment for a set number of weeks with a set number of miles included.", P))
    A(steps(["Open <b>Truck Leasing</b> (Company menu).",
             "Under <b>Compare lease plans</b> pick a new truck from the dealer list (or one of your own trucks) - the price fills in.",
             "Every plan is shown side by side: length, miles included, interest rate, down payment, weekly payment (truck value "
             "used plus interest), total you pay, total interest, the price to buy it at the end, the extra-mile charge and the "
             "fee for ending early.",
             "Click <b>Lease on ...</b> under the plan you want and confirm. The down payment is taken now; the weekly payment "
             "comes out of the company account every week.",
             "Open the lease (click it in <b>Your leases</b>) to see miles used, the pace you're on, payments made and what "
             "buying or returning the truck would cost today.",
             "When the last payment is made you're notified: <b>Buy it</b> for its end value, <b>Return it</b> (extra miles are "
             "charged) or <b>Renew</b> on any plan."]))
    A(Paragraph("Everyone in the company can see the costs; only Owners and Managers can lease, end a lease or change plans "
                "(<b>Lease plans</b> &gt; <b>Edit</b> / <b>New plan</b>). Changing a plan never changes leases already signed. "
                "A leased truck can't be sold - buy it out first.", SMALL))

    A(Paragraph("10. Looking after your data", H1))
    A(bullets(["Everything (company, drivers, jobs, money, online ID) is in <b>%LOCALAPPDATA%\\Programs\\Fleet Manager Pro\\data</b> (press Windows + R and paste that to open it). "
               "It appears the first time you start the program.",
               "<b>Back up:</b> close Fleet Manager Pro and copy the <b>data</b> folder somewhere safe.",
               "<b>Move to another PC:</b> close Fleet Manager Pro, run <b>Fleet Manager Pro Setup.exe</b> on the new PC, copy the "
               "<b>data</b> folder into the same place there and start it. Run the company on one PC at a time.",
               "<b>Already using a copy from a zip?</b> Close it, install, then copy its <b>data</b> folder into "
               "<b>%LOCALAPPDATA%\\Programs\\Fleet Manager Pro</b> before starting the installed one.",
               "<b>Update to a new version:</b> open <b>Updates</b>, press <b>Check for updates</b>, then <b>Update now</b>. The app "
               "closes and reopens by itself and your data is not touched.",
               "<b>Update by hand:</b> download the newest zip from https://github.com/jpoblocki77/fleet-manager-pro/releases, unzip it, close Fleet Manager Pro and run the Setup inside. Your data is kept.",
               "<b>Step by step:</b> <b>READ ME - Your Data.txt</b>, in the program folder and the Start menu, covers moving your "
               "data, backing up and going back to a backup.",
               "<b>Uninstall:</b> Windows Settings &gt; Apps. Your <b>data</b> folder stays; delete it yourself if you no longer want it."]))

    A(Paragraph("Troubleshooting", H1))
    A(table([["Problem", "Fix"],
             ["Top bar says \"waiting for game\"", "The plugin is not loaded. Repeat step 4, restart the game, and click OK on the SDK message."],
             ["Top bar says \"game not running\"", "The game is closed or still on the profile/menu screen. Get into the truck."],
             ["Jobs are not logged", "Fleet Manager Pro must be running <i>before</i> you deliver. Check Live Cockpit shows \"On job\" while you drive."],
             ["A delivery says \"pending review\"", "The anti-cheat checks held its pay (for example the app was closed for most of the drive). "
                                                    "A manager opens the job and clicks Approve."],
             ["The page will not open", "Is the black window still open? If you closed it, start Fleet Manager Pro again."],
             ["Fleet Manager Pro closes straight away", "Run <b>Fleet Manager Pro Setup.exe</b> again: it puts back any missing file and keeps your data."],
             ["Fleet Manager Pro is missing, or Defender says it's a threat", "A temporary false positive until the program is signed. "
                                                    "Restore and allow it - see <i>If Microsoft Defender blocks or removes Fleet Manager Pro</i> in section 1."],
             ["\"Too many wrong passwords\"", "Wait five minutes and try again. A manager can reset a driver's password on their profile page."],
             ["Forgot the owner password", "It can't be read back: get in touch through the Fleet Manager Pro website."],
             ["Port 8760 is already in use", "Another copy is still running (check Task Manager for Fleet Manager Pro). "
                                             "Close it, or start on another port from a Command Prompt in its folder (section 6 shows how to open it): "
                                             "\"Fleet Manager Pro.exe\" --port 8770"],
             ["Other PCs cannot connect", "Press <b>Let other PCs in</b> on Drivers &gt; Connect a driver, use the https:// address it shows, and allow it through Windows Firewall."]],
            [2.3 * inch, 4.6 * inch]))
    A(Spacer(1, 10))
    A(KeepTogether([Paragraph("Good to know", H2),
                    bullets(["The game plugin scs-telemetry.dll is open-source (MIT licence). It only reads data; "
                             "it does not change your game or your save.",
                             "It works alongside other telemetry tools: several can use the same plugin at once.",
                             "Fleet Manager Pro only listens on your own PC unless you let other PCs or tablets in."])]))
    A(Spacer(1, 10))
    A(Paragraph("Fleet Manager Pro is (c) 2026 Jeff Poblocki. All rights reserved. It is licensed, not sold, under the licence "
                "agreement: copying, sharing, reselling or reverse engineering it is not allowed.", SMALL))
    doc.build(s, onFirstPage=cover, onLaterPages=page)
    print("written:", OUT, f"({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    build()
