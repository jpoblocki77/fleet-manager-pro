# Shipping an update to buyers

Fleet Manager Pro now has an in-app updater. Each running copy asks this licence server once a day (and at
start-up) whether a newer build exists **for that exact copy**. The server answers with an **Ed25519-signed
manifest** (version + SHA-256 + filename), signed with `host_key.json` — the same key that signs licences. The
buyer sees an **"Update available"** banner and clicks **Update now**; the app downloads the package over the
pinned HTTPS channel, checks it against the signed checksum, swaps the files in (keeping their `data` folder) and
relaunches. Nothing installs on its own — it is notify + one click.

Because every buyer copy has its **own sealed pages**, an update is still a per-buyer build: you rebuild that
buyer's copy at the new version and drop it in their folder here.

## One-time: nothing

The updates live in `updates\` next to `license_server.py` on the cloud box (reached over AnyDesk / Remote
Utilities). The folder is created for you the first time you publish. The server already reaches buyers as
`fleetmanager.servehttp.com:8750`, so there is **no new port to open**.

## The easy way: one command for every buyer

Buyers never get a file resent to them — you publish once to this server and their apps download it themselves.
To push a new version to **all** buyers at once:

1. Bump `Fleet Manager Pro\VERSION` (e.g. `1.3` → `1.4`).
2. Make sure `license_server.py` is running.
3. Double-click **`Build and Publish Update (All Buyers).bat`** in the Development folder.

It rebuilds every active buyer's sealed `.exe` copy (the buyer list comes from the licence server) and publishes
each one here automatically. Each buyer then sees "Update available" and installs with one click. `--keys FMP-… FMP-…`
limits it to certain buyers; `--stock` also refreshes a stock copy; `--dry-run` just lists who would be updated.

## Doing one buyer by hand

1. **Bump the version.** Edit `Fleet Manager Pro\VERSION` (e.g. `1.3` → `1.4`). The server only offers a build
   whose version is **higher** than what the buyer is running, so this step is what makes an update "new".

2. **Build the buyer's copy** as usual — `Build Buyer Copy.bat`, entering that buyer's purchase key. You get a zip
   in `Build Output`, and the build prints the buyer's **build tag** (`FMPB-…`).

3. **Publish it to this server.** From the `Fleet Manager License Host` folder:

   ```
   python publish_update.py --zip "....\Build Output\For Buyer - FMP-XXXX-XXXX-XXXX-XXXX.zip" --key FMP-XXXX-XXXX-XXXX-XXXX
   ```

   - The no-exe build is retired, but the server still serves `Fleet Manager Pro <ver> (no exe).zip` packages
     (published with `--flavor noexe`) to any old no-exe copy, so those keep working if you ever need to.
   - For a **stock** copy: `--stock`.
   - You can also target by build tag: `--tag FMPB-XXXX-XXXX-XXXX-XXXX`.
   - Add short release notes the buyer sees: `--notes "Adds X, fixes Y."`

   The version is read from the `VERSION` file inside the package. `publish_update.py` copies it into
   `updates\<BUILD TAG>\` and removes the previous package **of that flavour** there.

4. **Make sure `license_server.py` is running** on this PC. That's it — the buyer's copy offers the update at its
   next daily check or restart.

## How a buyer updates

- A banner appears at the top of the app, and **Settings → Licence → Updates** shows the same thing with a
  **Check for updates** button.
- They click **Update now**. The app downloads, verifies the signed checksum, closes, swaps the new files in
  (their `data` folder is never touched) and reopens automatically.

## Safety notes

- The manifest and the download are both verified against the key baked into each copy, so a broken or hostile
  server can't push a tampered build — a bad signature or checksum is refused and nothing is changed.
- A package is only ever offered to the copy it was built for (folders are keyed by build tag), so one buyer's
  build is never served to another.
- Keep `host_key.json` private. It signs licences **and** updates; anyone with it could issue either.
