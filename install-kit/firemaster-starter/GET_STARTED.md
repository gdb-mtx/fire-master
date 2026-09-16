# Get Started with FIREMaster

FIREMaster runs entirely on your own computer. Your financial data never leaves your
machine. This starter kit launches the app from prebuilt images — no coding, no GitHub
account, nothing to compile.

Don't take that on faith: the full source is public at
[github.com/gdb-mtx/fire-master](https://github.com/gdb-mtx/fire-master), and the images
this kit pulls are built from it by public GitHub Actions — so you (or your favorite AI)
can audit every line and every update before running it. Free for personal use.

**Total time: about 5 minutes** (most of it is installing Docker).

---

## 1. Install Docker Desktop

Docker is the free tool that runs FIREMaster on your machine.

- **Mac:** Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/),
  drag it to Applications, and launch it. Wait until the whale icon in your menu bar stops animating.
- **Windows:** Download the installer from the same page, run it, and reboot once when asked
  (this sets up "WSL2", which Docker needs). Launch Docker Desktop and wait for it to say
  **"Engine running"**. If it instead shows **"WSL not installed"**: open PowerShell **as
  administrator** (right-click → Run as administrator), run `wsl --install --no-distribution`,
  reboot, and launch Docker Desktop again — it happens on some fresh Windows machines.
  The first time the app starts, Windows Firewall may ask about **"Docker Desktop Backend"** —
  click **Allow**. That's Docker's standard networking prompt; everything still runs only on
  your machine.

You only ever do this once.

---

## 2. Open a terminal in this folder

Unzip this kit somewhere easy to find (e.g. your Desktop), then:

- **Mac:** Right-click the `firemaster-starter` folder → **New Terminal at Folder**.
- **Windows:** Open the folder, click the address bar, type `powershell`, and press Enter.

---

## 3. Set your password (first run only)

Paste this and press Enter. It asks you to choose an admin password for the app:

```
docker compose run --rm --no-deps backend uv run python -m app.setup
```

---

## 4. Start FIREMaster

```
docker compose up
```

Only this computer can reach the dashboard and API (they listen on `127.0.0.1`), and Postgres and
Redis never leave Docker's private network. Another device on your wifi sees nothing unless you
edit the Compose file on purpose. FIREMaster is not built to face the internet.

The first time, this downloads the app (~30–60 seconds). When you see the logs settle and
`migrate` say it finished, you're ready.

> **Tip — getting your terminal back.** `docker compose up` runs in the foreground and streams
> logs. Docker shows a small menu at the bottom of the window:
> - Press **`d`** to **detach** — the app keeps running, and you get your terminal back.
> - Press **Ctrl+C** to **stop** the app.
>
> (You can also skip the foreground entirely and start detached from the get-go with
> `docker compose up -d`.)

---

## 5. Open the app

Go to **http://localhost:5173** in your browser and log in with the password you just set.

You'll land in a fully populated demo: dashboard, retirement projections, runway, scenarios —
all driven by example data so you can explore everything before connecting anything real.

---

## Using your own data (connect Monarch)

The demo is great for exploring, but when you're ready to track your **real** finances, connect your
Monarch Money account — one command, your data stays on your machine. See **CONNECT_MONARCH.md** in
this folder. The demo is replaced by your real accounts automatically on the first sync.

---

## Supercharge it with Claude Code (optional, but this is the point)

The web app *shows* you your finances. **Claude Code** — an AI agent in your editor — *answers
questions* and *sets things up for you*, working directly against FIREMaster's API on your machine.
This is the feature the whole app exists to serve.

