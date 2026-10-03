// A live preview of the checklist's verdict while the trader taps. The server decides
// (and timestamps) when the form is saved; this only shows it early.
(function () {
  "use strict";
  const form = document.querySelector("form[data-checklist]");
  if (!form) return;
  const preview = document.getElementById("preview");
  const killers = (form.dataset.killers || "").split(" ");
  const autoRed = form.dataset.autoRed === "1";
  const tapped = ["stop_in_order", "planned_rr_ok", "size_ok"];

  function update() {
    let red = autoRed;
    for (const name of tapped) {
      const picked = form.querySelector(`input[name="${name}"]:checked`);
      if (!picked) {
        preview.textContent = "";
        preview.className = "preview";
        return;
      }
      if (killers.includes(name) && picked.value !== "yes") red = true;
    }
    preview.textContent = red ? "Rulebook says no trade" : "Go";
    preview.className = red ? "preview no" : "preview go";
  }

  form.addEventListener("change", update);
  update();
})();

// "Print or save as PDF" on the weekly review.
for (const button of document.querySelectorAll("[data-print]")) {
  button.addEventListener("click", () => window.print());
}

// Drop files on a [data-drop] form: they go into its file input and the form is sent.
for (const form of document.querySelectorAll("form[data-drop]")) {
  const zone = form.querySelector(".drop");
  const input = form.querySelector('input[type="file"]');
  if (!zone || !input) continue;
  zone.addEventListener("dragover", (ev) => { ev.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", (ev) => {
    ev.preventDefault();
    zone.classList.remove("over");
    if (!ev.dataTransfer || !ev.dataTransfer.files.length) return;
    input.files = ev.dataTransfer.files;
    form.submit();
  });
}
