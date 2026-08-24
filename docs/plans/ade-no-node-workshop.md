# ADE workshop: build a data app without Node.js or Streamlit

This workshop uses the runnable example in `examples/no_node_data_app/`.

## Setup

```bash
cd examples/no_node_data_app
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ../..
python app.py
```

Open `http://127.0.0.1:8770`. The page is rendered by FastAPI and Hedron; HTMX is delivered as a
browser asset and there is no npm install or Node process.

## Guided tasks

1. Submit the source and destination names.
2. Observe the server-rendered preview fragment.
3. Change the row limit and submit again.
4. Add a validation error for an empty source.
5. Add a test for the fragment response.

## Extension challenge

Add a persisted run record and replace the preview response with a polling status region. Follow the
patterns in [data-mover-shell-patterns.md](data-mover-shell-patterns.md), especially CSRF and target
allowlisting.
