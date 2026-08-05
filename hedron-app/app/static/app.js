document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-password-toggle]");
  if (!button) return;
  const input = document.getElementById(button.dataset.passwordToggle);
  if (!input) return;
  const revealing = input.type === "password";
  input.type = revealing ? "text" : "password";
  button.setAttribute("aria-pressed", revealing ? "true" : "false");
  button.textContent = revealing ? "Hide password" : "Show password";
});

document.addEventListener("htmx:beforeRequest", (event) => {
  event.detail.elt.setAttribute("aria-busy", "true");
  document.getElementById("global-feedback")?.replaceChildren();
});

document.addEventListener("htmx:afterRequest", (event) => {
  event.detail.elt.removeAttribute("aria-busy");
});

document.addEventListener("htmx:afterSwap", (event) => {
  const host = document.getElementById("toast-host");
  if (!host) return;
  for (const toast of host.querySelectorAll(".hedron-toast")) {
    window.setTimeout(() => toast.remove(), 4500);
  }
});
