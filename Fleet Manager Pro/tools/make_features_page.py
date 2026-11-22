"""Developer tool: write the searchable features page (one self-contained file, nothing loaded from outside).

    python tools/make_features_page.py [output file]      ->  ..\\Guides\\features.html

AREAS holds the words: (area id, area name, [features]); a feature is
(id, name, where to find it, who can use it, what it does, [steps], [good to know]). Text may use <b>, <i> and <code>.
"""
import html
import os
import re
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(APP), "Guides", "features.html")

ALL = "Everyone"
DISP = "Everyone can look; Dispatchers, Managers and the Owner make changes"
MGR = "Everyone can look; Managers and the Owner make changes"
ADMIN = "Managers and the Owner"

AREAS = [
    ("start", "Getting around", [
        ("menu", "Menu and page help", "Left side of every page", ALL,
         "The menu groups every page under Operations, Company, Community and Admin. Each page opens with a "
         "<b>How to use this page</b> panel with short steps that fit your role.",
         ["Click a page in the menu on the left.",
          "Read <b>How to use this page</b> at the top.",
          "Fold the panel away when you know the page. Your browser remembers that for each page."],
         ["Admin pages only show for Managers and the Owner.", "Click a driver, a job or a truck on most pages to see its details."]),
        ("game-box", "Game box (ATS, ETS2 or both)", "Top bar", ALL,
         "Chooses which game's data you see and in which units: ATS in miles and pounds, ETS2 in kilometres and tonnes, or both games.",
         ["Open the <b>Game</b> box at the top.", "Pick <b>ATS only</b>, <b>ETS2 only</b> or <b>Both games</b>."],
         ["It filters the logbook, dashboard, dispatch, events, leaderboard, driver figures, garages, trucks and map.",
          "Money (Finance) is for the whole company.", "If jobs seem to be missing, check this box first."]),
        ("status", "Connection status", "Top bar", ALL,
         "Shows whether Fleet Manager Pro can see your game, and your truck and speed while you drive.",
         ["Start the game and get into your truck.", "Look at the top bar: your speed and truck appear."],
         ["<b>waiting for game</b>: the game plugin isn't loaded. Install it in <b>Settings &gt; Telemetry</b>.",
          "<b>game not running</b>: the game is closed or still in its menus."]),
        ("notifications", "Notifications", "Bell in the top bar", ALL,
         "Tells you about things meant for you: a contract assigned to you, a level up, low fuel, an event that changed, a new application.",
         ["Click the bell. The red number counts unread ones.", "Click a notification to open what it is about.",
          "Click <b>x</b> to delete one, <b>Mark all read</b> to clear the number, or <b>Delete all</b>."], []),
        ("theme", "Light and dark look", "Sun button in the top bar", ALL,
         "Switches between the dark and light look.", ["Click the sun button. Click it again to go back."],
         ["Company colours are a separate setting: see <b>Company settings</b>."]),
        ("roles", "Roles", "Drivers", "Managers and the Owner set them",
         "A member's role decides what they can do: <b>Owner</b> (everything), <b>Manager</b> (fleet, finance, members, recruitment and settings), "
         "<b>Dispatcher</b> (contracts, events and announcements), <b>Driver</b> and <b>Trainee</b> (drive, claim contracts, log jobs, sign up for events).",
         ["Open <b>Drivers</b> and click the member.", "Under <b>Management</b>, choose the role and press <b>Save</b>."],
         ["Only the Owner can make someone a Manager, take loans, or dissolve the company.",
          "A Manager can't edit or remove anyone at their own level or above."]),
    ]),
    ("drive", "While you drive", [
        ("dashboard", "Dashboard", "Operations &gt; Dashboard", ALL,
         "Your company at a glance: the last 14 days of revenue and distance, who is driving now, recent jobs, top drivers, your wallet and level, "
         "news, upcoming events and open contracts.",
         ["Open it when you start a session.", "Look at <b>Open contracts</b> for work and <b>Announcements</b> for news.",
          "Click any job, driver or event to open it."],
         ["Managers also see jobs awaiting approval and new applications."]),
        ("live", "Live Cockpit", "Operations &gt; Live Cockpit", ALL,
         "A live dashboard for your truck that updates 4 times a second: speed and limit, RPM, gear, cruise control, current job, fluids, damage, "
         "your pedals and steering, lights, warnings, navigation, Hours of Service, trailers and wheels.",
         ["Start the game and get into your truck. The page fills in by itself.", "Put it on a second screen, or on a tablet with <b>Connect a Tablet</b>.",
          "Press <b>Stream overlay</b> for OBS, or <b>All data points</b> for every value."], []),
        ("fluids", "Fuel &amp; AdBlue", "Operations &gt; Fuel &amp; AdBlue", ALL,
         "Live tank levels and a graph of them over time, plus a refuel log showing when you filled up, how much went in and the level after.",
         ["Drive: the bars and graph fill in by themselves.", "Pick 1 hour, 6 hours, 24 hours or 7 days.", "Scroll down for the refuel log."],
         ["Readings are kept for 30 days.", "Managers can pick another driver at the top.",
          "The driver, Managers and the Owner are told when fuel or AdBlue runs low. Turn that off in <b>Settings &gt; Compliance</b>."]),
        ("map", "Fleet Map", "Operations &gt; Fleet Map", ALL,
         "A real map of borders and cities. Roads your company drives are drawn in and remembered, places you visit are marked, and drivers on the road show live.",
         ["Scroll to zoom and drag to move.", "Choose ATS or ETS2.", "Press <b>Whole map</b> to see everything, or <b>Fit drivers</b> to zoom to the trucks driving now."],
         ["The map starts empty and fills in as your drivers haul.", "Cities become exact once you have been there; unvisited UK cities in ETS2 are approximate."]),
        ("telemetry", "Telemetry Data", "Operations &gt; Telemetry Data", ALL,
         "Every value the game sends, grouped and searchable: truck, engine, lights, damage, job, navigation, wheels and trailers. Values that change flash red.",
         ["Type part of a name to find a value, or pick a group.", "Press <b>Pause</b> to hold the list still.", "Press <b>Download snapshot</b> to save everything as a JSON file."],
         ["Managers also get a raw view for troubleshooting."]),
        ("overlay", "In-game overlay", "Operations &gt; Overlays", ALL,
         "A small panel that floats over the game with the values you choose, plus pop-ups for breaks due, drive time running out, violations, inspections, defects and low fuel.",
         ["Run the game in <b>windowed</b> or <b>borderless</b> mode.", "Tick the values you want: driving, job, fuel and truck, or Hours of Service.",
          "Choose alerts, position, opacity, size and the hide/show key.", "Press <b>Save</b>, then <b>Open the overlay</b>.",
          "In the game, press <b>Ctrl + I</b> (or your key) to hide or show it."],
         ["Clicks go straight through it to the game.", "Exclusive full screen hides it, like every other window.",
          "It can open by itself when Fleet Manager Pro starts, and hide when the game is closed."]),
        ("stream", "Stream overlay (OBS)", "Operations &gt; Overlays", ALL,
         "A see-through panel for OBS, a second screen or a tablet: company and driver, speed, gear, trip, deadline, fuel, Hours of Service, damage, clock, your chosen values and alerts.",
         ["Choose a layout: wide bar, column or compact.", "Tick what to show, set the size and watch the preview.", "Press <b>Copy</b> to copy your link.",
          "In OBS: <b>Sources &gt; + &gt; Browser</b>, paste the link, width 1920, height 220, OK.", "Drag it where you want it."],
         ["Keep the link private: it signs in by itself. If it leaks, press <b>New token</b> on your profile.",
          "Use <b>Solid background</b> for a second screen; leave it off for OBS."]),
        ("tablet", "Connect a Tablet", "Community &gt; Connect a Tablet", "Everyone; Managers and the Owner turn it on",
         "Opens Fleet Manager Pro on a tablet or phone by scanning a code. You are signed in as yourself.",
         ["A Manager presses <b>Turn on tablet access</b>. If Windows asks, choose <b>Private networks</b> and <b>Allow</b>.",
          "Put the tablet on the same Wi-Fi as the PC.", "Scan the code with the camera and tap the link.",
          "The browser warns about the certificate the first time. iPad: <b>Show Details &gt; visit this website</b>. Android: <b>Advanced &gt; Proceed</b>.",
          "Use <b>Add to Home Screen</b> to open it like an app."],
         ["Each code works once and for a few minutes. Keep it private.", "Press <b>Turn off tablet access</b> when you don't need it."]),
    ]),
    ("jobs", "Jobs and dispatch", [
        ("logbook", "Logbook", "Operations &gt; Logbook", ALL,
         "Every delivery. A job you drive is logged the moment you deliver it, with route, cargo, distance, pay, costs, profit and driving score.",
         ["Start Fleet Manager Pro <i>before</i> you drive.", "Take a job in the game and deliver it.", "Use search, driver, status and game, then <b>Filter</b>.",
          "Click a job to open it.", "Press <b>Export CSV</b> for a spreadsheet."],
         ["Jobs from the demo simulator are marked <i>demo</i> and can be cleared."]),
        ("job", "Job details", "Logbook &gt; click a job", "Everyone; Managers and the Owner approve and delete",
         "One delivery in full: the route you drove, a timeline, every cost, anything the game took off the pay, damage, speeding, fuel and the driving score.",
         ["Open a job from the Logbook.", "Managers: press <b>Approve &amp; pay</b> or <b>Reject</b> on a held job, or <b>Clear flags</b>.", "Managers: <b>Delete job</b> removes it."],
         ["Pay is held when the checks find something odd: a position jump without a ferry or train, too much distance, a trip finished too fast, a drive the app barely saw, or pay far above the offer."]),
        ("manual", "Log a job by hand", "Logbook &gt; Log a job", ALL,
         "Adds a delivery you drove without telemetry.",
         ["Press <b>Log a job</b>.", "Fill in the cargo, route, distance and pay. Add fuel, tolls, fines and damage if you know them.",
          "Put a screenshot link in <b>Notes / proof</b> if your company wants one.", "Press <b>Submit job</b>."],
         ["Drivers' manual jobs wait for a Manager unless that is turned off in <b>Settings &gt; Economy</b>."]),
        ("score", "Driving score, XP, levels and ranks", "Logbook and your profile", ALL,
         "Each delivery gets a score from 0 to 100. You start at 100 and lose points for cargo damage, truck wear, fines, speeding and arriving late. "
         "Distance and weight earn XP, and a better score earns more.",
         ["Drive carefully and on time.", "See a job's score on its page.", "See your level and next rank on your profile."],
         ["The most you can lose: cargo damage 40, fines 30 (8 each), truck wear 25, speeding 25, late 15.",
          "Special transport adds 25% of the base XP and a company contract adds 20%.",
          "Ranks: Rookie, Driver (level 5), Senior Driver (10), Road Captain (20), Veteran (35), Elite (50), Legend (75).",
          "XP rates and how much speeding is forgiven are in <b>Settings &gt; Economy</b>."]),
        ("dispatch", "Dispatch Board", "Operations &gt; Dispatch", "Everyone can claim; Dispatchers, Managers and the Owner post",
         "Contracts the company wants driven, with a bonus, priority and deadline. A matching delivery completes the contract by itself.",
         ["Dispatchers: press <b>New contract</b>.", "Choose the game, cities, companies and cargo from the lists. Only the destination city is required.",
          "Set the bonus, deadline, priority and who it is for, then <b>Post contract</b>.", "Drivers: press <b>Claim</b>. <b>Release</b> gives it back.",
          "Pick up the matching load in the game and deliver it."],
         ["Company lists only show companies that are in the city you picked.", "It matches even when the driver plays in another language.",
          "Dispatchers can press <b>Done</b>, <b>Cancel</b> or <b>Reopen</b>.",
          "You can't claim when you are out of hours or your truck is on hold."]),
        ("send", "Send to game", "Dispatch &gt; Send to game", "Everyone, in a browser on the game PC",
         "Puts a contract into the game's freight market so you can pick it up in the truck.",
         ["Save your game.", "Press <b>Send to game</b> on the contract and pick the save to start from.",
          "In the game, load <b>Fleet Manager Pro - Contract #N</b>.", "Drive to the pickup company, take the job and deliver it."],
         ["It makes a new save and never changes your others.", "The contract needs a From city, To city and Cargo from the lists.",
          "Single-player only: TruckersMP doesn't use your saves.", "The job stays in the market for about a game day."]),
        ("planner", "Route Planner", "Company &gt; Route Planner", ALL,
         "Tells you whether a lane pays before you take it, using your company's own deliveries: average pay, costs, profit, time on the road and where to go next.",
         ["Choose a From city and a To city. Cargo is optional.", "Press <b>Check this lane</b>.", "New lane? Type the distance to get an estimate from your averages.",
          "Look at <b>Best next legs</b> and <b>Most profitable lanes</b>."],
         ["It needs some deliveries before it has anything to show."]),
    ]),
    ("eld", "ELD &amp; Compliance", [
        ("hos", "Electronic logbook (Hours of Service)", "Operations &gt; ELD &amp; Compliance", ALL,
         "Records your duty status on in-game time and shows how long you can still drive. ATS uses US rules and ETS2 uses EU rules, each with its own log.",
         ["Drive: driving and on-duty time record themselves. Sleeping in the game logs as sleeper berth.",
          "Press <b>Off duty</b>, <b>Sleeper</b> or <b>On duty</b> when you stop for the day.", "Use <b>Previous day</b> and <b>Next day</b> to look back."],
         ["US: 11 h driving, 14 h window, 30-minute break after 8 h, 70 h in 8 days or 60 h in 7.", "EU: 9 h driving, 45-minute break after 4.5 h, 56 h a week.",
          "Breaking a rule is a violation with a fine."]),
        ("rest", "Simulate rest", "ELD &amp; Compliance", "Each driver, for themselves",
         "Takes a break, a sleeper berth or a full restart now, without waiting in the game.",
         ["Press the rest you need: break, sleeper berth or restart.", "Your clocks update and the rest shows in green on the graph."],
         ["Only you can simulate your own rest."]),
        ("dvir", "Vehicle inspections (DVIR)", "ELD &amp; Compliance &gt; DVIR", "Everyone; Managers and the Owner mark repairs",
         "A pre-trip or post-trip report of eight items. Telemetry hints help you spot problems.",
         ["Choose pre-trip or post-trip and your truck.", "Mark each item <b>OK</b> or <b>Defect</b> and add notes.", "Tick the signature box and press <b>Submit report</b>.",
          "Managers: mark the report <b>Repaired</b> when it is fixed."],
         ["Picking up a load without a pre-trip in the last 24 game-hours is a violation.", "A defect puts the truck on hold until it is repaired, and repairs cost a set price per defect."]),
        ("roadside", "Roadside inspections", "ELD &amp; Compliance &gt; Inspections", "Everyone sees them; Dispatchers and above can start one",
         "On some loads an inspector checks you at a stop on the way. Level 1 checks driver, vehicle and load; level 2 driver and vehicle; level 3 hours and reports.",
         ["Keep your hours legal, do your pre-trip and fix defects.", "Read the results and pass rate here.", "Dispatchers: pick a driver who is driving and a level to inspect them now."],
         ["Serious findings put you out of service with a bigger fine.", "The chance per load starts at 15%. Change it in <b>Settings &gt; Compliance</b>."]),
        ("reset-eld", "Reset compliance records", "ELD &amp; Compliance &gt; Reset", ADMIN,
         "Clears logbooks, violations, reports or inspections for one driver or all, for example for a new season.",
         ["Press <b>Reset</b>.", "Choose the driver, game and what to clear.", "Type <b>RESET</b> and confirm."],
         ["It can't be undone. Fines already charged stay in the books."]),
    ]),
    ("fleet", "Trucks, trailers and leasing", [
        ("trucks", "Trucks", "Company &gt; Fleet &amp; Garages", MGR,
         "Your trucks with plate, driver, garage, odometer, condition and jobs. A truck is added by itself the first time it is driven.",
         ["Drive, and the truck appears. Or press <b>Truck</b> to buy one from the dealer list.", "Click a truck for its condition, jobs and maintenance.",
          "Press <b>Edit</b> to change its plate, driver, garage or service interval.", "Press <b>Sell</b> to take it out of the fleet and bank the money.", "<b>Delete sold</b> clears sold trucks from the list."],
         ["A purchase can't take the company account below zero.", "Suggested resale is 60% of its value.", "A leased truck can't be sold: buy it out first."]),
        ("maintenance", "Maintenance", "Fleet &amp; Garages &gt; Maintenance log &gt; Record", MGR,
         "Records services, repairs, tyres, inspections, washes and upgrades. Repairs you do in the game are noticed by themselves.",
         ["Press <b>Record</b>, pick the truck and the type.", "Check the cost, add notes and press <b>Save</b>."],
         ["Prices come from <b>Settings &gt; Economy</b> and can't be changed here. A repair costs the truck's wear times the repair rate.", "A service clears the <i>service due</i> reminder."]),
        ("garages", "Garages", "Company &gt; Fleet &amp; Garages", MGR,
         "Home bases for your trucks, in any garage location in either game.",
         ["Press <b>Garage</b> and choose a location and size.", "Leave the price blank for the standard price, then <b>Buy garage</b>.", "Use <b>Edit</b> to change it, or <b>Delete</b> to sell it."],
         ["Small: 1 truck, 100,000, 500 a week. Medium: 3 trucks, 250,000, 1,500 a week. Large: 5 trucks, 450,000, 2,500 a week."]),
        ("trailers", "Trailers", "Company &gt; Fleet &amp; Garages", MGR,
         "Trailers the company owns or leases.",
         ["Press <b>Trailer</b> and choose a model: name, brand and price fill in.", "Set its type, plate and garage, then <b>Add trailer</b>.", "Press <b>Sell</b> or <b>Delete</b> when you no longer need it."],
         ["Suggested resale is 60% of what you paid."]),
        ("lease", "Truck Leasing", "Company &gt; Truck Leasing", MGR,
         "Use a truck for a small down payment and a weekly payment, with a set number of miles included. Every cost is shown before you sign.",
         ["Pick a new truck, or one you own, under <b>Compare lease plans</b>.", "Compare length, miles, interest, down payment, weekly payment and buyout price.",
          "Press <b>Lease on</b> under the plan you want.", "Click the lease to see miles used, your pace and what ending it today would cost.", "After the last payment: <b>Buy it</b>, <b>Return</b> or <b>Renew</b>."],
         ["Payments are taken from the company account every week.", "Built-in plans: Short Haul (12 weeks), Regional (26), Long Haul (52), Owner-Operator (104).",
          "Extra miles are charged when you return it. Ending early costs a share of the payments left.", "Managers add or change plans with <b>New plan</b> and <b>Edit</b>; signed leases keep their terms."]),
        ("trailer-lease", "Trailer Leasing", "Company &gt; Trailer Leasing", MGR,
         "The same idea for trailers, with no mileage limit.",
         ["Pick a trailer under <b>Compare trailer lease plans</b>.", "Compare the plans and press <b>Lease on</b>.", "Click a lease to see what it costs and what ending it would cost.", "At the end: buy it, return it at no charge, or renew."],
         ["Built-in plans: Short Term (12 weeks), Seasonal (26), Annual (52), Long Term (104).", "The game doesn't report trailer mileage, so there is no mileage charge."]),
    ]),
    ("money", "Money and results", [
        ("finance", "Finance", "Company &gt; Finance", MGR,
         "The company ledger: freight income, driver pay, fuel, tolls, fines, repairs, garages, leases and loans, with a 30-day cash-flow chart.",
         ["Read the balance, income and expenses at the top.", "Look at <b>By category</b> and <b>Driver earnings</b>.", "Managers: press <b>Entry</b> to record money in or out by hand.", "Press <b>CSV</b> for a spreadsheet."],
         ["Every company starts with 300,000. That can't be changed.", "Costs from driving are booked by themselves."]),
        ("loans", "Loans", "Finance &gt; Take a loan", "The Owner",
         "Borrows money for the company and pays it back every week.",
         ["Press <b>Take a loan</b>.", "Choose a plan: Starter (12 weeks, 6.9%), Standard (26 weeks, 8.9%) or Expansion (52 weeks, 11.5%).", "Type an amount from 10,000 to 5,000,000 and press <b>Borrow</b>.", "Press <b>Pay off now</b> to finish early."],
         ["Up to 3 loans at a time. There is no penalty for paying off early."]),
        ("expenses", "Expenses", "Company &gt; Expenses", MGR,
         "Every cost the company has paid, with search, filters and totals by category.",
         ["Filter by category, truck, driver, source or words.", "Managers: press <b>Add expense</b> for costs the game doesn't know, such as insurance or parking.", "Use <b>Edit</b> or <b>Delete</b> to fix an expense entered by hand."],
         ["The page shows the newest 500. Press <b>CSV</b> for all of them."]),
        ("insights", "Insights", "Company &gt; Insights", ALL,
         "How the company really makes money: profit margin, pay, cost and profit per mile or kilometre, 12-month trends, and the best lanes, cargo, trucks and drivers.",
         ["Pick a period at the top.", "Compare profit with cost per distance.", "Use the best lanes and cargo to choose your next loads."],
         ["Only delivered loads count."]),
        ("performance", "Driver Performance", "Company &gt; Driver Performance", ALL,
         "Each driver's loads, distance, on-time rate, score, revenue, damage and fines.",
         ["Pick a period.", "Click a driver to see each load's deadline and arrival time."], []),
    ]),
    ("people", "Drivers and recruitment", [
        ("drivers", "Drivers and divisions", "Company &gt; Drivers", MGR,
         "Everyone in the company: role, division, level, jobs, distance, hours left and who is on the road.",
         ["Press <b>Add member</b>, type a username and temporary password, choose a role and press <b>Add</b>.", "Give the driver their username and password.",
          "Press <b>Divisions</b> to make teams, then set a member's division on their profile."],
         ["Passwords need at least 8 characters."]),
        ("profile", "Profiles", "Drivers &gt; click a member", "Everyone edits their own; Managers and the Owner manage others",
         "A member's level, recent jobs, favourite cargo, destinations, achievements, records and contact names.",
         ["Open your own profile to change your name, colour, country, bio or password.", "Managers: change role, status, division or pay, or reset a password.",
          "<b>New token</b> makes a fresh key for remote telemetry and the stream overlay."],
         ["<b>Leave the company</b> deletes your account for good.", "<b>Fire</b> (Managers) deletes a member for good. Deliveries and pay stay in the books as \"Former driver\"."]),
        ("connect", "Connect a driver (their own PC)", "Drivers &gt; Connect a driver", ADMIN,
         "Lets a driver play on their own PC, at home or anywhere, and have their jobs logged here.",
         ["Start <b>Fleet Manager Pro (LAN)</b> on your PC.", "Save your internet address, or press <b>Find it for me</b>.",
          "Press <b>Open the port for me</b>, or forward the port on your router yourself.", "Pick the driver and press <b>Download file</b> (or <b>Copy the line</b>). Send it only to them.",
          "The driver installs Fleet Manager Pro and the game plugin, puts the file in their Fleet Manager Pro folder, double-clicks it and leaves it open."],
         ["The driver needs no purchase key.", "The file holds their private token. If it leaks, make a <b>New token</b> and send a new file.",
          "Keep your PC on, in LAN mode, while they drive.", "Drivers on your own Wi-Fi don't need the router step."]),
        ("lan", "LAN mode", "Start menu &gt; Fleet Manager Pro (LAN)", "Whoever runs the main PC",
         "Lets other PCs on your network open your company in a browser, encrypted.",
         ["Close Fleet Manager Pro and start <b>Fleet Manager Pro (LAN)</b>. In a zip copy, use <b>Start Fleet Manager Pro (LAN).bat</b>.", "Allow it if Windows Firewall asks.",
          "On the other PC, open the <code>https://</code> address the black window shows.", "Accept the certificate warning the first time, then sign in."],
         ["Without LAN mode (or tablet access), only your own PC can open it."]),
        ("recruitment", "Recruitment", "Company &gt; Recruitment", ADMIN,
         "An online Apply page for your company. It works even when your PC is off, and applications arrive here.",
         ["Copy your Apply link and share it on Discord, TruckersMP or your website.", "Press <b>Check now</b>, or wait about 10 minutes.", "Choose <b>Accept</b>, <b>Interview</b> or <b>Reject</b>.",
          "Accepting makes the driver's account: send them their username and password."],
         ["Open or close applications and change the page's text in <b>Settings &gt; Recruitment</b>.", "<b>Clean up old applications</b> deletes ones you have already decided.", "At most 300 can wait at once, so open the app regularly."]),
    ]),
    ("community", "Community", [
        ("events", "Events &amp; Convoys", "Community &gt; Events &amp; Convoys", DISP,
         "Convoys, meetings, training, public events and races, with sign-up and XP for showing up.",
         ["Dispatchers: press <b>New event</b> and fill in the time, game, server, meet-up and route.", "Everyone: open the event and choose your RSVP.", "Afterwards the organiser ticks who attended and presses <b>Save attendance &amp; award XP</b>."],
         ["You can set required DLC, a minimum level and slots.", "Drivers who signed up are told when the time, place or route changes."]),
        ("leaderboard", "Leaderboard", "Community &gt; Leaderboard", ALL,
         "Ranks your drivers by distance, revenue, deliveries, tonnage, score, XP or realism.",
         ["Pick what to rank by and the period."], ["Realism rewards careful driving: distance weighted by driving score and cargo condition."]),
        ("global", "Global Leaderboard", "Leaderboard &gt; Global", "Everyone can look; Managers and the Owner choose what is shared",
         "Ranks every Fleet Manager Pro company together, and the drivers of companies that share their names. A public page shows the same board.",
         ["Press <b>Global</b> on the Leaderboard.", "Pick what to rank by, the period and the game.", "Press <b>Update now</b> to refresh, at most once every 30 seconds.",
          "Managers: untick <b>Show my company on the global leaderboard</b> to leave, or tick <b>Show my drivers' names too</b>."],
         ["It updates every 10 minutes while the app runs.", "Shared: your company name, currency and totals, including revenue. Driver names only if you tick the box.",
          "Leaving removes your company straight away, and so does dissolving the company.", "Simulator drives don't count. Companies that don't update for 30 days drop off.", "Each company reports its own numbers, so treat it as friendly competition."]),
        ("achievements", "Achievements", "Community &gt; Achievements", ALL,
         "Badges that unlock as you drive, each worth 250 XP: first delivery, long hauls, heavy loads, perfect scores, ferries, trains, special transport, convoys and contracts.",
         ["Drive. They unlock by themselves.", "Open the page to see what each one still needs."], []),
        ("announcements", "Announcements", "Community &gt; Announcements", DISP,
         "Company news for everyone.",
         ["Dispatchers: write a title and message, choose Info, Important or Event, and press <b>Post</b>.", "Tick <b>Pin to top</b> to keep it first."],
         ["Posts also go to Discord when a webhook is set."]),
        ("messages", "Messages", "Community &gt; Messages", ALL,
         "Company chat for everyone, and private messages to one member.",
         ["Pick <b>Company chat</b> or a member.", "Type your message and press <b>Send</b>."],
         ["The menu shows how many you haven't read."]),
        ("discord", "Discord posts", "Settings &gt; Discord", ADMIN,
         "Posts deliveries, announcements, events, contracts and new applications to your Discord server.",
         ["In Discord: <b>Server Settings &gt; Integrations &gt; Webhooks &gt; New Webhook &gt; Copy URL</b>.", "Paste it into <b>Settings &gt; Discord</b> and tick what to post.", "Press <b>Save</b>, then <b>Send test message</b>."],
         ["Applications can use their own webhook, for a staff-only channel."]),
    ]),
    ("admin", "Settings and admin", [
        ("set-company", "Company settings and colours", "Settings &gt; Company", ADMIN,
         "Company name, tag, motto, main game, units, currency symbol and colours.",
         ["Change what you need.", "Pick a main and accent colour, or a ready-made pair. You see a preview straight away.", "Press <b>Save</b>."],
         ["Everyone sees the colours, including on the sign-in page. <b>Reset</b> brings back navy and crimson."]),
        ("set-economy", "Economy settings", "Settings &gt; Economy", ADMIN,
         "Fuel price, driver pay and commission, repair and service prices, XP rates and how much speeding is forgiven.",
         ["Type the new values and press <b>Save</b>."],
         ["Values use your units.", "Here you also choose whether in-game repairs are charged and whether manual jobs need approval."]),
        ("set-telemetry", "Telemetry settings and game plugin", "Settings &gt; Telemetry", ADMIN,
         "Installs the plugin that reads the game, and chooses where the data comes from and which driver this PC's game is credited to.",
         ["Close the game and press <b>Install</b> next to it.", "Start the game and click <b>OK</b> on the \"advanced SDK features\" message.", "Choose the driver under <b>Credit this PC's telemetry to</b> and press <b>Save</b>."],
         ["The plugin only reads data.", "Not listed? Type the game's folder and press <b>Install here</b>.", "You can hold every remote driver's delivery for approval, and list allowed host names."]),
        ("demo", "Demo simulator", "Settings &gt; Telemetry &gt; Source", ADMIN,
         "Drives a truck between real ATS cities so you can try everything without the game.",
         ["Set <b>Source</b> to <b>Demo simulator</b> and press <b>Save</b>.", "Watch Live Cockpit and the Logbook.", "Set it back to <b>Game</b> when you are done."],
         ["Its jobs and money go into your real books. Clear them from the Logbook with <b>Clear demo jobs</b>."]),
        ("set-compliance", "Compliance settings", "Settings &gt; Compliance", ADMIN,
         "Turns the logbook, inspections, roadside checks and low-fuel alerts on or off, and sets the rules, chances and fines.",
         ["Tick what you want, set the numbers and press <b>Save</b>."], ["Starting fines: 500 per hours violation, 250 for no pre-trip, 1,500 out of service."]),
        ("licence", "Licence", "Settings &gt; Licence", "Managers and the Owner look; the Owner deactivates",
         "Shows this PC's activation and lets you move to another PC.",
         ["Press <b>Check now</b> to check with the licence server.", "Moving? The Owner presses <b>Deactivate this PC</b>, then activates on the new PC with the same key."],
         ["One key works on 2 PCs.", "Without internet it works for 7 days."]),
        ("updates", "Updates", "Admin &gt; Updates", ADMIN,
         "Finds and installs new versions. Your data is never touched.",
         ["Press <b>Check for updates</b>.", "Read <b>What's new</b> and press <b>Update now</b>. The app closes and reopens.", "Or press <b>Install updates automatically</b>."],
         ["Automatic updates wait until nobody is driving.", "Every update is signed and checked before it installs."]),
        ("backups", "Backups", "Admin &gt; System &amp; Backups", ADMIN,
         "Saves copies of your company database.",
         ["Press <b>Back up now</b>.", "Leave <b>Automatic backups</b> on for one copy a day."],
         ["The newest 14 automatic copies are kept in <code>data\\backups</code>. Your own are kept until deleted.", "Only the Owner can download or delete them.",
          "Safest of all: close the app and copy the whole <b>data</b> folder. <b>READ ME - Your Data.txt</b> explains how to go back to a backup."]),
        ("logs", "Logs and health", "Admin &gt; System &amp; Backups", ADMIN,
         "Shows how this copy is doing and what it has been doing, for when you need help.",
         ["Pick a log and type a word to filter it.", "Press <b>Download all logs</b> to send them to the seller."],
         ["Passwords, tokens and purchase keys are hidden in the logs."]),
        ("audit", "Audit log", "Settings &gt; Audit log", ADMIN,
         "A list of who changed what, and when.", ["Open the tab and read the newest lines."], []),
        ("clear", "Clear all data", "Settings &gt; Clear data", "The Owner",
         "Starts the company over: deletes jobs, money, fleet and records, and keeps your account, settings and licence.",
         ["Press <b>Clear all data</b>.", "Choose whether to keep the members, tick the box, type the word shown and your password."],
         ["A backup is saved to <code>data\\backups</code> first."]),
        ("dissolve", "Dissolve the company", "Settings &gt; Clear data", "The Owner",
         "Ends the company: every other member is removed for good and recruiting closes.",
         ["Press <b>Dissolve the company</b>.", "Choose to keep your books or erase everything.", "Tick the box, type the word shown and your password."],
         ["This can't be undone in the app. Erasing saves a backup first.", "The company also leaves the Global Leaderboard."]),
    ]),
]

