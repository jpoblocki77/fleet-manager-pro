# Fleet Manager Pro — 1.3-alpha

A virtual trucking company (VTC) manager for **American Truck Simulator** and **Euro Truck Simulator 2**.
Built from scratch in **Python · Flask · Waitress · SQLite**, styled in navy blue and crimson red, with the
game's telemetry wrapped straight into the app through the bundled **scs-telemetry.dll**.

It combines the ideas behind four popular tools into one app (no code copied from any of them):

| Inspired by | What Fleet Manager Pro does |
|---|---|
| **Trucky** | Automatic job logging from telemetry, route trails, driving score, XP & levels, achievements, leaderboards, live map |
| **VTC Manager** | Members, roles & divisions, public recruitment form & application review, events/convoys with RSVPs and attendance XP, announcements, Discord webhooks |
| **Truckers Hub** | Driver profiles with lifetime stats, favourite cargo/destinations, realism ranking, live cockpit and stream overlay, remote driver agents |
| **Dispolotse** | Company economy: ledger, freight income, driver pay, fuel/tolls/fines/repairs, garages with weekly upkeep, fleet purchases & sales, maintenance, bank loans, dispatch contracts |

### Game selector, filter and map

* The **Game** box in the top bar (ATS only / ETS2 only / Both games) sets the units on every page **and** filters the
  data: logbook, dashboard, dispatch, events, leaderboard, driver stats, garages, trucks and the map show only that
  game. Money (Finance) and Hours of Service stay company-wide.
* The **Fleet Map** draws real geography in the game's own map projection (Lambert conformal conic, 1:20 for ATS,
  1:19 for ETS2): state / country borders and named cities, from public-domain data (US Census, Natural Earth) in
  `fmpro/static/map/`. No game files are used. Roads appear as your drivers drive them and are remembered; a city
  you pick up or deliver in is marked at its exact in-game position. ETS2 moves and enlarges the UK, so unvisited UK
  cities are approximate. `tools/build_map.py` rebuilds the map data. The ETS2 map has not yet been checked
  against real ETS2 driving.

### Send a contract to the game

Contracts you post don't show up in the game's freight market by themselves: the game only offers the jobs stored
in your save. On the Dispatch board, **Send to game** (shown only in a browser on the PC where the game is installed)
puts the contract into the game for you:

1. Save your game (or use the latest autosave), then click **Send to game** and pick the save to start from.
2. Fleet Manager Pro copies that save into a **new** slot named *Fleet Manager Pro - Contract #N*, with the job added
   to the freight market of a company in the From city that ships that cargo, going to the To city. Urgent / high
   priority contracts become urgent jobs. Your other saves are never changed; sending again replaces the same slot.
3. In the game: **Load game** → *Fleet Manager Pro - Contract #N* → drive to the pickup company and take the job.
   Delivering it completes the contract automatically.

