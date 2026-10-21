# Live transfer panel upgrade — October 21, 2026

Reworked the source → transfer status → destination section using native Hedron
Grid/GridItem, Surface, Identity, Icon, Text, Badge, ActionGroup, and Progress.

- Wider endpoint cards with file/database/layer icons, provider details, distinct object names
  and namespaces, and persisted row counts.
- Long names use native truncation and retain their full value in the DOM and hover title.
- One progress meter for active/completed runs. Stage-based progress is labeled explicitly.
- Cancelled/failed states show persisted destination impact without a misleading 0%-complete meter.
- Historical runs remain readable when their provider has subsequently been disabled.
- Success uses the native success tone. Polling, cancellation, and rerun controls remain intact.

The only CSS added is `.data-mover-transfer-status { align-self: center; text-align: center; }`.
Verified the missing native behaviors against Hedron/core 1.1.3 and upstream main, then filed:

- [#979 GridItem alignment markers lack native rules](https://github.com/eddiethedean/hedron/issues/979)
- [#980 Bounded text alignment for Text and typography scopes](https://github.com/eddiethedean/hedron/issues/980)

The shell stylesheet revision was incremented to invalidate cached styles. Remove the fallback
when both upstream capabilities are available.

Browser verification covered light/dark desktop layouts at 1120 and 1440 pixels, successful,
cancelled, and failed persisted runs, and a fresh live run through polling to success. No horizontal
overflow or duplicate IDs was observed in the inspected final states. Success/active routes expose
one progress meter; the cancelled route exposes none and reports Destination unchanged.

`make check`: **890 application tests + 25 demo tests passed; 82.00% coverage**. Lint, formatting,
types, documentation links, Hedron checks, and the Posit matrix passed. `make hedron-build` and
`git diff --check` also passed. Existing monitor tests now check single-meter behavior, cancellation
feedback, and stylesheet cache invalidation.

- [Final light preview](transfer-upgrade-preview-light.jpg)
- [Cancelled, light, in context](transfer-upgrade-cancelled-light.jpg)
- [Succeeded, dark](transfer-upgrade-succeeded-dark.jpg)
- [Active, dark](transfer-upgrade-active-dark.jpg)
- [1120-pixel desktop, dark](transfer-upgrade-1120-dark.jpg)
- [Validation output](transfer-upgrade-validation.log)
- [Build output](transfer-upgrade-build.log)