CSS = """
:root{--bg:#f4f7fb;--card:#fff;--ink:#1b2a41;--muted:#4c6283;--line:#d3dcea;--pale:#eef2f8;--accent:#c8102e;--mark:#ffe08a}
@media (prefers-color-scheme:dark){:root{--bg:#071327;--card:#0d203d;--ink:#e8eef8;--muted:#93a7c6;--line:#1d3a66;--pale:#12294c;--accent:#f0556d;--mark:#6b5200}}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:100px}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 "Segoe UI",system-ui,sans-serif}a{color:var(--accent)}
header{background:linear-gradient(135deg,#050e1c,#15325e);border-bottom:5px solid #c8102e;color:#fff;padding:44px 20px;text-align:center}
header h1{margin:0;font-size:clamp(28px,5vw,42px);line-height:1.15}header p{margin:8px auto 0;color:#b9cbe6;max-width:62ch}header a{color:#fff}
.search{display:none}.js .search{display:block;position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 20px}
.search div{max-width:1080px;margin:0 auto;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center}
.search input{flex:1 1 220px;min-width:0;padding:11px 14px;font:inherit;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:8px}
.search label{font-weight:600}.search p{margin:0;color:var(--muted);font-size:14px;flex:0 0 100%;min-height:1.6em}
.wrap{max-width:1120px;margin:0 auto;padding:24px 20px;display:grid;grid-template-columns:230px minmax(0,1fr);gap:28px;align-items:start}
nav{position:sticky;top:110px;max-height:calc(100vh - 130px);overflow-y:auto;font-size:14.5px}
nav b{display:block;margin:12px 0 4px}nav b:first-child{margin-top:0}
nav a{display:block;padding:3px 10px;color:var(--ink);text-decoration:none;border-left:3px solid var(--line)}
nav a:hover,nav a:focus-visible{border-color:var(--accent);color:var(--accent)}
main{min-width:0}h2{font-size:25px;margin:30px 0 12px}h2:first-child,section:first-child h2{margin-top:0}
article{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 26px;margin:0 0 14px}
article h3{margin:0;font-size:20px;color:var(--accent)}article h4{margin:14px 0 2px;font-size:15px}
.meta{margin:2px 0 10px;color:var(--muted);font-size:14px}article p{margin:6px 0}ol,ul{margin:4px 0;padding-left:24px}li{margin:4px 0}li::marker{color:var(--accent);font-weight:700}
code{font-family:Consolas,monospace;font-size:.92em;overflow-wrap:anywhere}[hidden]{display:none!important}
::highlight(found){background-color:var(--mark)}
footer{text-align:center;color:var(--muted);font-size:14px;padding:0 20px 34px}
@media (max-width:760px){.wrap{grid-template-columns:minmax(0,1fr);padding:16px}nav{position:static;max-height:260px;border:1px solid var(--line);border-radius:10px;padding:10px}article{padding:16px}}
@media print{nav,.search{display:none!important}.wrap{display:block;padding:0}article{border:0;break-inside:avoid}}
"""