The contract needs a **From city**, **To city** and **Cargo** picked from the dropdowns. Single-player only
(TruckersMP doesn't use your saves). Encrypted and plain-text saves both work.

### Dispatch city, cargo and company lists

The New contract form has dropdowns for **From city**, **To city**, **Cargo**, **From company** and **To company**.
Each starts with *Any* (the destination city is required), then what your own drivers have already been to or
hauled (exactly as the game reported it, so mods are included), then the game's full list, then **Other** to type a
name. **From company** and **To company** only list the companies that have a depot in the chosen From / To city
(read from the game's map files), so a contract can never ask for a company that doesn't exist there; pick the city
first. ETS2 cities show the English name with the local one, e.g. *Vienna (Wien), Austria*; same-name cities are kept
apart (*Carlsbad, California* / *Carlsbad, New Mexico*).

A contract only matches a job from its own game. Cities and cargo match by name **or** by the game's own id, so a
driver playing in German who delivers to "Wien" still completes a "Vienna" contract. When several contracts fit,
the most specific one wins.

All lists are read from the games' own definition files:

| | Cities | Cargo | Companies |
|---|---|---|---|
| ATS | 297 | 307 definitions (289 names) | 221 depots (151 names, e.g. Wallbert, Eddy's, Bushnell Farms) |
| ETS2 | 298 | 414 definitions (376 names) | 241 depots (205 names) |

Rebuild after a game update or new DLC (writes `fmpro/cargo_list.json`):
`python tools\build_cargo.py "<ATS folder>" "<ETS2 folder>"`

### Compliance (inspired by HaulTools Simulation)

**ELD & Compliance** adds an electronic logbook and roadside enforcement, all on in-game time:

* **Hours of Service** — duty status (off duty / sleeper / driving / on duty) is logged automatically from telemetry;
  sleeping in the game is logged as sleeper berth. ATS uses US rules (11 h driving, 14 h window, 30-min break after
  8 h, 70 h/8-day or 60 h/7-day cycle, 34 h restart); ETS2 uses EU rules (9 h, 45-min break after 4.5 h, 56 h week).
  Live clocks in the cockpit and logbook, a 24-hour ELD chart, violations with fines.
* **DVIR** — 8-item pre/post-trip inspection with telemetry hints (worn tires, low air…). Defects put the truck on a
  maintenance hold until a manager marks it repaired. A load picked up without a pre-trip in 24 h is a violation.
* **Roadside inspections** — on a share of loads an inspector checks the truck at a stop part-way along the route:
  Level 1 (driver, vehicle, load incl. weight), Level 2 (driver + vehicle) or Level 3 (hours and DVIR). Out-of-service
  findings, fines and an inspection history.
* **Dispatch holds** — drivers who are out of hours or whose truck is on hold can't claim contracts; the Drivers page
  shows each driver's hours left.

* **Driver bank accounts (My Bank)** — each driver's own account, separate from the company account. It opens with 3,000
  (Settings → Economy; not taken from the company). Pay leaves the
  company account when a load is delivered, waits as the week's pay, and is deposited every Friday at noon (the clock of
  the PC running the program; made up later if it was closed). Statement, pay slips, next pay. Fines (game, Hours of
  Service, DVIR, roadside inspections), traffic tickets and licence fines come out of it; Settings → Economy can make
  the company pay the fines instead. One offence is never paid twice. Personal loans at one of four APRs (4, 12, 26 or 52 weeks)
  work like the company's loans: the money goes into the driver's account and fixed instalments are taken from it every
  Friday after payday. Private: only the driver and managers who outrank them see it.
* **Driver's licences (CDL & Points)** — off until turned on. A driver passes a drivers test of two in-game hours
  (pre-trip, control skills, road, park), marked from live telemetry; 80 of 100 marks pass. The licence has 12 points.
  Accidents, red lights, fines and every 5 real minutes of speeding cost points and a ticket from the driver's bank account.
  One point comes back every real day. At zero the licence is suspended: wait for the points or pay the fine (5,000),
  then retake the test. Without a licence a driver can't claim contracts and new loads are held for a manager.
  The simulator never counts.

Settings → Compliance turns each part on/off and sets the rules and fines.

## Start

Fleet Manager Pro is free: no key, no activation, no account. Most people install the compiled program
(**Fleet Manager Pro Setup.exe** from <http://fleetmanager.servehttp.com:8080>): see
**Fleet Manager Pro - Installation Guide.pdf**. The steps below are for the source version.

1. Install Python 3.10+ from python.org (tick *Add to PATH*).
2. Double-click **Start Fleet Manager Pro.bat**. First run installs Flask, Waitress and cryptography, then opens
   <http://127.0.0.1:8760>.
3. Read and accept the licence agreement (**Agree and continue**). No key is needed.
4. Create your company and owner account (password: at least 8 characters).
5. **Settings → Telemetry → Game plugin → Install** (or copy `plugin\scs-telemetry.dll` into
   `<game>\bin\win_x64\plugins\`). Start the game and click OK on the "advanced SDK features" message.

No game handy? **Settings → Telemetry → Source → Demo simulator** drives a truck between real ATS cities with
tolls, fines, refuels and deliveries — everything it does goes through the same pipeline as the real game.

```
python run.py                    # this PC only, port 8760
python run.py --lan              # let drivers on your network connect over HTTPS  (or Start Fleet Manager Pro (LAN).bat)
python run.py --port 9000 --no-browser --data D:\FMP-data
python run.py agent --server https://HOST-PC:8760 --token TOKEN --pin FINGERPRINT    # stream a remote driver's game
```

Your data lives in `data\fleet.db` (SQLite) plus `data\secret.key`. Back up the `data` folder to keep everything.

## Telemetry — every data point

`fmpro/telemetry/layout.py` maps the plugin's entire 32 KiB shared-memory block (`Local\SCSTelemetry`,
plugin revision 10–12): **3,546 values** in total.

* **Game** – SDK state, pause, game/sim/render time, multiplayer offset, game & telemetry versions, time scale, in-game clock
* **Truck** – brand/model IDs, plate & country, odometer, gears & ratios, differential, retarder, shifter type & H-shifter slots
* **Drivetrain / engine** – speed, RPM, gear (actual & dashboard), cruise control, fuel/AdBlue level & capacity, average consumption, range, air/oil pressure, oil/water/brake temperature, battery voltage
* **Controls** – user & effective steering/throttle/brake/clutch, parking & engine brake, diff lock, lift axles, wipers, retarder level
* **Lights & warnings** – blinkers, parking/low/high beams, beacon, brake, reverse, hazard, aux front/roof, backlight; fuel, AdBlue, air, oil, water and battery warnings with their thresholds
* **Damage** – engine, transmission, cabin, chassis, wheels, cargo
* **Job** – cargo & IDs, mass, units, source/destination city & company, income, planned distance, deadline, market, special transport, cargo loaded
* **Navigation & position** – route distance/time, speed limit, rest stop, world X/Y/Z, heading, pitch, roll
* **Vectors** – linear/angular velocity & acceleration, cabin & head offsets, hook position
* **Wheels** (up to 16) – substance, radius, suspension deflection, velocity, steering, rotation, lift, on-ground, steerable/powered/simulated/liftable, positions
* **Trailers** (10 slots) – attached, IDs, brand, body/chain type, plates, wear (body/chassis/wheels), cargo damage, all wheel data, velocities, world placement
* **Gameplay events** – delivered (revenue, XP, damage, distance, delivery time, autopark/autoload), cancelled (penalty), fines (offence, amount), tolls, ferries & trains (route, price), refuels
* **Substances** – the 25 surface names used by wheel substance indices

See them all live in **Telemetry Data** (with a raw hex viewer for admins). The **Live Cockpit** and the `/overlay`
HUD (for OBS: `/overlay?key=YOUR_TOKEN&solid`) are built on the same feed.

## Project layout

```
run.py                    launcher (Waitress), LAN / HTTPS mode, remote agent
fmpro/__init__.py         app factory, template filters, paths for the compiled build
fmpro/db.py               SQLite schema, upgrades of older databases, helpers
fmpro/auth.py             accounts, roles, sessions, CSRF, host check, login throttling, game choice, setup, agreement
fmpro/licensing.py        the pinned TLS channel to the Fleet Manager server, signature checks, build tag
fmpro/online_id.py        the company's own online ID for the Apply page, Global Leaderboard and VTC Finder
fmpro/EULA.txt            the licence agreement everyone accepts on first start
fmpro/pages.py            pages and forms: dashboard, logbook, dispatch, fleet, finance, drivers, events, settings...
fmpro/compliance.py       Hours of Service rules and clocks, DVIR, roadside inspections, dispatch holds
fmpro/compliance_views.py the ELD & Compliance pages
fmpro/bank.py             drivers' bank accounts and the Friday payday (bank_views.py: the page)
fmpro/personal_loans.py   drivers' personal loans (the company's own loans are in economy.py)
fmpro/cdl.py              driver's licences: points, tickets, suspension (not the program's own licence: licensing.py)
fmpro/cdl_test.py         the drivers test: marks one telemetry frame at a time
fmpro/cdl_views.py        the CDL & Points page
fmpro/economy.py          XP/levels/ranks, scoring, ledger, loans, upkeep, achievements, Discord webhook
fmpro/api.py              JSON API: live telemetry, remote agents (sanitised), map layers, overlay, CSV exports
fmpro/network.py          "Let other PCs in" switch (the network door on a normal start)
fmpro/tlsfront.py         HTTPS front door for LAN mode (self-signed certificate, pinning)
fmpro/telemetry/          layout.py (DLL memory map) · reader.py (decoder) · service.py (sampler, job tracker,
                          anti-cheat, road/place learning) · demo.py (simulator)
fmpro/cargo_list.json     cargo and company names per game (built by tools/build_cargo.py)
fmpro/static/map/         Fleet Map borders and cities per game (built by tools/build_map.py)
fmpro/templates, static   UI (no external libraries or CDNs)
plugin/                   scs-telemetry.dll + its MIT licence
tools/                    build_cargo.py · build_map.py · make_install_guide.py (developer tools, not shipped)
tests/e2e_test.py         end-to-end test (throw-away data and Fleet Manager server)
tests/security_test.py    security test
```

## Licence

Fleet Manager Pro is © 2026 Jeff Poblocki. All rights reserved. It is **free**: everyone uses it under the licence
agreement in `fmpro/EULA.txt` (shipped as **Licence Agreement.txt** and readable in the app at `/licence-agreement`,
linked from every page). The first start shows the agreement; nothing else opens until **I agree** is ticked, and
the time is recorded (setting `eula_accepted` and the audit log).

* No licence key and no activation; it works fully offline.
* Updates and the online features (Apply page, Global Leaderboard, VTC Finder) use the Fleet Manager server over a
  certificate-pinned connection; updates are signed with the server's Ed25519 key.
* Online features identify the company by its own random ID and secret (`online_id.py`, kept in the database), not
  by anything personal. Copies that were activated with a key before 1.5.29 hand their online data over once.
* **Settings → About** shows the version, when the agreement was accepted and the company's online ID.

## Roles

Owner › Manager › Dispatcher › Driver › Trainee. Managers run fleet, finance, recruitment and settings;
dispatchers post contracts, events and announcements; drivers claim contracts, log manual jobs (approved by a
manager) and RSVP to events. Only the owner takes loans or promotes managers.

## Security

* Passwords are hashed (Werkzeug scrypt), need at least 8 characters and can't be a common one or based on the
  username. Changing or resetting a password signs every other browser out; removed members lose access at once.
* Wrong passwords are counted per PC (IP address), not per account: 5 wrong tries block that PC for that account for
  5 minutes and 20 wrong tries block it altogether for a while, but nobody can lock someone else out of their account.
* Every form is CSRF-protected; the session cookie is HttpOnly + SameSite (and Secure over HTTPS); a
  Content-Security-Policy limits scripts, connections and form targets to this site.
* **LAN mode is encrypted.** `--lan` serves other PCs over HTTPS with a certificate made on first use (kept in the
  data folder). Plain HTTP from another PC is only ever answered with a redirect to https; this PC itself can keep
  using http://127.0.0.1. Browsers show a one-time warning for the self-made certificate; remote agents pin its
  fingerprint (`--pin`). `--lan --insecure-http` turns encryption off for networks you fully trust.
* Requests addressed to a foreign host name are refused (DNS-rebinding protection). Behind a reverse proxy with your
  own domain, list it in the `FMP_ALLOWED_HOSTS` environment variable (comma separated).
* **Telemetry is checked, and suspicious pay is held.** Frames from remote agents are rebuilt field by field. Every
  delivery is checked for position jumps without a ferry/train, an odometer that outruns the speedometer, a route
  finished faster than a truck could drive it, a drive the tracker barely saw, and revenue far above the offer. A
  delivery that fails any check is **not paid** (no money, no XP) until a manager approves it; Settings → Telemetry
  can hold every remote driver's delivery for approval. The server still can't see the game itself, so a patient
  cheat who fakes a plausible drive in real time is not caught: managers should glance at remote drivers' logs.
* Roles are enforced on the server for every action; maintenance costs, fleet and finance are manager-only; only the
  owner takes loans; managers can't edit, remove or read the agent
  token of anyone at or above their own rank.
* CSV exports neutralise spreadsheet formulas; all numbers are range-checked; the plugin installer only works from a
  browser on the server PC itself.
* **The public program is compiled** (`Build Pro Buyer Copy.py` → `Fleet Manager Pro.exe`, no Python source) and
  only installs updates signed by the Fleet Manager server.

## Tests

```
python tests\e2e_test.py
python tests\security_test.py
python tests\fuzz_test.py      # every page and form, every role, hostile input
```

It starts its own throw-away Fleet Manager server (needs the `Fleet Manager License Host` folder next to this one) and
never touches your data or your real server.

## Feature status — 1.3-alpha

| Page | Status |
|---|---|
| Dashboard | ✓ Complete and working |
| Live Cockpit | ✓ Complete and working |
| Dispatch (incl. Send to game) | ✓ Complete and working |
| Drivers | ✓ Complete and working |
| ELD & Compliance | ✓ Complete and working |
| Fleet Map | ✓ Complete and working |
| Telemetry Data | ✓ Complete and working |
| Finance | ✓ Complete and working |
| Fleet & Garages | ✓ Complete and working |
| Logbook | ✓ Complete and working |
| Expenses | ✓ Complete and working |
| Driver Performance | ✓ Complete and working |
| Recruitment (online Apply link, review, hire) | ✓ Complete and working |
| Events & Convoys (create, edit, RSVP, attendance XP) | ✓ Complete and working |
| Leaderboard (distance, revenue, deliveries, tonnage, score, XP, realism) | ✓ Complete and working |
| Truck Leasing | ✓ Complete and working |
| Achievements | ✓ Complete and working |
| Announcements | ✓ Complete and working |
| Messages | ✓ Complete and working |
| Notifications (bell menu: delete one or all) | ✓ Complete and working |
| Settings (economy values in your units) | Working |
| Everything else | Working, still being polished |

Complete pages carry a green ✓ in the sidebar.

## Photo credits

Real-life truck photos from [Unsplash](https://unsplash.com), free to use under the Unsplash License:
sign-in page — Josiah Farrow; Dashboard — Tom Jackson; Fleet & Garages — Nigel Tadyanehondo;
Dispatch — Rhys Moult; Drivers / Driver Performance — Bhargav Panchal.

## Recruitment — how drivers join

Fleet Manager Pro only listens on your own PC, so drivers apply on your **online Apply page**, which lives on the
Fleet Manager server (always online). Recruitment shows your link, e.g. `http://fleetmanager.servehttp.com:8750/apply/abcd2345`,
with a **Copy** button: share it on Discord, TruckersMP or your socials.

1. A driver opens the link and fills in name, contact (Discord / email), age, country, game, experience and a message.
2. Within about 10 minutes (or when you press **Check now**) the application appears on **Recruitment**; managers get a
   notification and, with a Discord webhook, a message. The server deletes it once it's collected.
3. **Accept** creates the driver's account (optionally in a division) — give them their username and password.
   **Interview** / **Reject** keep track of the rest.

Close or open applications in **Settings → Recruitment** (the online page follows). Protection: at most 5 applications
per address per hour, a hidden field that catches bots, length limits, and at most 300 waiting per company.

## Truck leasing

**Company → Truck Leasing** lets the company lease trucks instead of buying them. Four plans come built in, and
managers can edit them, add their own or hide them:

| Plan | Length | Miles included | Down | APR | End value | Extra miles | Ending early |
|---|---|---|---|---|---|---|---|
| Short Haul | 12 weeks | 25,000 | 5% | 7.9% | 75% | $0.25/mi | 40% of payments left |
| Regional | 26 weeks | 60,000 | 10% | 6.9% | 60% | $0.20/mi | 35% |
| Long Haul | 52 weeks | 125,000 | 10% | 5.9% | 45% | $0.18/mi | 30% |
| Owner-Operator | 104 weeks | 250,000 | 15% | 4.9% | 30% | $0.15/mi | 25% |

Weekly payment = (price − down payment − end value) ÷ weeks, plus interest of (price − down + end value) × APR ÷ 104.
Pick a dealer truck (or one you already own) and the page shows every plan side by side: down payment, weekly
payment split into truck value and interest, total paid, total interest and the buyout price. Every member sees the
costs; only Owners and Managers can sign, end or change leases.

* The down payment is taken when you sign; payments come out of the company account every week (like loans).
* Miles are counted from the truck's odometer. The lease page shows miles used, the pace you're on, the extra
  miles projected and what returning or buying the truck would cost today.
* At the end: **buy** it for the end value, **return** it (extra miles charged) or **renew** on any plan at its
  end value. Ending early costs the plan's fee on the payments left; buying early pays the rest off.
* Leased trucks carry a *leased* badge on Fleet and can't be sold; returned trucks leave the fleet.

## Release notes — 1.3-alpha

* **Copies built per buyer, with sealed pages**: each buyer copy carries a hidden build tag (from the purchase key)
  and its pages are encrypted; the licence server hands the key back inside the signed licence, wrapped to the
  PC. A cracked copy that skips the licence check shows no pages, and a leaked copy can be traced to its buyer
  (License Manager *10) Identify a copy*, *! LEAK?* in the list). New `Build Buyer Copy.bat` asks for everything.
* **Security clean-up**: Bandit reports no issues (SQL identifiers checked, errors logged, secure random,
  URL schemes checked); docstrings are stripped and page templates compiled into the buyer copies.
* **Starting capital locked at $275,000**: every new company (and a *Clear data* reset) starts with 275,000; the setup
  page shows it but it can't be changed. Existing companies keep their books as they are.
* **License Manager: delete purchases** (seller only): *8) Delete a purchase* (active or revoked) and *9) Delete ALL
  revoked purchases*, each confirmed by typing DELETE. A copy of every deleted purchase goes to deleted_purchases.jsonl.
