# No-Node data app example

This is the runnable HED-4 workshop artifact. It uses FastAPI, Hedron, and HTMX server-side
interactions. It does not use Node.js, npm, a JavaScript bundler, or Streamlit.

## Run it

From the repository root:

```bash
python -m venv /tmp/data-mover-no-node
source /tmp/data-mover-no-node/bin/activate
python -m pip install -e .
cd examples/no_node_data_app
python app.py
```

Open <http://127.0.0.1:8770>. Submit the form and observe the `#preview` fragment update without a
full-page navigation. The same endpoint also works as a normal HTML form POST.

## What to study

- `app.py` defines a page and an action with Python components.
- The form uses `hx-post`, `hx-target`, and `hx-swap` attributes; no client build is required.
- Validation runs on the server and the response is an accessible Hedron `Alert`.
- The pattern is intentionally small; add persistence and CSRF before adapting it to real data.

See [the workshop guide](../../docs/plans/ade-no-node-workshop.md) for exercises.