JS = """
(function () {
  document.documentElement.classList.add("js");
  var box = document.getElementById("q"), found = document.getElementById("found"), nav = document.querySelector("nav");
  var arts = [].slice.call(document.querySelectorAll("article")), areas = [].slice.call(document.querySelectorAll("section"));
  var texts = arts.map(function (a) { return a.textContent.toLowerCase(); });
  function mark(words) {
    if (!window.CSS || !CSS.highlights || typeof Highlight === "undefined") return;
    CSS.highlights.delete("found");
    if (!words.length) return;
    var h = new Highlight();
    arts.forEach(function (a) {
      if (a.hidden) return;
      var w = document.createTreeWalker(a, NodeFilter.SHOW_TEXT), n;
      while ((n = w.nextNode())) {
        var t = n.nodeValue.toLowerCase();
        words.forEach(function (word) {
          for (var i = t.indexOf(word); i >= 0; i = t.indexOf(word, i + word.length)) {
            var r = document.createRange(); r.setStart(n, i); r.setEnd(n, i + word.length); h.add(r);
          }
        });
      }
    });
    CSS.highlights.set("found", h);
  }
  function run() {
    var words = box.value.toLowerCase().split(/\\s+/).filter(Boolean), count = 0;
    arts.forEach(function (a, i) {
      a.hidden = !words.every(function (w) { return texts[i].indexOf(w) >= 0; });
      if (!a.hidden) count++;
      nav.querySelector('a[href="#' + a.id + '"]').hidden = a.hidden;
    });
    areas.forEach(function (s) {
      s.hidden = !s.querySelector("article:not([hidden])");
      nav.querySelector('[data-area="' + s.id + '"]').hidden = s.hidden;
    });
    found.textContent = !words.length ? arts.length + " features" : count ? count + (count === 1 ? " feature found" : " features found") : "Nothing found. Try another word.";
    mark(words);
  }
  box.addEventListener("input", run);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && box.value) { box.value = ""; run(); }
    if (e.key === "/" && document.activeElement !== box) { e.preventDefault(); box.focus(); }
  });
  run();
})();
"""


