// Loading state: disable the submit button and say so, so slow pages (research, collection) are not double-submitted.
document.addEventListener("submit", function (e) {
  var f = e.target;
  if (!(f instanceof HTMLFormElement) || f.dataset.noBusy) return;
  var b = f.querySelector("button:not([type=button])");
  if (b && !b.disabled) {
    b.dataset.label = b.textContent;
    setTimeout(function () { b.disabled = true; b.textContent = "Working…"; }, 0);
  }
});
window.addEventListener("pageshow", function (e) {
  if (!e.persisted) return;
  document.querySelectorAll("button[data-label]").forEach(function (b) { b.disabled = false; b.textContent = b.dataset.label; });
});
