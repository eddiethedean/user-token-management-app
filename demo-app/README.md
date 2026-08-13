# Posit FastAPI demo

This is a deliberately small deployment-confidence check for Posit Workbench and Posit Connect.
It proves that the platform can:

1. create a Python environment and install dependencies;
2. start a FastAPI ASGI application;
3. serve HTML and JSON routes; and
4. preserve links and browser requests beneath the platform's application URL prefix.

It has no database, authentication, email, Hedron, or HTMX. Those belong to the full Data Mover app and
can be tested after this baseline succeeds.

The deployment bundle deliberately targets **Python 3.11.x**, matching the intended Posit Connect
environment. Check `python3 --version` before creating the local virtual environment so the local
and Connect runtimes stay comparable.

## Files

```text
demo-app/
├── app.py            FastAPI app and URL-prefix handling
├── start.py          local/Workbench-aware server launcher
├── requirements.txt Connect runtime dependencies
├── requirements-dev.txt
├── pyproject.toml    supported Python version range
├── tests/            route, prefix, and launcher tests
└── README.md
```

## 1. Start it locally

From the repository root:

```bash
cd demo-app
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python start.py
```

Open <http://127.0.0.1:8000>. A successful check has all of these results:

- the page says **The FastAPI demo is running**;
- **Call the JSON endpoint** returns a payload with `"status": "ok"`;
- **About this check** opens and its return link works; and
- <http://127.0.0.1:8000/health> returns `{"status":"ok"}`.

Stop the server with `Ctrl+C`.

## Run the tests

The test suite exercises every route, simulated Connect and Workbench prefixes, unsafe-prefix
fallback, `rserver-url` parsing and errors, and the arguments passed to Uvicorn:

```bash
cd demo-app
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
pytest
```

The repository-level `make check` command also runs this suite in CI.

## 2. Start it in Posit Workbench

Open a terminal inside your Workbench session and run the same setup:

```bash
cd /path/to/user-token-management-app/demo-app
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python start.py
```

`start.py` uses Workbench's `UVICORN_ROOT_PATH` for the default port when available. Otherwise, it
detects Workbench through `RS_SERVER_URL`, asks the `rserver-url` utility for the session-specific
proxy URL, and supplies its path to Uvicorn. Open the printed **Posit Workbench URL**. In VS Code or
Positron sessions, the server should also appear in the **Proxied Servers** view.

Do not copy or hard-code the printed path: it changes with the Workbench session and port.

To use another port:

```bash
DEMO_PORT=8050 python start.py
```

Repeat the four checks from the local section. On the home page, **Detected application base
path** should now show a Workbench path rather than `(none)`.

## 3. Deploy it to Posit Connect

Use a virtual environment where the demo already runs locally. Install the publisher CLI (it is a
deployment tool, so it is intentionally not in the app's runtime `requirements.txt`):

```bash
cd /path/to/user-token-management-app/demo-app
source .venv/bin/activate
python -m pip install rsconnect-python
```

Register your Connect server once. Create an API key in your Connect account, keep it out of shell
history, and replace the example server URL and nickname:

```bash
export CONNECT_API_KEY='your-api-key'
rsconnect add \
  --server https://connect.example.com/ \
  --name my-connect \
  --api-key "$CONNECT_API_KEY"
unset CONNECT_API_KEY
```

Deploy **this directory**, not the parent Data Mover repository:

```bash
rsconnect deploy fastapi \
  --name my-connect \
  --title "FastAPI deployment check" \
  --entrypoint app:app \
  --exclude tests \
  --exclude requirements-dev.txt \
  ./
```

The exclusions keep tests and development-only dependencies out of the runtime bundle. Connect
still receives `app.py`, `requirements.txt`, `pyproject.toml`, and the declared Python version
range it needs to reconstruct and start the application.

Connect prints the content URL after a successful deployment. Open it and repeat the four checks
from the local section. The home page's detected base path should show the Connect content path,
and the JSON button should still work.

The Connect process imports `app:app` directly. It does not run `start.py`; Connect owns the ASGI
server and supplies the application base URL at request time.

## What success tells us

| Result | What it verifies |
|---|---|
| Home page loads | Python, dependency restore, ASGI import, and HTML responses work |
| JSON button works | Browser requests retain the Connect/Workbench URL prefix |
| About and return links work | Server-generated navigation retains the prefix |
| `/health` works | Direct route access works through the proxy |

A complete pass means the platform foundation for Data Mover is viable. It does not yet prove
PostgreSQL access, persistent storage, authentication headers, secure cookies, SMTP, encrypted
connection storage, CSV uploads, pipeline persistence, background workers, or the Hedron production
build; those are the next integration layers.

## Troubleshooting

### Workbench says `rserver-url` is missing

The normal binary is `/usr/lib/rstudio-server/bin/rserver-url`. If your installation puts it
elsewhere, point the launcher to it:

```bash
RSERVER_URL_BIN=/custom/path/rserver-url python start.py
```

If the utility is unavailable, ask the Workbench administrator whether local web-server proxying
is enabled and where `rserver-url` is installed.

### The Workbench page loads but links or the JSON button return 404

Confirm that you used `python start.py`, not plain `uvicorn app:app`. The launcher supplies the
dynamic Workbench root path to Uvicorn. Restart it and use the newly printed URL.

### Connect cannot find `app`

Run the deployment command from inside `demo-app` and retain `--entrypoint app:app`. The first
`app` is `app.py`; the second is the FastAPI object inside it.

### Connect cannot install a package or match Python

Check the deployment log and confirm that Connect exposes a Python 3.11 runtime and can reach its
configured Python package repository. This demo declares Python `>=3.11,<3.12` and lists only
FastAPI and Uvicorn as runtime dependencies.

### Connect deploys successfully but access is denied

Open the content settings in Connect and grant yourself or the intended viewers access. This is a
content permission issue rather than an application startup issue.

## Official references

- [Posit Connect: FastAPI](https://docs.posit.co/connect/user/fastapi/)
- [Posit Connect: publishing from the command line](https://docs.posit.co/connect/user/publishing-cli/)
- [Posit Workbench: proxying web servers](https://docs.posit.co/ide/server-pro/user/vs-code/guide/proxying-web-servers.html)