def items(tag, rows):
    return f"<{tag}>" + "".join(f"<li>{r}</li>" for r in rows) + f"</{tag}>"


def build(out=OUT):
    nav, body, seen = "", "", set()
    for aid, aname, feats in AREAS:
        nav += f'<b data-area="a-{aid}">{aname}</b>'
        body += f'<section id="a-{aid}"><h2>{aname}</h2>\n'
        for fid, name, where, who, what, steps, notes in feats:
            assert fid not in seen, fid
            seen.add(fid)
            nav += f'<a href="#{fid}">{name}</a>'
            body += (f'<article id="{fid}"><h3>{name}</h3><p class="meta"><b>Where:</b> {where} &middot; <b>Who:</b> {who}</p>\n'
                     f"<h4>What it does</h4><p>{what}</p>\n<h4>How to use it</h4>{items('ol', steps)}\n"
                     + (f"<h4>Good to know</h4>{items('ul', notes)}\n" if notes else "") + "</article>\n")
        body += "</section>\n"
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Features - Fleet Manager Pro</title>
<meta name="description" content="What every Fleet Manager Pro feature does and how to use it, for American Truck Simulator and Euro Truck Simulator 2.">
<style>{CSS}</style>
</head>
<body>
<header><h1>Features</h1><p>What every Fleet Manager Pro feature does, and how to use it</p>
<p>Installing? See the <a href="install.html">installation guide</a>. Something wrong? See the <a href="faq.html">FAQ &amp; troubleshooting</a>.</p></header>
<div class="search" role="search"><div><label for="q">Search</label>
<input id="q" type="search" placeholder="For example: overlay, lease, loan, backup" autocomplete="off"><p id="found" role="status"></p></div></div>
<div class="wrap">
<nav aria-label="Features">{nav}</nav>
<main>
{body}</main>
</div>
<footer>Fleet Manager Pro is (c) 2026 Jeff Poblocki. American Truck Simulator and Euro Truck Simulator 2 are trademarks of SCS Software, which is not affiliated with Fleet Manager Pro.</footer>
<script>{JS}</script>
</body>
</html>
"""
    bad = re.findall(r"&(?!#?\w+;)", re.sub(r"(?s)<(style|script)>.*?</\1>", "", page))
    assert not bad, "a bare & in the text: write &amp;"
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)
    print("written:", out, f"({len(page) // 1024} KB, {len(seen)} features)", html.unescape(", ".join(a[1] for a in AREAS)))


if __name__ == "__main__":
    build(*sys.argv[1:2])
