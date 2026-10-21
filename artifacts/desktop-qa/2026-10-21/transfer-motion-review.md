# Transfer motion and theme review — October 21, 2026

Follow-up to the transfer panel redesign. Native Hedron components still provide layout,
surfaces, identity, typography, badges, icons, and progress. Scoped CSS now supplies a
directional connector signal because Hedron 1.1.3 only offers an opacity pulse.

- Three staggered packets flow from source to destination during the loading stage.
- A restrained expanding ring highlights the transfer hub; text and cards stay still.
- The signal is preserved across HTMX monitor refreshes. Its enclosing server-authored
  state selects the icon and stops decorative motion at completion, cancellation, or failure.
- Theme tokens supply every signal color. Completed, cancelled, and failed states have
  distinct static check, pause, and attention icons as well as visible status text.
- Reduced-motion, forced-colors, and print rules disable decorative animation. These rules
  were reviewed in source; OS accessibility preferences were not changed during browser QA.
- Stylesheet cache revision is 7 so existing clients receive the updated CSS on reload.

Browser QA used the offline demo at 8877 with synthetic data. A temporary, uncommitted
writer delay allowed observation across multiple live refreshes and a light/dark switch;
the run completed successfully with three rows and all four schema columns matching.
The delay was removed and normal demo speed restored. Existing cancelled and failed runs
were checked at 1120px in both themes. The active transfer was checked at 1440px, and the
final normal viewport (1095px) had no horizontal overflow, one signal, and one progress meter.

The GIFs contain actual browser screenshots over approximately one 2.4-second motion cycle.

- [Light animation](transfer-motion-light.gif)
- [Dark animation](transfer-motion-dark.gif)
- [Completed, light](transfer-motion-complete-light.jpg)
- [Completed, dark](transfer-motion-complete-dark.jpg)
- [Cancelled, dark, 1120px](transfer-motion-cancelled-dark-1120.jpg)
- [Failed, light, 1120px](transfer-motion-failed-light-1120.jpg)
- [Failed, dark, 1120px](transfer-motion-failed-dark-1120.jpg)

Missing native motion support was reported in
[Hedron #981](https://github.com/eddiethedean/hedron/issues/981), alongside the existing
alignment reports [#979](https://github.com/eddiethedean/hedron/issues/979) and
[#980](https://github.com/eddiethedean/hedron/issues/980).

Validation: `make check` passed (890 application tests, 25 demo tests; 81.98% coverage),
including lint, formatting, types, docs, Hedron checks, and Posit matrix. `make hedron-build`
and `git diff --check` passed. Monitor tests assert the terminal state marker, stable signal
identity, HTMX preservation, cancellation feedback, and single-meter behavior.

- [Validation log](transfer-motion-validation.log)
- [Build log](transfer-motion-build.log)
