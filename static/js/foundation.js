(() => {
  let lastTrigger = null;
  document.addEventListener("click", (event) => {
    const opener = event.target.closest("[data-dialog-open]");
    if (opener) {
      const dialog = document.getElementById(opener.dataset.dialogOpen);
      if (dialog) {
        lastTrigger = opener;
        dialog.showModal();
        const focus = dialog.querySelector("[data-opening-focus], input:not([type=hidden]), select, textarea, button");
        if (focus) focus.focus();
      }
    }
    const closer = event.target.closest("[data-dialog-close]");
    if (closer) {
      const dialog = closer.closest("dialog");
      if (dialog && dialog.dataset.inFlight === undefined) dialog.close();
    }
    if (event.target.closest("[data-toast-close]")) event.target.closest("[data-toast]")?.remove();
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("close", () => lastTrigger?.focus());
    dialog.addEventListener("cancel", (event) => {
      if (dialog.dataset.inFlight !== undefined) event.preventDefault();
    });
    if (dialog.dataset.dialogAutoOpen !== undefined) {
      dialog.showModal();
      (dialog.querySelector("[aria-invalid=true], .errorlist + input, .errorlist + select, input:not([type=hidden]), select, textarea") || dialog.querySelector("button"))?.focus();
    }
  });
  document.querySelectorAll("form[data-submit-once]").forEach((form) => form.addEventListener("submit", (event) => {
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    const dialog = form.closest("dialog");
    dialog?.setAttribute("data-in-flight", "");
    form.querySelectorAll("button[type=submit]").forEach((control) => { control.disabled = true; });
    dialog?.querySelectorAll("[data-dialog-close]").forEach((control) => { control.disabled = true; });
    const status = form.querySelector("[data-submit-status]");
    if (status) status.hidden = false;
  }));
  document.querySelectorAll("[data-material-dependencies]").forEach((form) => {
    const sync = () => {
      const category = form.querySelector("[name=category_id]");
      const unit = form.querySelector("[name=unit_id]");
      const categoryVersion = form.querySelector("[name=category_version]");
      const unitVersion = form.querySelector("[name=unit_version]");
      const versionFor = (select) => {
        try { return JSON.parse(select.dataset.versionMap || "{}")[select.value]; } catch (_) { return null; }
      };
      if (category && categoryVersion) categoryVersion.value = versionFor(category) || categoryVersion.value;
      if (unit && unitVersion) unitVersion.value = versionFor(unit) || unitVersion.value;
    };
    form.addEventListener("change", sync);
    sync();
  });
  const toast = document.querySelector("[data-toast]");
  if (toast) {
    let remaining = 5000;
    let started = Date.now();
    let timer;
    let hovered = false;
    let focused = false;
    let running = false;
    const syncTimer = () => {
      const paused = hovered || focused;
      if (paused && running) {
        clearTimeout(timer);
        remaining = Math.max(0, remaining - (Date.now() - started));
        running = false;
      } else if (!paused && !running && remaining > 0) {
        started = Date.now();
        running = true;
        timer = setTimeout(() => toast.remove(), remaining);
      }
    };
    toast.addEventListener("mouseenter", () => { hovered = true; syncTimer(); });
    toast.addEventListener("mouseleave", () => { hovered = false; syncTimer(); });
    toast.addEventListener("focusin", () => { focused = true; syncTimer(); });
    toast.addEventListener("focusout", (event) => {
      focused = toast.contains(event.relatedTarget);
      syncTimer();
    });
    syncTimer();
  }
})();