**Setup — pick ONE of these two ways** (about a minute; both need a
[claude.ai](https://claude.ai/) Pro or Max plan):

**Option A — VS Code** *(recommended if you like a visual editor)*
1. Install [VS Code](https://code.visualstudio.com/) and its **Claude Code extension** from the
   marketplace.
2. **Open this `firemaster-starter` folder in VS Code.** It ships a `CLAUDE.md` that teaches the
   agent your API automatically — endpoints, auth, the data gotchas. No setup beyond opening it.
3. Open the Claude panel (Claude icon in the sidebar). You get chat alongside clickable files
   and rendered reports.
4. **Worth 30 seconds:** set markdown files to open in preview by default — Claude's reports
   read far better rendered. `Cmd/Ctrl+,` → search "editor associations" → add `*.md` →
   `vscode.markdown.preview.editor`, or drop this into `settings.json`:
   ```json
   "workbench.editorAssociations": { "*.md": "vscode.markdown.preview.editor" }
   ```

**Option B — plain terminal** *(no VS Code needed)*
1. Install the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code).
2. Open a terminal **in this folder** and run `claude`. Same agent, same `CLAUDE.md` context —
   you just read its reports in whatever editor you like.

**First message, either way:**
   > *Authenticate to the FIREMaster API (ask me for the admin password — don't save it
   > anywhere), then pull my wealth projection and orient yourself.*

(The API it talks to is localhost-only and password-gated; the interactive API explorer at
`http://localhost:8000/docs` is the same surface Claude discovers.)

**Things to ask it** (it reads `CLAUDE.md` and does the API work for you):

- **Enrich your accounts** — categorize them for better projections:
  > *List my accounts, then help me set each one's role (taxable, retirement, cash, etc.) and any
  > target balances. Ask me anything you need.*

- **Build a what-if scenario** — no JSON wrangling:
  > *Create a scenario where I retire two years earlier and cut spending 15%. Read my current
  > config first, build the overrides, save it, activate it, and show me the impact.*

- **Set up a property** (if you own one) — record + classification rules:
  > *I own a rental called Cedar Duplex worth ~$450k with a $280k mortgage. Add it, create rules so
  > Home Depot and my property manager get classified to it, and reclassify my transactions.*

- **Get a report** anytime:
  > *Write me a one-page markdown report on my retirement runway and the three biggest risks.*

- **Stress-test the plan** — the question a dashboard can't answer:
  > *Pull the wealth projection. Which month does cash bottom out, what's driving it, and
  > what's the cheapest fix — a spending cut, an earlier property sale, or starting withdrawals
  > sooner? Quantify each option.*

**Three ground rules that work well:**
- Let Claude **read freely, but have it show you any change** (POST/PATCH payload) before
  sending — it asks permission by default.
- Ask for **reports as markdown files** in a `reports/` folder — they accumulate into a
  personal financial-review archive instead of scrolling away in chat.
- **Never paste secrets.** Claude only needs the admin password at login; your Monarch
  credentials never enter the conversation.

Everything stays local — Claude talks to `localhost:8000`, your data never leaves your machine.

---

## Everyday use

| What | Command (run in this folder) |
|------|------------------------------|
| Start | `docker compose up` (add `-d` to run in the background) |
| Stop | `docker compose down` — your data is kept |
| Update to the latest version | `docker compose pull` then `docker compose up -d` |

In a foreground session, press **`d`** to detach (app keeps running) or **Ctrl+C** to stop it.

---

## Troubleshooting

- **Docker Desktop says "WSL not installed" (Windows)** — run `wsl --install --no-distribution`
  in an administrator PowerShell, reboot, and launch Docker Desktop again.
- **"Cannot connect to the Docker daemon"** — Docker Desktop isn't running. Launch it, wait for
  the engine to start, try again.
- **"port is already allocated" / 5173 in use** — something else is using that port. Stop it,
  or ask support how to change the port.
- **The page won't load** — give it a few more seconds on first run while images download, then
  refresh `http://localhost:5173`.
- **Anything else — ask Claude Code.** If you set it up above, just describe the problem
  ("the app won't start, here's what the terminal says") — it can read the logs, diagnose,
  and walk you through the fix. This beats any FAQ we could write.

Questions? Reply to your welcome email and we'll help.
