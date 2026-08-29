/* Wait until the panel process has gone down and come back after a
   self-update, then reload. Triggered by HX-Trigger: selfUpdateStarted. */
(function () {
  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  async function waitForRestart() {
    var sawDown = false;
    var deadline = Date.now() + 120000;
    while (Date.now() < deadline) {
      await sleep(2000);
      try {
        var r = await fetch("/", { credentials: "same-origin", redirect: "manual" });
        if (sawDown && (r.ok || r.status === 303 || r.status === 302)) {
          window.location.reload();
          return;
        }
      } catch (e) {
        sawDown = true;
      }
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.body.addEventListener("selfUpdateStarted", waitForRestart);
  });
})();
