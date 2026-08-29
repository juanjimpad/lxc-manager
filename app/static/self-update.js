/* After Confirm, wait until the new process is up (APP_VERSION changed)
   and reload. A systemd restart can be faster than one failed fetch, so
   "saw the server go down" is not required. */
(function () {
  var waiting = false;

  function sleep(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function pageVersion() {
    return document.documentElement.getAttribute("data-app-version") || "";
  }

  async function readCurrent() {
    var r = await fetch("/api/v1/version", {
      credentials: "same-origin",
      redirect: "manual",
    });
    if (!r.ok) return null;
    var j = await r.json();
    return j.current || null;
  }

  async function waitForRestart() {
    if (waiting) return;
    waiting = true;
    var before = pageVersion();
    var deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      await sleep(1500);
      try {
        var current = await readCurrent();
        if (current && before && current !== before) {
          window.location.reload();
          return;
        }
        if (current && !before) {
          window.location.reload();
          return;
        }
      } catch (e) {
        /* still down */
      }
    }
    window.location.reload();
  }

  document.addEventListener("selfUpdateStarted", waitForRestart, true);
})();
