# Deploying to Streamlit Community Cloud

The app is free to host. It reads the parquet files committed in
`data/processed/`, so the cloud app needs no network access to NCDEX.

The GitHub repository will be **public**. Before pushing, read the note on your
commit email below.

## Before you push: your commit email will be public

Every commit in this repository carries the name and email from your git
configuration. Run this to see what will be published:

```powershell
git log --format="%an <%ae>" | Sort-Object -Unique
```

If you are happy for that address to be visible, skip to step 1. If not, switch
to GitHub's private address and rewrite the existing commits before the first
push (safe now, because nothing has been pushed yet):

```powershell
# Find your address at https://github.com/settings/emails ("Keep my email addresses private")
git config user.name  "Your Name"
git config user.email "12345678+your-username@users.noreply.github.com"
git rebase --root --exec "git commit --amend --reset-author --no-edit"
```

Nothing else personal is in the repository: no secrets, tokens, local paths or
virtual-environment files are committed (checked before this guide was written).

## 1. Create the GitHub repository

1. Go to <https://github.com/new>.
2. Repository name: `arb-dashboard` (any name works).
3. Visibility: **Public**.
4. Do **not** add a README, .gitignore or licence; the project already has them.
5. Click **Create repository**.

## 2. Push the code

From the project folder, replacing `YOUR-USERNAME`:

```powershell
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/arb-dashboard.git
git push -u origin main
```

Git will open a browser window to sign in to GitHub the first time.

## 3. Create the app

1. Go to <https://share.streamlit.io> and sign in with GitHub.
2. Click **Create app**, then **Deploy a public app from GitHub**.
3. Repository: `YOUR-USERNAME/arb-dashboard`. Branch: `main`. Main file path: `app/app.py`.
4. Open **Advanced settings** and set Python version to **3.13** (the version
   the app was tested on). If the build fails, choose 3.12 and redeploy.
5. Click **Deploy**. The first build takes a few minutes.

The app installs only `requirements.txt` (five packages). The theme comes from
`.streamlit/config.toml`.

## 4. Record the link

Copy the app URL (it looks like `https://something.streamlit.app`) into the
"Live app" line at the top of `README.md`, then commit and push.

Add `?commodity=GUARSEED` to the URL to open directly on guar seed.

## Refreshing the data before the interview

Run this the evening before, once NCDEX has published the day's bhavcopy, and again on the morning if you want the previous day included.

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt     # first time only
python -m src.pipeline --update         # fetch new trading days, rebuild the tables
python -m src.build_metrics             # rebuild the metric tables and the Phase 3 report
python -m pytest                        # all tests should pass (takes a minute or two)
git add -A
git commit -m "Refresh data"
git push
```

Streamlit Cloud redeploys automatically within a minute or two of the push.
Check the "Latest trading day" line under the title to confirm the new date.

Things to know:

- The header, insights and every number in `reports/interview_notes.md` are as
  of 9 Oct 2026. After a refresh the dashboard updates itself; the notes do not,
  so re-read the header before quoting figures.
- FBIL publishes the T-bill curve about a week late. Recent days show "RBI fill"
  until it catches up.
- NCDEX drops connections after bursts of requests. The pipeline waits five
  seconds between downloads and retries; a few new days take under a minute.
- A sleeping app (no visitors for some days) takes about 30 seconds to wake.
  Open it a few minutes before you present.

## Running locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app/app.py
```
