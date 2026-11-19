"""The "How to use this page" panel shown at the top of every page.

GUIDES maps a page's template name to a short list of steps. base.html shows the guide for whichever page is being
drawn (see install()); the panel can be folded away and each browser remembers that per page.
"""
from flask import before_render_template, g

GUIDES = {
    "dashboard": [
        "This is your company at a glance: the last 14 days of deliveries, who is driving right now, recent jobs and the top drivers.",
        "Click a driver, a job or a truck anywhere on the page to open its full details.",
        "Announcements, upcoming events and open contracts appear here too, so check this page when you start a session.",
        "Use the game picker at the top of the window to show ATS, ETS2 or both.",
    ],
    "live": [
        "Start American Truck Simulator or Euro Truck Simulator 2 and this page fills in by itself (it updates 4 times a second).",
        "Current job, truck, fuel, damage, lights, warnings, navigation and your Hours of Service clocks are all shown live.",
        "If it says \"Waiting for the game\", make sure the game is running and the telemetry plugin is installed (Settings, Telemetry tab).",
    ],
    "fluids": [
        "Drive with the game running and the tank bars and graph fill in on their own.",
        "Pick a range (1 hour, 6 hours, 24 hours or 7 days) to see how the levels changed; refuels show as jumps up.",
        "Owners and Managers can pick a driver to see their truck instead of their own.",
        "When fuel or AdBlue gets low you get a notification. Turn it off in Settings, Compliance tab.",
    ],
    "telemetry": [
        "Every value the game sends through scs-telemetry.dll is listed here, grouped by truck, trailers and job.",
        "Values that change flash red, so you can see what reacts when you press something in the truck.",
        "Pause freezes the list; Download snapshot saves everything as a JSON file for troubleshooting.",
    ],
    "map": [
        "Scroll to zoom and drag to pan. The lines are roads your company has driven; dots are drivers on the road right now.",
        "Whole map shows everything; Fit drivers zooms to the trucks that are driving.",
        "The map fills in as your drivers deliver more loads, so it grows over time.",
    ],
    "jobs": [
        "Every delivery is listed here. Jobs driven with telemetry log themselves the moment you deliver.",
        "Use the filters (driver, game, status) and press Filter to narrow the list.",
        "Click a job to see its route, timeline, costs and driving score.",
        "Drove without telemetry? Use Log a job manually; a Manager approves it before it is paid.",
    ],
    "job_detail": [
        "This page shows one delivery: the route driven, a timeline of the trip, the costs and the driving score.",
        "Owners and Managers can Approve & pay or Reject a manual job, clear warning flags, or delete the job.",
        "Use your browser's Back button to return to the Logbook.",
    ],
    "job_new": [
        "Use this form for a delivery you drove without telemetry, for example on another PC.",
        "Fill in the route, cargo, distance and pay, then press Submit job.",
        "If manual approval is on, the job waits in the Logbook until a Manager approves it.",
    ],
    "dispatch": [
        "Contracts are jobs the company wants driven. Press New contract to post one (route, cargo, pay and deadline).",
        "Drivers press Claim to take an open contract (Release gives it back).",
        "Pick up the matching load in the game and the contract links up and completes by itself on delivery; Done finishes it by hand.",
        "Send to game puts the contract into your game's freight market so you can pick it up in the truck.",
        "Assigned drivers get a notification; Cancel or Reopen a contract if plans change.",
    ],
    "planner": [
        "Pick a start city and a destination, then press Check this lane.",
        "You'll see what that lane has really paid your company, what it costs and how long it takes, from your own loads.",
        "Best next legs suggests where to head after you arrive; Most profitable lanes shows your best routes overall.",
    ],
    "compliance": [
        "The logbook records your duty status on in-game time: driving, on duty, off duty and sleeper berth.",
        "Use Change duty status when you stop or start work; driving is recorded by itself from telemetry.",
        "Simulate rest gives you a break, a sleeper berth or a full restart without waiting in the game.",
        "Fill in a DVIR (inspection report) before and after trips, and mark defects Repaired once they are fixed.",
        "Rules and limits are set in Settings, Compliance tab (US or EU rules per game).",
    ],
    "fleet": [
        "Trucks, garages and trailers your company owns. Trucks driven with telemetry are added by themselves.",
        "Use the Truck, Trailer and Garage buttons to add or buy one by hand.",
        "Click a truck to see its condition, jobs and maintenance history; Record maintenance logs a repair or service.",
        "Sell or delete trucks and trailers you no longer use; sold ones can be cleared from the list.",
    ],
    "truck": [
        "This page shows one truck: its condition, the jobs driven in it and its maintenance history.",
        "Edit changes the name, plate or details; Sell takes it out of the fleet and credits the company.",
        "Delete from list only removes the record, without any money changing hands.",
    ],
    "leasing": [
        "Lease a truck instead of buying it: pick a plan in Compare lease plans and press Lease on.",
        "Your leases shows what you are paying now; click a lease to buy it out, end it or renew it.",
        "Owners and Managers can add or edit lease plans with New plan.",
    ],
    "lease": [
        "This page shows one lease: what it costs, what ending it today would cost, and every payment so far.",
        "Use Buy it to own the truck outright, or Renew when the term ends.",
    ],
    "finance": [
        "The company ledger: freight income, fuel, tolls, fines, repairs, payroll, garages and loans.",
        "Entry records money in or out by hand; Take a loan borrows cash and Pay off now clears a loan early.",
        "Driver earnings shows what each driver has been paid.",
    ],
    "expenses": [
        "Every cost the company has paid, including fuel, tolls, fines, repairs and payroll.",
        "Filter by category, driver, truck or period to see where the money goes.",
        "Add expense records a cost by hand; Edit or Delete fixes one entered by mistake.",
    ],
    "insights": [
        "How the company really makes money, from delivered loads only.",
        "Pick a period at the top, then compare trends, cost and pay per distance, best lanes, cargo, trucks and drivers.",
        "Use it to decide which routes, cargo and trucks to focus on.",
    ],
    "performance": [
        "Shows each driver's loads, distance and whether loads arrived before the in-game deadline.",
        "Pick a period at the top; click a driver to see every load they delivered and how late any were.",
    ],
    "drivers": [
        "Everyone in the company. Click a member to see their profile, jobs and records.",
        "Owners and Managers can press Add member to create a login for a new driver and set their role.",
        "Divisions groups drivers into teams; former members can be deleted once they have left.",
    ],
    "driver": [
        "This member's profile: recent jobs, favourite cargo, top destinations, achievements and records.",
        "Remote telemetry gives a token so this driver can send live data from their own PC (New token makes a fresh one).",
        "Edit profile, change password, and (for Managers) change role or fire the member at the bottom of the page.",
    ],
    "recruitment": [
        "Share your Apply link (press Copy) on Discord, TruckersMP or socials; it works even when this PC is off.",
        "Applications appear here; press Check now to fetch new ones, then Accept, Interview or Reject each one.",
        "Applications must be opened in Settings, Recruitment tab.",
    ],
    "leaderboard": [
        "Pick what to rank by (distance, revenue, deliveries, tonnage, score, XP or realism) and the period.",
        "Realism rewards careful driving: distance weighted by driving score and cargo condition.",
    ],
    "achievements": [
        "Achievements unlock by themselves as you drive; each one is worth +250 XP.",
        "Unlocked ones are highlighted; each one says what you need to do to earn it.",
    ],
    "events": [
        "Company convoys, meetings and training. Press New event to plan one.",
        "Open an event and set your RSVP; showing up earns XP.",
    ],
    "event": [
        "Shows one event and who is coming. RSVP so the organiser knows you'll be there.",
        "Organisers mark who attended, then press Save attendance & award XP; Edit, Cancel or Delete the event if plans change.",
    ],
    "announcements": [
        "Company news for everyone. Owners and Managers write a post and press Post.",
        "Posts also go to Discord if a webhook is set in Settings, Discord tab.",
    ],
    "messages": [
        "Pick Company chat or a member from the list, type your message and press Send.",
        "Company chat goes to everyone; picking a member sends a private message only they can read.",
    ],
    "tablet": [
        "Turn on tablet access, then scan the code with your tablet or phone camera; it opens here already signed in.",
        "The tablet must be on the same Wi-Fi network as this PC.",
        "The first time, the tablet may warn about the security certificate; choose to continue. Turn access off when you're done.",
    ],
    "settings": [
        "Use the tabs across the top to move between Company, Economy, Telemetry, Compliance, Discord, Recruitment, Licence and the audit log.",
        "Change what you need and press Save at the bottom of that tab.",
        "The Telemetry tab installs the game plugin; the Licence tab moves this copy to another PC.",
    ],
    "updates": [
        "New versions come from your licence server. Press Check for updates to look now.",
        "Update now installs the new version; the app closes and reopens by itself. Your data is never touched.",
        "Turn on automatic updates to install them as soon as nobody is driving.",
    ],
    "system": [
        "Back up now saves a copy of the company database; automatic backups are also made every day.",
        "Logs show what the app has been doing. Filter them when you need to report a problem to the seller.",
    ],
}

TITLE = "How to use this page"


def guide():
    """The steps for the page being drawn, or None."""
    return GUIDES.get(getattr(g, "howto_page", None) or "")


def _remember(sender, template, context, **extra):
    name = (template.name or "").rsplit("/", 1)[-1]
    if name.endswith(".html") and not name.startswith("_") and name != "base.html":
        g.howto_page = name[:-5]


def install(app):
    before_render_template.connect(_remember, app)
    app.jinja_env.globals.update(howto=guide, HOWTO_TITLE=TITLE)
