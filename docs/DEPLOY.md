# Deploy the always-on viewer (Streamlit Community Cloud)

The public site is a **read-only viewer** (`DESK_VIEWER_MODE=1`): it only displays the decision
data your laptop generates. It runs **no agents**, needs **no `ANTHROPIC_API_KEY`**, and spends
**nothing**. Your laptop stays the private "engine"; the cloud is the always-on "window."

```
Your laptop (private)                         Streamlit Cloud (public, always-on)
  run cycles  → writes runs/*.json  → ./sync.sh (git push) →  read-only viewer
  (spends API)                                                (free, no API key)
```

## One-time setup

### 1. Create a GitHub repo and push
The local repo is already initialized and committed on `main`. Create an empty GitHub repo
named e.g. `algo-desk` (no README/license), then:

```bash
cd "/Users/yfrimpong/AI/Algorithmic Trading/algo-desk"
git remote add origin https://github.com/<your-username>/algo-desk.git
git push -u origin main
```

> Private repo is fine — Streamlit Cloud can deploy from private repos on your account.

### 2. Deploy on Streamlit Community Cloud
1. Go to **https://share.streamlit.io** and sign in with GitHub.
2. **Create app → Deploy a public app from GitHub.**
3. Set:
   - **Repository:** `<your-username>/algo-desk`
   - **Branch:** `main`
   - **Main file path:** `app/streamlit_app.py`
4. Open **Advanced settings → Secrets** and paste:
   ```toml
   DESK_VIEWER_MODE = "1"
   # Optional: require a password even to view (omit for an open demo link)
   # DESK_SHARE_PASSWORD = "some-password"
   ```
5. Click **Deploy**. In ~2 minutes you get a public URL like
   `https://<your-app>.streamlit.app` — share it with testers.

That URL is always on (it sleeps after long inactivity and wakes on the next visit) and works
when your laptop is off.

## Daily use

```bash
# 1) Run cycles locally (this is where the agents + your API key run):
.venv/bin/python -m src.orchestrator            # one cycle over the universe
#    or run the GUI in control mode and click "Run decision cycle now"

# 2) Push the fresh results to the public viewer:
./sync.sh
```
The cloud viewer auto-redeploys within ~1 minute and shows the new decisions, equity curve, and fills.

## Notes & guardrails
- **No secrets leak:** `.env` and `.venv/` are gitignored; the cloud has no API key.
- **Viewer is read-only:** run-cycle, approvals, mode toggle, and kill-switch are all hidden when
  `DESK_VIEWER_MODE=1`.
- **requirements:** Streamlit Cloud installs `requirements.txt`. The Claude SDK installs but is
  never imported on the viewer path. (A lean `requirements-viewer.txt` exists if you later want a
  separate deploy branch with a smaller install.)
- **Switching a deploy to control mode is intentionally not supported** — never expose the
  run/approve controls publicly; drive the desk only from your laptop.