* **Installation Guide for both versions**: a *Which version do you have?* table, start / update / data steps for the
  standard and the single-program (.exe) version, and a section on restoring and allowing Fleet Manager Pro if
  Microsoft Defender wrongly flags it (marked as temporary until the program is code-signed and cleared by Microsoft).
  The buyer readmes point to it.
* **Diesel and AdBlue** (`fmpro/fuel_prices.py`): the company pays for every fill-up. Diesel is charged at the game's own
  price in the state or country the truck is in (bundled; run `tools/build_fuel_prices.py` after a game update), or the
  company's price where there is none. The games have no AdBlue price, so it is set on Settings → Economy.
* **Settings in your units**: on Settings → Economy, diesel and AdBlue price, driver pay, XP per distance and weight, and the
  speeding tolerance show per gallon / mile / ton and in mph when your units are imperial (per litre / km / tonne
  and km/h for metric). Saving without changing a value keeps it exactly as it was.
* **Source code protection**: both buyer copies are now compiled to native machine code with **Nuitka** instead of
  Python bytecode (no-exe copy: the whole program is one `fmpro.*.pyd`; exe copy: a Nuitka standalone build), so
  there is no Python source or bytecode left to decompile.
* **Licence agreement and copyright**: buyers must tick *I agree to the licence agreement* to activate; the
  agreement ships with every copy and is linked from every page, with the notice *© 2026 Jeff Poblocki*.
