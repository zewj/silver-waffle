# Multi Roblox Manager

A Windows desktop tool that lets you run several Roblox clients side-by-side,
each signed in to a different account, with one-click focus switching and
server hopping that doesn't trip the "you are joining too quickly" errors.

> Windows only. Roblox's client is Windows-native; the techniques here
> (Win32 mutex hold, DPAPI, `RobloxPlayerBeta.exe` direct exec) don't have
> macOS or Linux equivalents.

## Features

- **Multiple instances**: holds the `ROBLOX_singletonEvent` named mutex for
  the lifetime of the app so each new launch bypasses the "Roblox is
  already running" exit path.
- **Multi-account, properly**: each instance authenticates with its own
  one-shot ticket minted from a saved `.ROBLOSECURITY` cookie. Different
  instances run as different accounts at the same time, regardless of
  which account the Roblox launcher is signed into.
- **Stable launches via the official launcher (default)**: opens a
  `roblox-player:1+launchmode:play+gameinfo:<ticket>+placelauncherurl:…`
  URL so `RobloxPlayerLauncher.exe` runs updates / sets up Hyperion's
  expected parent process, then spawns `RobloxPlayerBeta.exe` itself with
  the same ticket. Multi-account still holds — the ticket carries the
  identity. A **Direct** mode that runs `RobloxPlayerBeta.exe` itself is
  available for users who don't want the launcher in the loop, and the
  protocol path auto-falls-back to direct exec if it stalls.
- **Persistent presets**: launch configurations (label + place + account)
  are saved to disk and survive restarts. "Launch All" boots them in one
  click, spaced to avoid join throttling.
- **Encrypted cookie storage**: cookies are encrypted with DPAPI
  (`CryptProtectData`) so the on-disk file is only decryptable by your
  Windows user account.
- **Focus + cycle**: per-instance focus button, plus `Ctrl+Tab` to cycle.
  Uses `AttachThreadInput` + `SetForegroundWindow` to defeat focus-stealing
  prevention.
- **Overlap server hop**: a hop brings up the replacement client *first*,
  then kills the old PID once the new window appears — so the visible gap
  is just the new client's load screen rather than a close-and-reopen.

## Requirements

- Windows 10 or 11
- Roblox installed (the manager auto-discovers
  `%LOCALAPPDATA%\Roblox\Versions\version-*\RobloxPlayerBeta.exe`)
- Python 3.10+

## Setup

```powershell
git clone <this repo>
cd silver-waffle
py -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Adding accounts

Two ways:

### Sign in with Browser (recommended — supports password, MFA, QR, passkey)

1. Click **Accounts…** → **Sign in with Browser…**
2. An embedded Roblox login window opens. Use *any* of Roblox's official
   sign-in methods:
   - Username + password (+ MFA if enabled)
   - **QR code** ("Sign in with another device" on Roblox's login page —
     scan with the Roblox mobile app on a signed-in phone)
   - Passkey
3. Once Roblox redirects you to the home page, the window closes and the
   harvested `.ROBLOSECURITY` cookie is DPAPI-encrypted and saved.

This needs `pywebview` (in `requirements.txt`); WebView2 runtime is
preinstalled on Windows 10/11.

### Paste cookie (no extra deps)

1. Click **Accounts…**.
2. In a logged-in browser, open DevTools → Application → Cookies →
   `https://www.roblox.com`, copy the `.ROBLOSECURITY` value.
3. Paste it into the cookie box, give it a nickname, click **Add / Update**.

Either way the cookie is validated against
`users.roblox.com/v1/users/authenticated` before being saved to
`%APPDATA%\MultiRobloxManager\accounts.json`. Cookies expire eventually —
when they do, the launcher surfaces an `Auth ticket refused` error and
you just re-run **Sign in with Browser…** (or paste a fresh cookie).

> Keep cookies private. Anyone with your `.ROBLOSECURITY` value can log in
> as you. The DPAPI wrapper means the on-disk blob can only be decrypted
> by your Windows user on your machine.

## Launching instances

1. Paste a `placeId` (e.g. `920587237`) or a full Roblox game URL
   (`https://www.roblox.com/games/920587237/Adopt-Me`).
2. Pick an account from the dropdown (or leave it on the signed-in
   fallback).
3. Optional **Label** to make instances easy to tell apart in the table.
4. **Launch**. Repeat for as many clients as you want.

Use **Save Preset** to remember a (label, place, account) combo. Presets
show up in the right-hand list and can be launched individually, all at
once, or removed.

## Server hopping

Select a running instance and click **Server Hop**:

1. Queries `games.roblox.com/v1/games/{place}/servers/Public`.
2. Picks a non-full public server whose jobId isn't in the last 10 you
   were in (avoids rejoining the same instance you just left).
3. Spawns a replacement `RobloxPlayerBeta.exe` for the same account into
   that jobId.
4. Waits for the new window to appear, **then** kills the previous PID.

Launches across the whole app are spaced by `launch_cooldown` (default
2.5s) to stay under Roblox's per-account join rate limit. Tune it in
`%APPDATA%\MultiRobloxManager\config.json` if needed.

## How "no errors" is actually achieved

| Error people see                          | What the manager does to avoid it                                                              |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------- |
| "Roblox is already running"               | Holds `ROBLOX_singletonEvent` so the second client skips that branch.                          |
| Rate-limit / "joining too quickly" / 729  | `LAUNCH_COOLDOWN` between any two launches; recent-jobId blocklist when hopping.               |
| "Authentication failed" on extra clients  | Per-account auth ticket → `gameinfo:<TICKET>` in the protocol URL; the launcher's signed-in account is irrelevant. |
| Close-and-reopen flash on hop             | Overlap hop: new client up before old PID is killed.                                           |
| Hyperion / launcher-update flakiness      | Default `protocol` mode goes through `RobloxPlayerLauncher.exe`; if it stalls, the manager refetches a ticket and falls back to direct exec automatically. |

## File layout

```
main.py
multi_roblox/
  accounts.py        # AccountStore (DPAPI-encrypted cookies)
  auth.py            # CSRF + authentication-ticket exchange
  browser_login.py   # Embedded webview sign-in (password / QR / passkey)
  config.py          # Persistent presets / recent places / cooldown
  dpapi.py           # CryptProtectData / CryptUnprotectData wrappers
  gui.py             # Tkinter GUI
  launcher.py        # Find RobloxPlayerBeta.exe; spawn with -t/-j
  manager.py         # InstanceManager — mutex, launch, focus, hop
  mutex.py           # SingletonMutex (ROBLOX_singletonEvent)
  servers.py         # Public server list, jobId picker, protocol URI builder
  windows.py         # Win32 helpers: PID list, window find/focus, kill
```

## Data locations

- `%APPDATA%\MultiRobloxManager\accounts.json` — accounts (cookies
  encrypted).
- `%APPDATA%\MultiRobloxManager\config.json` — presets, recent places,
  launch cooldown.

## Use responsibly

This tool is for managing your own accounts. Don't use it to violate
Roblox's Terms of Service. Auth tickets minted from a stolen cookie are
still a stolen account.
