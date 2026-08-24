# Data Mover shell patterns (HED-3)

This is the reusable reference for Pipeline and future ADE pages. It records the patterns already
used by Access Registry/Data Mover, with concrete repository anchors.

## Authenticated shell

Use the application shell only after the `Auth` dependency resolves an approved user. Keep the
authentication/session owner in the application so refresh rotation, revocation, and security
version invalidation remain in one place.

```python
return await render_authenticated_view(
    request,
    body=page_body,
    auth=auth,
    settings=settings,
    page_title="Pipeline",
    csrf_token=auth.session.csrf_token,
)
```

Reference: `app/ui/http.py`, `app/ui/layout.py`, and `app/ui/routes/pipeline.py`.

## Fragment pattern

Every partial update has a declared region, an explicit target, and an interaction response. Do not
accept arbitrary client-supplied swap targets.

```python
@app.action(
    "/pipeline/preview",
    fragment_regions=(PIPELINE_PREVIEW_REGION, TOAST_HOST),
    include_in_schema=False,
)
async def preview(request: Request, _csrf: RequireCsrf, ...):
    return await interaction_response(
        request,
        ok_fragment(_preview_fragment(...), region_id=PIPELINE_PREVIEW_REGION.id),
    )
```

Reference: `app/ui/regions.py`, `app/ui/interactions.py`, and the Pipeline preview action.

## SafeUrl pattern

Use `mounted_path()`/`form_action()` and Hedron URL helpers for links and forms. Never concatenate a
request host, proxy header, or untrusted return path into an action URL.

```python
action=form_action(request, "/pipeline/save")
target=mounted_path(request, "/pipeline")
```

Reference: `app/ui/urls.py` and `app/ui/forms.py`.

## CSRF pattern

Unsafe actions depend on `RequireCsrf`; forms include the session token with `csrf_hidden()`. The
browser does not receive credential values, and a fragment endpoint must enforce the same ownership
checks as a full page.

```python
html.form(
    csrf_hidden(auth.session.csrf_token),
    ...,
    action=form_action(request, "/pipeline/runs"),
    method="post",
)
```

## Pipeline shell validation

The reference shell is covered by `tests/test_pipelines.py`, `tests/test_pipeline_runs.py`, and
`tests/test_ui_interactions.py`. These tests assert authenticated rendering, target/region markup,
CSRF-bearing mutations, owner-scoped runs, persisted events, and terminal status output. Run:

```bash
./.venv/bin/python -m pytest -q tests/test_pipelines.py tests/test_pipeline_runs.py tests/test_ui_interactions.py
```

## Non-negotiable boundaries

- Domain validation stays in services.
- Fragment targets are allowlisted.
- Credentials are decrypted only in the worker boundary.
- Plans, reports, audit events, and UI fragments contain references/metadata, never plaintext
  credentials.