* **Clear old notifications**: each notification in the bell menu has its own **×** delete button (the menu stays
  open), and **Delete all** (with a confirm) clears every notification you have. You can only ever delete your own.
* **Achievements in your game's units**: distance and weight goals and progress show in miles and pounds for
  ATS and kilometres and tonnes for ETS2 (also in the badge tips on driver profiles). The *1,000 km Club* badge
  is now called *Road Tested*; the goals themselves are unchanged.

* **Events & Convoys** and **Leaderboard** marked complete and working.
* **Notifications** marked complete and working (✓ in the bell menu).
* **Attendance XP is paid once**: saving attendance again, or unticking and re-ticking a driver, no longer pays
  the event's XP twice. Unticking a driver takes back exactly the XP they were given.
* **Event sign-ups are checked on the server**: no RSVPs for cancelled or completed events, and the event's
  minimum level is enforced (anyone can still say they're not going).
* A cancelled event can't be completed; completing, cancelling and deleting events are in the audit log.

First full release candidate: every feature is complete and tested.

* **Delete sold items**: sold and returned trucks and sold trailers can be deleted from the Fleet lists (one at a
  time, or all at once with **Delete sold**). Their jobs, maintenance, leases and ledger lines stay, unlinked, so
  the ledger totals don't change. Selling a garage already removes it from the list.
* **Fixed maintenance prices**: *Record maintenance* fills in the cost of a service, tyres, an inspection, an upgrade
  or a wash from the price list in Settings > Economy (defaults 800 / 2,400 / 150 / 0 / 40). The cost box is
  read-only and the server always charges the Settings price, whatever a form sends, so a price can only be
  changed in Settings (Owner and Managers). A **repair** costs the truck's wear x the repair rate (Settings >
  Economy, default 40 per 1%), and marking a pre/post-trip report **Repaired** costs a set price per defect
  (default 250) - neither can be typed in any more. The window also shows the truck's distance since its last
  service and what it has cost so far; drivers don't get the form.
