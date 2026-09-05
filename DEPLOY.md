# Deploying

## Streamlit Community Cloud (free, recommended)

1. Create a GitHub repo and push this folder:

   ```bash
   cd ~/SeasonlityTool
   git remote add origin https://github.com/<you>/seasonality-explorer.git
   git branch -M main
   git push -u origin main
   ```

2. Go to https://share.streamlit.io, sign in with GitHub, click **Create app**,
   pick the repo, branch `main`, main file `app.py`, and deploy.

Community Cloud installs `requirements.txt` automatically and picks up
`.streamlit/config.toml` for the theme. Apps get up to ~2.7 GB RAM and 2 CPU
cores; an app with no traffic for 12 hours sleeps and anyone who opens it can
wake it with one click.

**Python version:** choose 3.11 or 3.12 in the deploy dialog's *Advanced
settings*. Community Cloud only supports Python versions still receiving
security updates.

## Render (free web service)

New → Web Service → connect the repo, then either use the Dockerfile
(Runtime: Docker) or the native Python runtime with:

- Build command: `pip install -r requirements.txt`
- Start command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`

The free instance type spins down after 15 minutes with no traffic and takes
roughly a minute to come back; each workspace gets 750 free instance-hours per
calendar month.

## Google Cloud Run (free tier, no sleeping penalty)

```bash
gcloud run deploy seasonality --source . --region us-central1 --allow-unauthenticated
```

Cloud Run builds the Dockerfile and injects `$PORT`. Needs a billing account on
file even while inside the free tier.

## Run the container locally

```bash
docker build -t seasonality .
docker run --rm -p 8501:8501 seasonality
```

## Things to know on any free host

- **The price cache is ephemeral.** `data/cache/` lives on disk that is wiped on
  every restart and redeploy, so the first page load after a cold start
  re-downloads from Yahoo (a few seconds). Within a session `@st.cache_data`
  covers it.
- **Yahoo occasionally rate-limits shared cloud IPs.** `load_history` falls back
  to whatever is cached when a download fails, so a rate-limited app serves
  slightly stale data instead of erroring — but on a cold start with an empty
  cache there is nothing to fall back to. If you hit this often, commit a seed
  parquet for your most-used tickers, or lengthen `max_age_hours`.
- **Don't put secrets in the repo.** There are none today (Yahoo needs no key);
  `.streamlit/secrets.toml` is gitignored if you ever add any.
