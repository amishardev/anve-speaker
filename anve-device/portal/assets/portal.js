"use strict";
// The box's setup page: choose a Wi-Fi, give the box what it needs, keep the link that
// adds it to an account. It stands on the ANVE Box team's setup.js (anve-box:
// device/portal) and keeps its rules: network names come from the air and can contain
// anything, so every piece of text is set with textContent, never as HTML.
//
// One thing is different on purpose. This box's setup Wi-Fi is an open network, so
// nothing secret may cross it in the clear: the page seals what you type for the box
// (assets/seal, the same sealing the phone app uses) and the box seals its answer, which
// carries the link. If the sealing code did not load, the page refuses to send anything.
(() => {
  const SVG = "http://www.w3.org/2000/svg";
  const ICONS = "assets/icons.svg#i-";
  const BARS = [null, "network_wifi_1_bar", "network_wifi_2_bar", "network_wifi_3_bar", "signal_wifi_4_bar"];
  const VIEWS = ["view-networks", "view-join", "view-link", "view-connecting", "view-trial"];
  const FAST_BLINK = { steps: [[1, 150], [0, 150]], smooth: false };
  const SLOW_PULSE = { steps: [[0.12, 1200], [1, 1200]], smooth: true };
  const $ = (id) => document.getElementById(id);

  let status = null;      // the box's /api/status
  let choice = null;      // { ssid, secure, sec, other }
  let answer = null;      // the box's sealed answer, opened: { claimLink, ticket, hold, ... }
  let holdTimer = null;
  let watchTimer = null;
  let gone = false;       // the box has left the setup Wi-Fi (a request to it failed)
  let view = "view-networks";
  let away = false;       // this device can't reach the box (asked twice, no answer)
  let misses = 0;
  let closesAt = 0;       // when a test run or a set-up-again window closes (0: no window)

  // ---------------------------------------------------------------- helpers

  function icon(name, label) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "icon");
    if (label) {
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", label);
    } else {
      svg.setAttribute("aria-hidden", "true");
    }
    const use = document.createElementNS(SVG, "use");
    use.setAttribute("href", ICONS + name);
    svg.append(use);
    return svg;
  }

  function strong(text) {
    const el = document.createElement("strong");
    el.textContent = text;
    return el;
  }

  function unbroken(text) {
    const el = document.createElement("span");
    el.className = "nowrap";
    el.textContent = text;
    return el;
  }

  function say(text) {
    $("live").textContent = text;
  }

  // A request that gives up: on a setup Wi-Fi that is closing, a request can hang.
  async function ask(url, options, ms) {
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), ms || 6000);
    try {
      return await fetch(url, Object.assign({ cache: "no-store", signal: stop.signal }, options || {}));
    } finally {
      clearTimeout(timer);
    }
  }

  async function getJSON(url) {
    const response = await ask(url);
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  function setupWifi() {
    return status ? status.setupWifi : "the setup Wi-Fi";
  }

  function show(next) {
    view = next;
    for (const id of VIEWS) $(id).hidden = id !== next;
    window.scrollTo(0, 0);
    const title = $(next).querySelector(".h1");
    if (title) title.focus({ preventScroll: true });
  }

  // The dark side of the page: which step, what the box's light is doing, one line of text.
  function stage(step, phase, title, sub, light) {
    for (const el of document.querySelectorAll(".step")) {
      const n = Number(el.dataset.step);
      el.classList.toggle("done", n < step);
      el.classList.toggle("now", n === step);
      if (n === step) el.setAttribute("aria-current", "step");
      else el.removeAttribute("aria-current");
    }
    document.querySelector(".stage").dataset.phase = phase;
    $("stage-title").textContent = title;
    $("stage-sub").textContent = sub;
    if (light && window.AnveLed) window.AnveLed.play($("stage-led"), light);
  }

  function light(name, fallback) {
    return (status && status.lights && status.lights[name]) || fallback;
  }

  // ---------------------------------------------------------------- 1. networks

  const ERRORS = {
    wrong_password: (n) => ["The password for ", strong(n), " didn't work. Check it and try again."],
    not_found: (n) => [strong(n), " couldn't be found. Check that it's switched on and near the box, or choose another network."],
    no_answer: (n) => [strong(n), " didn't answer. Move the box closer to the router, or choose another network."],
    no_address: (n) => [strong(n), " took the password but didn't let the box online. Try again, or choose another network."],
    unsupported: (n) => [strong(n), " uses a kind of sign-in this box can't do yet. Choose another network."],
    internal: () => ["Something went wrong inside the box. Try again; if it keeps happening, unplug the box for a few seconds."],
  };

  function showSetupError() {
    const error = status && status.error;
    const make = error && (ERRORS[error.code] || ERRORS.no_address);
    $("error").hidden = !make;
    if (make) $("error-text").replaceChildren(...make(error.network || "The network"));
  }

  function showNotice() {
    const p = (status && status.portal) || {};
    let text = "";
    if (!window.AnveSeal) {
      text = "This browser can't lock your Wi-Fi password before sending it, so this page won't send it. Open this page in a current browser, or use the ANVE Home app.";
    } else if (p.recovery) {
      text = "This box is already set up and has lost its Wi-Fi. Give it a new one from the ANVE Home app on the phone it's paired with.";
    } else if (p.trial) {
      text = "This is a test run of the setup Wi-Fi. You can go through every step: the box checks what you send and changes nothing.";
    } else if (p.again) {
      text = "This box is already set up and is being set up again. What you send here replaces its Wi-Fi and its name. " +
        "If you send nothing, it goes back to the Wi-Fi it was on.";
    }
    $("notice").hidden = !text;
    $("notice-text").textContent = text;
    setWindow(p);
    return !text || p.trial || p.again;      // may the person go on?
  }

  // A test run or a set-up-again has a closing time: the chip beside the name counts it down.
  function setWindow(info) {
    const label = info.trial ? "Test run" : info.again ? "Set up again" : "";
    closesAt = label && typeof info.left === "number" ? Date.now() + info.left * 1000 : 0;
    $("trial-chip").dataset.label = label;
    $("trial-chip").hidden = !label;
    tickWindow();
  }

  function tickWindow() {
    const chip = $("trial-chip");
    if (chip.hidden) return;
    const label = chip.dataset.label;
    if (!closesAt || away) {
      chip.textContent = label;
      return;
    }
    const left = Math.max(0, Math.round((closesAt - Date.now()) / 1000));
    chip.textContent = `${label} · closes in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
  }

  // ---------------------------------------------------------------- is the box still there?

  // Every few seconds, on every step before "connecting" (which has its own watch): the
  // setup Wi-Fi closes by itself (a test run ends, a phone drops an open network with no
  // internet), and a page that goes on showing a list that can't work looks stuck.
  let asking = false;
  async function alive() {
    if (asking || view === "view-connecting") return;
    asking = true;
    try {
      let info = null;
      try {
        info = await getJSON("api/alive");
      } catch {
        info = null;
      }
      const p = (status && status.portal) || {};
      if (info && info.ok) {
        misses = 0;
        // first contact, contact again, or the box is in another mode than this page thinks
        if (away || !status || info.trial !== p.trial || info.again !== Boolean(p.again)) await comeBack();
        else setWindow(info);
      } else if (++misses >= 2 && !away && status) {
        goAway();
      }
    } finally {
      asking = false;
    }
  }

  function goAway() {
    away = true;
    const p = (status && status.portal) || {};
    const wasTest = p.trial && closesAt && Date.now() > closesAt - 8000;     // its time was up
    let title;
    let text;
    if (wasTest) {
      title = "The test run is over.";
      text = ["The box has closed its setup Wi-Fi and gone back to the network it was on. Nothing was changed. You can close this page."];
    } else if (view === "view-link") {
      title = "This device has lost the box.";
      text = ["Your link is still here: copy it now. The box joins ", strong(answer.network), " by itself; when its light shows two short blinks, open the link."];
    } else {
      title = "This device isn't connected to the box any more.";
      text = ["Open your Wi-Fi settings and join ", unbroken(setupWifi()), " again. This page carries on by itself. ",
        p.trial ? "If that network isn't in the list, the test run has ended and the box is back on its own Wi-Fi."
          : "If that network isn't in the list, the box has left setup mode."];
    }
    $("lost-title").textContent = title;
    $("lost-text").replaceChildren(...text);
    $("lost").hidden = false;
    if (wasTest) $("notice").hidden = true;       // "this is a test run" is said above now, in the past tense
    for (const id of ["networks", "join-form", "trial-again", "go"]) $(id).inert = true;
    tickWindow();
    stage(view === "view-networks" ? 1 : view === "view-join" ? 2 : 3, "idle", wasTest ? "Test run over." : "Lost the box.",
      wasTest ? "The box is back on its own Wi-Fi." : `This device is no longer on ${setupWifi()}.`, null);
    say(`${title} ${$("lost-text").textContent}`);
  }

  async function comeBack() {
    // the box may have restarted in between: take its key, its state and its list again
    let statusData;
    let networkData;
    try {
      [statusData, networkData] = await Promise.all([getJSON("api/status"), getJSON("api/networks")]);
    } catch {
      return;                     // not really back yet: the next round asks again
    }
    away = false;
    $("lost").hidden = true;
    for (const id of ["networks", "join-form", "trial-again", "go"]) $(id).inert = false;
    loaded(statusData, networkData);
    if (view === "view-networks" || view === "view-trial") openNetworks();
    else if (view === "view-join") stage(2, "idle", "Almost there.", "The box is back. Carry on where you were.", light("setup", SLOW_PULSE));
    say("Connected to the box again.");
  }

  function networkRow(net, i, usable) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "row net";
    row.style.setProperty("--i", String(i));
    const main = document.createElement("span");
    main.className = "row-main";
    const name = document.createElement("span");
    name.className = "row-title";
    name.textContent = net.ssid;
    if (net.supported === false) {
      const why = document.createElement("small");
      why.textContent = "Work or school sign-in (802.1X): not supported yet";
      name.append(why);
    }
    const meta = document.createElement("span");
    meta.className = "net-meta";
    if (net.secure) meta.append(icon("lock", "Needs a password"));
    const bars = Math.min(4, Math.max(1, net.bars || 1));
    meta.append(icon(BARS[bars], `Signal ${bars} of 4`));
    main.append(name, meta);
    row.append(main);
    row.disabled = !usable || net.supported === false;
    row.addEventListener("click", () => openJoin({ ssid: net.ssid, secure: net.secure, sec: net.sec, other: false }));
    return row;
  }

  function otherRow(i, usable) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "row net";
    row.style.setProperty("--i", String(i));
    const main = document.createElement("span");
    main.className = "row-main";
    const name = document.createElement("span");
    name.className = "row-title";
    name.textContent = "Other network";
    const chev = icon("chevron_right");
    chev.classList.add("chev");
    main.append(name, chev);
    row.append(main);
    row.disabled = !usable;
    row.addEventListener("click", () => openJoin({ ssid: "", secure: true, sec: undefined, other: true }));
    return row;
  }

  function renderNetworks(list, usable) {
    const box = $("networks");
    box.replaceChildren(...list.map((net, i) => networkRow(net, i, usable)), otherRow(list.length, usable));
    box.setAttribute("aria-busy", "false");
    $("networks-hint").hidden = list.length > 0;
  }

  function openNetworks() {
    stage(1, "search", "Let's get your box online.",
      "The light on the box is pulsing slowly: it's ready for setup.", light("setup", SLOW_PULSE));
    show("view-networks");
  }

  // ---------------------------------------------------------------- 2. details

  function openJoin(next) {
    choice = next;
    const other = next.other;
    $("title-join").textContent = other ? "Other network" : next.ssid;
    $("field-ssid").hidden = !other;
    $("field-password").hidden = !other && !next.secure;
    $("ssid").value = "";
    $("password").value = "";
    setPasswordVisible(false);
    clearFieldErrors();
    $("send-error").hidden = true;
    if (other) {
      $("join-lead").textContent = "Type the network's name exactly as it appears, capitals included.";
      $("password-help").textContent = "Leave it empty if the network has no password.";
    } else if (next.secure) {
      $("join-lead").textContent = "Enter the password for this network, and give the box a name.";
      $("password-help").textContent = "";
    } else {
      $("join-lead").textContent =
        "This network has no password. If it shows a sign-in page when you join it, add those details below.";
      $("password-help").textContent = "";
    }
    $("password-help").hidden = !$("password-help").textContent;
    const p = (status && status.portal) || {};
    if (!$("boxname").value) $("boxname").value = p.name || "ANVE Box";
    if (!$("room").value && p.room) $("room").value = p.room;
    if (p.voice) {
      $("voice-summary").textContent = "Voice assistant key: already on this box";
      $("voice-lead").textContent = "This box already has its key for \"Hey ANVE\". Paste a key here only if you want to replace it.";
    }
    if (!next.secure && !other) $("more-signin").open = true;
    stage(2, "idle", "Almost there.", "Give the box your Wi-Fi details. It stays on its own setup Wi-Fi until you're done.",
      light("setup", SLOW_PULSE));
    show("view-join");
    if (other) $("ssid").focus();
    else if (next.secure) $("password").focus();
  }

  function setPasswordVisible(visible) {
    $("password").type = visible ? "text" : "password";
    $("reveal").setAttribute("aria-pressed", String(visible));
    $("reveal").setAttribute("aria-label", visible ? "Hide password" : "Show password");
    $("reveal-icon").setAttribute("href", ICONS + (visible ? "visibility_off" : "visibility"));
  }

  function clearFieldErrors() {
    for (const id of ["ssid", "password", "signin-user", "signin-pass", "voicekey", "boxname"]) $(id).removeAttribute("aria-invalid");
    for (const id of ["ssid-help", "signin-help", "voicekey-help"]) $(id).hidden = true;
    $("password-help").classList.remove("bad");
  }

  function fieldError(id, helpId, text) {
    $(id).setAttribute("aria-invalid", "true");
    const help = $(helpId);
    help.textContent = text;
    help.hidden = false;
    help.classList.add("bad");
    const fold = $(id).closest("details");
    if (fold) fold.open = true;
    $(id).focus();
  }

  function validPassword(value) {
    if (value.length >= 8 && value.length <= 63) return /^[\x20-\x7E]*$/.test(value);
    return value.length === 64 && /^[0-9a-fA-F]+$/.test(value);
  }

  function setBusy(busy) {
    const button = $("connect");
    button.disabled = busy;
    if (busy) {
      const spin = document.createElement("span");
      spin.className = "spin";
      spin.setAttribute("aria-hidden", "true");
      button.replaceChildren(spin, document.createTextNode("Sending to the box"));
    } else {
      button.replaceChildren(document.createTextNode("Continue"));
    }
  }

  function sendError(text) {
    $("send-error-text").textContent = text;
    $("send-error").hidden = false;
    setBusy(false);
  }

  // What the box refuses, field by field (provision.py: _check)
  const FIELD_ERRORS = {
    "wifi.ssid": ["ssid", "ssid-help", "Network names are at most 32 characters."],
    "wifi.password": ["password", "password-help", "Wi-Fi passwords have 8 to 63 characters."],
    portal: ["signin-user", "signin-help", "The sign-in name and password can't contain line breaks, and are at most 128 characters."],
    key: ["voicekey", "voicekey-help", "That doesn't look like an ANVE device key. Paste it exactly as it was given."],
  };

  async function submit(event) {
    event.preventDefault();
    clearFieldErrors();
    $("send-error").hidden = true;
    const ssid = choice.other ? $("ssid").value : choice.ssid;
    const password = $("password").value;
    if (choice.other && !ssid) return fieldError("ssid", "ssid-help", "Type the network's name.");
    if (choice.other && new TextEncoder().encode(ssid).length > 32) {
      return fieldError("ssid", "ssid-help", "Network names are at most 32 characters.");
    }
    const needsPassword = choice.other ? password.length > 0 : choice.secure;
    if (needsPassword && !validPassword(password)) {
      return fieldError("password", "password-help", "Wi-Fi passwords have 8 to 63 characters.");
    }
    const user = $("signin-user").value.trim();
    const pass = $("signin-pass").value;
    if (Boolean(user) !== Boolean(pass)) {
      return fieldError(user ? "signin-pass" : "signin-user", "signin-help",
        "Fill in both the sign-in name and its password, or leave both empty.");
    }
    const voicekey = $("voicekey").value.trim();
    if (voicekey && !/^[\x21-\x7E]{16,256}$/.test(voicekey)) {
      return fieldError(...FIELD_ERRORS.key);
    }
    if (!window.AnveSeal) return sendError("This browser can't lock your details, so nothing was sent.");

    const details = {
      wifi: { ssid, password: needsPassword ? password : "", hidden: choice.other },
      name: $("boxname").value.trim() || "ANVE Box",
      room: $("room").value.trim(),
    };
    if (!choice.other && typeof choice.sec === "string") details.wifi.sec = choice.sec;
    if (user) details.portal = { username: user, password: pass };
    if (voicekey) details.key = voicekey;

    setBusy(true);
    for (let attempt = 0; attempt < 2; attempt++) {
      let sealed;
      try {
        sealed = window.AnveSeal.seal(status.portal.pub, details);
      } catch {
        return sendError("This browser couldn't lock your details, so nothing was sent. Reload the page and try again.");
      }
      let response;
      try {
        response = await ask("api/portal/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(sealed.message),
        }, 10000);
      } catch {
        return sendError(`Couldn't reach the box. Check that this device is still on ${setupWifi()}.`);
      }
      let body = {};
      try {
        body = await response.json();
      } catch {
        body = {};
      }
      if (response.ok) {
        let opened;
        try {
          opened = window.AnveSeal.openReply(sealed.key, body);
        } catch {
          return sendError("The answer didn't come from this box, so the page stopped. Reload it and try again.");
        }
        for (const id of ["password", "signin-pass", "voicekey"]) $(id).value = "";
        setBusy(false);
        return accepted(opened, ssid);
      }
      if (body.error === "bad_message" && attempt === 0) {
        // the box restarted and has a new key: fetch it and send once more
        try {
          status = await getJSON("api/status");
          continue;
        } catch {
          return sendError(`Couldn't reach the box. Check that this device is still on ${setupWifi()}.`);
        }
      }
      if (body.error === "bad_field" && FIELD_ERRORS[body.field]) {
        setBusy(false);
        return fieldError(...FIELD_ERRORS[body.field]);
      }
      if (body.error === "busy") return sendError("The box is already connecting. Give it a minute, then reload this page.");
      if (body.error === "not_in_setup") return sendError("The box isn't in setup mode any more. Reload this page.");
      return sendError("The box isn't ready for Wi-Fi details right now. Reload this page and try again.");
    }
  }

  // ---------------------------------------------------------------- 3. the link, then connecting

  function accepted(opened, ssid) {
    answer = opened;
    answer.network = opened.network || ssid;
    if (opened.trial) {
      $("trial-lead").replaceChildren("The box checked the details for ", strong(answer.network),
        " and would accept them. Nothing was changed.");
      stage(3, "done", "Everything checks out.", "This was a test: the box changed nothing.", light("setup", SLOW_PULSE));
      show("view-trial");
      return say("Test run complete. Nothing was changed.");
    }
    if (opened.claimLink && opened.hold > 0) return openLink();
    openConnecting();
  }

  function openLink() {
    $("claim-url").textContent = answer.claimLink;
    $("step3-label").textContent = "Add to your account";
    stage(3, "idle", "Keep your link.", "The box is waiting on its setup Wi-Fi until you have it.", light("setup", SLOW_PULSE));
    show("view-link");
    say("Copy the link that adds this box to your account.");
    const total = answer.hold;
    const end = Date.now() + total * 1000;
    const fill = $("hold-fill");
    const tick = () => {
      const left = Math.max(0, Math.round((end - Date.now()) / 1000));
      fill.style.transform = `scaleX(${Math.max(0, (end - Date.now()) / (total * 1000))})`;
      $("hold-text").replaceChildren("The box joins ", strong(answer.network), ` by itself in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}.`);
      if (left <= 0) {
        clearInterval(holdTimer);
        openConnecting();
      }
    };
    tick();
    holdTimer = setInterval(tick, 500);
  }

  async function go() {
    $("go").disabled = true;
    clearInterval(holdTimer);
    try {
      await ask("api/portal/go", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticket: answer.ticket }),
      }, 5000);
    } catch {
      // the box may already be on its way: the next screen copes with either
    }
    openConnecting();
  }

  function openConnecting() {
    clearInterval(holdTimer);
    const link = answer.claimLink || "";
    $("title-connecting").textContent = `Connecting to ${answer.network}`;
    $("connecting-lead").replaceChildren(
      "The box is leaving ", unbroken(setupWifi()),
      " to join your Wi-Fi. This device will drop off the setup Wi-Fi too, usually back onto its own network. Watch the light on the box.");
    $("legend-setup").replaceChildren("It couldn't connect. Join ", unbroken(setupWifi()), " again to see why.");
    $("finish").hidden = !link;
    $("claim-url-2").textContent = link;
    if (link) $("open-claim").href = link;
    $("step3-label").textContent = link ? "Add to your account" : "Go online";
    const online = light("online", null);
    if (online) $("legend-online-title").textContent = online.label;
    $("legend-online").textContent = link
      ? "Online. Open the link to add it to your account."
      : "Online and ready. There's nothing else to do.";
    if (!answer.voice) {
      $("voice-note-text").textContent = "\"Hey ANVE\" isn't switched on yet: this box has no voice assistant key. " +
        "It still goes online and joins your account. To switch the voice assistant on, the box has to be set up again, with its key.";
      $("voice-note").hidden = false;
    }
    for (const led of document.querySelectorAll("[data-light]")) {
      window.AnveLed.play(led, light(led.dataset.light, null));
    }
    stage(3, "search", "Connecting…", "A fast blink means the box is joining your Wi-Fi.", FAST_BLINK);
    show("view-connecting");
    say(`Connecting to ${answer.network}.`);
    gone = false;
    watched = 0;
    clearInterval(watchTimer);
    watchTimer = setInterval(watch, 3000);
  }

  // After "connect": either the box comes back on its setup Wi-Fi with a reason (it could
  // not join), or this device gets back online and the link can be opened.
  let watching = false;
  let watched = 0;
  async function watch() {
    if (watching) return;
    if (++watched > 100) return clearInterval(watchTimer);   // five minutes: stop asking
    watching = true;
    try {
      let fresh = null;
      try {
        fresh = await getJSON("api/status");
      } catch {
        gone = true;
      }
      if (fresh && fresh.error && !(fresh.portal && fresh.portal.busy)) {
        // the box is back, with a reason
        status = fresh;
        clearInterval(watchTimer);
        answer = null;
        showSetupError();
        openNetworks();
        return say("The box couldn't connect.");
      }
      if (gone && answer && answer.claimLink && answer.cloud && $("open-claim").getAttribute("aria-disabled") === "true") {
        try {
          // any answer at all from ANVE means this device is online again
          await ask(answer.cloud + "/", { mode: "no-cors" }, 4000);
          $("open-claim").removeAttribute("aria-disabled");
          $("waitrow").classList.add("ok");
          $("wait-spin").hidden = true;
          $("wait-ok").removeAttribute("hidden");            // (an svg has no .hidden property)
          clearInterval(watchTimer);                         // off the setup Wi-Fi: nothing more to ask the box
          $("wait-text").textContent = "This device is back online. When the box shows two short blinks, add it to your account.";
          stage(4, "done", "You're back online.", "Open the link to add the box to your account.", light("online", null));
          say("This device is back online. You can open the link now.");
        } catch {
          // not online yet
        }
      }
    } finally {
      watching = false;
    }
  }

  async function copyLink(urlId, labelId, iconId) {
    const text = $(urlId).textContent;
    let copied = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        copied = true;
      }
    } catch {
      copied = false;
    }
    if (!copied) {
      // an http page has no clipboard API: select the text and use the old command
      const range = document.createRange();
      range.selectNodeContents($(urlId));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      try {
        copied = document.execCommand("copy");
      } catch {
        copied = false;
      }
    }
    $(labelId).textContent = copied ? "Copied" : "Selected";
    $(iconId).setAttribute("href", ICONS + (copied ? "check" : "content_copy"));
    say(copied ? "Link copied." : "Link selected. Copy it from the menu.");
  }

  // ---------------------------------------------------------------- start

  async function start() {
    $("back").addEventListener("click", openNetworks);
    $("reveal").addEventListener("click", () => setPasswordVisible($("password").type === "password"));
    $("join-form").addEventListener("submit", submit);
    $("copy").addEventListener("click", () => copyLink("claim-url", "copy-label", "copy-icon"));
    $("copy-2").addEventListener("click", () => copyLink("claim-url-2", "copy-label-2", "copy-icon-2"));
    $("go").addEventListener("click", go);
    $("trial-again").addEventListener("click", openNetworks);
    // leaving by accident would lose the link: ask first while it is on screen
    window.addEventListener("beforeunload", (event) => {
      if (answer && answer.claimLink) event.preventDefault();
    });
    openNetworks();

    try {
      const [statusData, networkData] = await Promise.all([getJSON("api/status"), getJSON("api/networks")]);
      loaded(statusData, networkData);
      openNetworks();
    } catch {
      $("networks").setAttribute("aria-busy", "false");
      $("networks-loading").textContent =
        "Couldn't reach the box. Check that this device is on the box's setup Wi-Fi. This page tries again by itself.";
    }
    setInterval(alive, 4000);
    setInterval(tickWindow, 1000);
  }

  // What the box says about itself and the networks it sees: at the start, and again
  // whenever this device finds the box again.
  function loaded(statusData, networkData) {
    status = statusData;
    $("boxid-value").textContent = status.box;
    $("setupwifi-value").textContent = status.setupWifi;
    $("stage-foot").hidden = false;
    showSetupError();
    const usable = showNotice();
    if (status.portal && status.portal.claimable) $("step3-label").textContent = "Add to your account";
    renderNetworks(networkData.networks || [], usable);
  }

  start();
})();