* **Edit events & convoys**: managers and dispatchers can change every detail of an event (Edit on the event page,
  or on its card in Events & Convoys). Drivers who RSVP'd going or maybe are notified when the time, place or route
  changes; RSVPs, the host and the status are kept.
* **Messages**: a private message to a member who doesn't exist (or was removed) is refused instead of being posted
  in the company channel.
* Truck Leasing, Achievements, Announcements, Messages and Recruitment marked complete.
* **Fire driver**: replaces *Remove member*. Type the driver's name to confirm; the member and everything personal
  (messages, notifications, achievements, hours-of-service logs, inspections, event sign-ups) are deleted from the
  database. Their deliveries and pay move to a "Former driver" placeholder that can't sign in, so the books still
  add up; contracts they had taken reopen. Only the owner fires managers. Members removed with an older version
  can be deleted with **Delete N former member(s)** on the Drivers page (owner).

* **Deliveries during a restart**: a job delivered (or cancelled) while Fleet Manager Pro was restarting is now
  settled from the result the game keeps, even after the game's "delivered" signal has cleared, instead of being
  marked abandoned.
* **EU hours**: a reduced daily rest of 9 h or more (for example a long ferry crossing) now resets the daily
  driving and 13 h window clocks, as EU 561/2006 allows.
* **Roadside inspections** no longer fine a missing pre-trip DVIR a second time when the load start already did.
* **Truck leasing** (see *Truck leasing*): plans with different lengths, mileage allowances, prices and interest
  rates, a side-by-side cost calculator, weekly billing, mileage tracking and buy / return / renew.

* **Fleet & Garages** marked complete.
* **Two games, two open jobs**: a job left open in ATS or ETS2 now waits while you drive the other game and is
  picked up again when you return (before, starting a job in one game abandoned the other's). Switching games is
  no longer mistaken for a teleport by the anti-cheat.
* Telemetry Data opens on the signed-in driver.
* Online recruitment (see *Recruitment — how drivers join*) and fitted banner photos.
* The package is on the D: drive; `Source\` holds the entire source code: the app (with tests and tools), the
  licence server (without its keys or purchase records), the buyer build script, the classic Fleet Manager and the guides.

## Release notes — RC 0.03a

Full end-to-end test and security review of everything added in 0.02, with these fixes:

* **Expenses**: a date before the year 2000 crashed the save on Windows (server error) — it is now refused with a
  message. Editing / deleting on the Expenses page only works on hand-entered *expenses*; hand-entered income
  (e.g. a refund) can no longer be changed or removed there through a crafted request.
* **Clear all data**: after 5 wrong Owner passwords in 15 minutes it locks for 15 minutes (each attempt is in the
  audit log). If the database is busy when it compacts the file afterwards, the reset still finishes normally.
* **Price lists** on Add truck / Add trailer are built without raw HTML (defence in depth).
* New **`tests/fuzz_test.py`**: about 37,600 requests over every page and form, as Owner, Manager, Dispatcher,
  Driver and Trainee, with hostile input (huge numbers, NaN, script tags, SQL, path tricks, bad dates). Nothing may
  answer with a server error and no input may come back as live HTML.

* **Online recruitment**: drivers can now apply from anywhere — see *Recruitment — how drivers join*. Needs the
  updated `license_server.py` on the licence server.

Results: 297 end-to-end checks, 129 security checks and the robustness scan all pass.

## Release notes — 0.02 Alpha

* **Cargo damage on the books**: the game pays the offer minus cargo damage (or lateness). The offer is now booked as
  freight and the deduction as its own expense (*cargo damage* / *late delivery*), so the Logbook shows e.g.
  "$8,476 · −$512 damage" and the job page lists it under Costs. Earlier deliveries are split the same way once, on
  first start (the company balance doesn't change).
* **Expenses page**: every cost the company paid, filterable by period, category, truck, driver and source, with
  totals per category and CSV export. Managers can **add, edit and delete** expenses by hand (insurance, parking,
  permits, a repair paid outside the game...). Expenses from the game can't be deleted.
* **Driver Performance page**: distance driven per driver (in the game's units), loads delivered, **on time vs late**
  against the in-game deadline (or the contract deadline), on-time rate, score, revenue, damage and fines; click a
  driver for every load with its deadline, arrival time and how early / late it was.
* **Reset ELD & Compliance** (managers): clear the logbook, violations, DVIRs and/or roadside inspections for one
  driver or all, for ATS, ETS2 or both. Type RESET to confirm; truck holds caused by removed reports are lifted.
* **Clear all data** (Owner only, Settings → Clear data): deletes every job, ledger entry, contract, truck, garage,
  logbook line, event, message and learned map data; keeps the Owner account, settings and licence. Needs a ticked box,
  DELETE ALL DATA typed, the Owner's password and a final confirm; a full database backup is saved to
  `data\backups` first.
* **New look**: real truck photos (Unsplash) on the sign-in page and as banners on Dashboard, Fleet & Garages,
  Dispatch, Drivers and Driver Performance; cleaner cards, tables and buttons, and a top bar that stays in view.
* **Truck and trailer prices**: Add truck / Add trailer list every stock truck (19 ATS, 26 ETS2) and ownable
  trailer (32 ATS, 58 ETS2) with the game dealer's price (`tools/build_vehicles.py`); picking one fills in the
  brand, model and price. Sell truck / Sell trailer show what was paid, the dealer price and a suggested resale.
  Trailers can now be sold. Purchases the company can't afford are refused.
* **Trailer types**: Add trailer has a drop-down of every trailer body the games allow (23 ATS, 22 ETS2: dry van,
  reefer, flatbed, lowboy, tankers, car transporter...), chosen automatically when you pick a dealer trailer.
* **Clear demo jobs**: managers can remove everything the telemetry simulator made (its jobs, their ledger
  entries, XP and the simulator's truck) from the Logbook with one button. Real jobs are never touched.
* **Repair cost** per 1% of wear lowered from 180 to 40 (existing installs still on 180 are moved to 40).
* **Units**: Add / Edit truck and Log a job use the chosen units (mi / gal / lb for ATS, km / L / t for ETS2).
* **Garages**: buying a garage now offers a drop-down of every garage location in the chosen game (130 ATS, 204
  ETS2, read from the games' own saves by `tools/build_garages.py`); owned ones are marked. Garages can be **edited**
  (location, size, upkeep, notes) and **deleted** (optionally sold; trucks based there are kept, without a garage).

## Release notes — 0.01 RC

* **Licensing**: online activation with Fleet Manager Pro keys (FMP-…), 2 PCs per purchase, 7-day offline grace.
* **ELD & Compliance**: Hours of Service logbook (US / EU rules), DVIR pre/post-trip inspections, roadside
  inspections, dispatch holds.
* **Game selector**: each driver picks ATS, ETS2 or Both in the top bar; every page follows that game's units.
* **Send to game**: a dispatch contract becomes a real job in the game's freight market (new save slot, your saves untouched).
* **City, cargo and company dropdowns** on dispatch contracts, read from both games' own files (ETS2 lists now from the real game).
* **Fleet Map** with real geography, learned roads and places; **game filter** on all data pages; buyer
  **Installation Guide** PDF (`tools/make_install_guide.py`).
* **Hardening**: HTTPS in LAN mode, compiled buyer program, anti-cheat pay holds, per-PC login throttling,
  8-character password rule, manager-only maintenance costs.
* **Security review**: agent-telemetry sanitising, host-name check, safer redirects, session invalidation on password
  change, Content-Security-Policy, CSV formula protection, tighter manager/owner separation.
* **Fixes from live ATS testing**: deliveries are recognised even when the plugin leaves its event flags set; average
  speed comes from the speedometer (the game's odometer is map-scale); no tracking after delivery; phantom refuels on
  job/truck load are ignored; "game not running" when the game is closed but another app holds the telemetry memory;
  Steam libraries on any drive are found.

## Release notes — 0.001 RC

First release candidate. Known limits: the map draws world coordinates (no game map tiles); anti-cheat is
heuristic (impossible average/top speed, missing distance).
