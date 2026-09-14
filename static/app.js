/* RustMetrics — Frontend.
   Watchlist + live server browser + notifications.
   Vanilla JS, no build step, runs in the browser as-is.
*/
(() => {
  "use strict";

  const REFRESH_MS         = 15000;
  const BROWSE_AUTO_FETCH  = true;
  const LS_PREFIX          = "rustmetrics.";

  let serversCache   = [];
  let browseCache    = null;
  let onlineStateMap = new Map();
  let currentDrawer  = null;
  let drawerHours    = 24;      // Chart-Zeitraum im Detail-Drawer (24h/168h/720h)
  let detailTimer    = null;
  let currentTab     = "watchlist";
  let browseFetched  = false;
  let me             = null;
  let friendsCache   = [];
  // Sort state for detail-drawer player lists (persists across auto-refresh)
  const detailSort = {
    current: "name_asc",       // name_asc | name_desc
    active:  "duration_desc",  // duration_desc | start_desc | name_asc
    past:    "ended_desc",     // ended_desc | duration_desc | name_asc
  };

  const $  = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const els = {
    navItems:       $$(".nav-item"),
    views:          $$(".view"),
    servers:        $("#servers"),
    serversEmpty:   $("#servers-empty"),
    serversToolbar: $("#servers-toolbar"),
    viewGridBtn:    $("#view-grid"),
    viewListBtn:    $("#view-list"),
    btnClearWatchlist: $("#btn-clear-watchlist"),
    addForm:        $("#add-server-form"),
    addHost:        $("#srv-host"),
    addPort:        $("#srv-port"),
    statServers:    $("#stat-servers"),
    statWatched:    $("#stat-watched"),
    statOnline:     $("#stat-online"),
    topbarStats:    $("#topbar-stats"),
    pollIndicator:  $("#poll-indicator"),
    btnNotif:       $("#btn-notif"),
    footerTs:       $("#footer-ts"),
    userSlot:       $("#user-slot"),
    guestCta:       $("#guest-cta"),
    authOnly:       $("#auth-only"),
    cookieBanner:   $("#cookie-banner"),
    cookieAck:      $("#cookie-ack"),
    settingsGuest:  $("#settings-guest"),
    settingsForm:   $("#settings-form"),
    setDiscordWebhook: $("#set-discord-webhook"),
    setNotifyOnline:   $("#set-notify-online"),
    setNotifyOffline:  $("#set-notify-offline"),
    btnTestWebhook:    $("#btn-test-webhook"),
    calWebcalUrl:      $("#cal-webcal-url"),
    calHttpsUrl:       $("#cal-https-url"),
    calWebcalOpen:     $("#cal-webcal-open"),
    btnCalCopy:        $("#btn-cal-copy"),
    btnCalCopyHttps:   $("#btn-cal-copy-https"),
    btnCalShow:        $("#btn-cal-show"),
    btnCalShowHttps:   $("#btn-cal-show-https"),
    btnCalReset:       $("#btn-cal-reset"),
    calStatus:         $("#cal-status"),
    settingsStatus:    $("#settings-status"),
    drawer:         $("#detail-drawer"),
    rhythmModal:    $("#rhythm-modal"),
    rhythmTitle:    $("#rhythm-title"),
    rhythmBody:     $("#rhythm-body"),
    drawerBody:     $("#drawer-body"),
    drawerTitle:    $("#drawer-title"),
    browseForm:     $("#browse-filter"),
    bfQ:            $("#bf-q"),
    bfRegion:       $("#bf-region"),
    bfTier:         $("#bf-tier"),
    bfSort:         $("#bf-sort"),
    bfGroup:        $("#bf-group"),
    bfWiped:        $("#bf-wiped"),
    bfMinPop:       $("#bf-min-pop"),
    bfRefresh:      $("#bf-refresh"),
    browseLoading:  $("#browse-loading"),
    browseResults:  $("#browse-results"),
    browseStatus:   $("#browse-status"),
    friendsSection: $("#friends-section"),
    friendsList:    $("#friends-list"),
    friendsCount:   $("#friends-count"),
    friendsRefresh: $("#friends-refresh"),
  };

  const fmt = {
    relTime(ts) {
      if (!ts) return "—";
      const diff = Math.max(0, Math.floor(Date.now() / 1000) - ts);
      if (diff < 60)    return `${diff}s ago`;
      if (diff < 3600)  return `${Math.floor(diff/60)}m ago`;
      if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
      return `${Math.floor(diff/86400)}d ago`;
    },
    duration(sec) {
      if (sec == null) return "—";
      if (sec < 60)    return `${Math.floor(sec)}s`;
      if (sec < 3600)  return `${Math.floor(sec/60)}m`;
      const h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60);
      return m ? `${h}h ${m}m` : `${h}h`;
    },
    timeShort(ts) {
      if (!ts) return "—";
      return new Date(ts * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
    },
    escape(s) {
      return String(s ?? "").replace(/[&<>"']/g, c =>
        ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    },
  };

  async function api(path, opts = {}) {
    const headers = { "Content-Type": "application/json", ...(opts.headers||{}) };
    const r = await fetch(path, { ...opts, headers, credentials: "same-origin" });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); if (j.error) msg = j.error; } catch (e) {}
      const err = new Error(msg);
      err.status = r.status;
      throw err;
    }
    return r.json();
  }

  // ─── Tabs ─────────────────────────────────────────────────────────────
  // Nur der Server-Browser hat eine eigene URL (/browser, serverseitig vorgerendert).
  const HOME_TITLE = "RustMetrics — Rust Server & Player Tracker";
  const TAB_PATHS  = { browse: "/browser" };
  const TAB_TITLES = { browse: "Rust Server Browser — Live Pop, Wipes & Filters | RustMetrics" };
  const PATH_TABS  = { "/browser": "browse", "/browser/": "browse" };
  const VALID_TABS = ["browse", "watchlist", "about", "settings", "mystats", "play"];

  function syncUrl(name, historyMode) {
    document.title = TAB_TITLES[name] || HOME_TITLE;
    if (historyMode === "none") return;
    const path = TAB_PATHS[name] || "/app";
    if (location.pathname === path) return;
    const method = historyMode === "replace" ? "replaceState" : "pushState";
    try { history[method]({ tab: name }, "", path); } catch (e) {}
  }

  function switchTab(name, historyMode = "push") {
    if (!name) return;
    currentTab = name;
    els.navItems.forEach(t => t.classList.toggle("is-active", t.dataset.tab === name));
    els.views.forEach(v => {
      const active = v.dataset.view === name;
      v.classList.toggle("is-active", active);
      v.hidden = !active;
    });
    if (name === "browse" && !browseFetched && BROWSE_AUTO_FETCH) {
      browseFetched = true;
      loadBrowse();
    }
    if (name === "mystats") {
      loadMyStats();
    }
    if (name === "play") {
      loadPlayLeaderboard();
    }
    syncUrl(name, historyMode);
    try { localStorage.setItem(LS_PREFIX + "tab", name); } catch (e) {}
  }
  els.navItems.forEach(t => {
    if (t.dataset.tab) t.addEventListener("click", () => switchTab(t.dataset.tab));
  });
  document.addEventListener("click", (e) => {
    const goto = e.target.closest?.("[data-go-tab]");
    if (goto) { e.preventDefault(); switchTab(goto.dataset.goTab); }
  });
  window.addEventListener("popstate", (e) => {
    switchTab(e.state?.tab || PATH_TABS[location.pathname] || "watchlist", "none");
  });

  // ─── Auth / User ──────────────────────────────────────────────────────
  function isAuthed() { return !!(me && me.authenticated); }

  // Whitelist für externe Steam-URLs (Defense gegen javascript:-Scheme falls Steam-Daten kompromittiert)
  function isSafeSteamUrl(url) {
    if (!url || typeof url !== "string") return false;
    return /^https:\/\/(steamcommunity\.com|[a-z0-9_-]+\.steamstatic\.com|[a-z0-9_-]+\.akamaihd\.net|avatars\.cloudflare\.steamstatic\.com)\//.test(url);
  }

  function renderUserSlot() {
    if (isAuthed()) {
      const safeAvatar  = isSafeSteamUrl(me.avatar_url)  ? me.avatar_url  : null;
      const safeProfile = isSafeSteamUrl(me.profile_url) ? me.profile_url : null;
      const avatar = safeAvatar
        ? `<span class="user-chip-avatar" style="background-image:url('${fmt.escape(safeAvatar)}')"></span>`
        : `<span class="user-chip-avatar placeholder">${fmt.escape((me.display_name||'?').slice(0,1).toUpperCase())}</span>`;
      const profileAttrs = safeProfile
        ? `href="${fmt.escape(safeProfile)}" target="_blank" rel="noopener noreferrer"`
        : `href="#" onclick="return false"`;
      els.userSlot.innerHTML = `
        <a class="user-chip" ${profileAttrs} title="Steam profile">
          ${avatar}
          <span class="user-chip-name">${fmt.escape(me.display_name || me.user_id)}</span>
        </a>
        <a class="btn-logout" href="/auth/logout" title="Log out">Logout</a>
      `;
    } else {
      els.userSlot.innerHTML = `
        <a class="btn-steam" href="/auth/steam/login">
          <span class="steam-icon" aria-hidden="true">⌬</span>
          <span>Sign in<span class="btn-steam-long"> with Steam</span></span>
        </a>
      `;
    }
  }

  function applyAuthState() {
    if (isAuthed()) {
      els.guestCta.hidden = true;
      els.authOnly.hidden = false;
      els.topbarStats.hidden = false;
      els.settingsGuest && (els.settingsGuest.hidden = true);
      els.settingsForm  && (els.settingsForm.hidden  = false);
      // Settings-Felder mit /api/me-Werten bestücken
      if (me.settings) {
        if (els.setDiscordWebhook) els.setDiscordWebhook.value = me.settings.discord_webhook || "";
        if (els.setNotifyOnline)   els.setNotifyOnline.checked = !!me.settings.notify_online;
        if (els.setNotifyOffline)  els.setNotifyOffline.checked = !!me.settings.notify_offline;
      }
      // Calendar-URL bestücken
      if (me.calendar) {
        if (els.calWebcalUrl) els.calWebcalUrl.value = me.calendar.webcal_url || "";
        if (els.calHttpsUrl)  els.calHttpsUrl.value  = me.calendar.url || "";
        if (els.calWebcalOpen) els.calWebcalOpen.href = me.calendar.webcal_url || "#";
      }
    } else {
      els.guestCta.hidden = false;
      els.authOnly.hidden = true;
      els.settingsGuest && (els.settingsGuest.hidden = false);
      els.settingsForm  && (els.settingsForm.hidden  = true);
      els.servers.innerHTML = "";
      els.serversEmpty.hidden = true;
      els.topbarStats.hidden = true;
    }
    renderUserSlot();
  }

  async function loadMe() {
    try { me = await api("/api/me"); }
    catch (e) { me = { authenticated: false }; }
    applyAuthState();
  }

  // ─── Notifications ────────────────────────────────────────────────────
  const NOTIF_ENABLED_KEY = LS_PREFIX + "notif";

  function notifEnabled() {
    return localStorage.getItem(NOTIF_ENABLED_KEY) === "1"
        && "Notification" in window
        && Notification.permission === "granted";
  }
  function setNotifBtn() {
    if (!("Notification" in window)) { els.btnNotif.style.display = "none"; return; }
    const on = notifEnabled();
    els.btnNotif.classList.toggle("active", on);
    els.btnNotif.querySelector(".lbl").textContent =
      on ? "ON" : (Notification.permission === "denied" ? "Blocked" : "Notif");
  }
  els.btnNotif?.addEventListener("click", async () => {
    if (!("Notification" in window)) return;
    if (Notification.permission === "default") {
      const p = await Notification.requestPermission();
      if (p === "granted") localStorage.setItem(NOTIF_ENABLED_KEY, "1");
    } else if (Notification.permission === "granted") {
      localStorage.setItem(NOTIF_ENABLED_KEY, notifEnabled() ? "0" : "1");
    }
    setNotifBtn();
  });
  function notifyPlayerOnline(serverName, playerName) {
    if (!notifEnabled()) return;
    try {
      new Notification("Player online", {
        body: `${playerName} just appeared on ${serverName}.`,
        tag:  `rustmetrics-${serverName}-${playerName}`,
      });
    } catch (e) {}
  }

  // ─── Stats ────────────────────────────────────────────────────────────
  function refreshStats() {
    let totalWatched = 0, totalOnline = 0;
    for (const s of serversCache) {
      for (const w of s.watched_players || []) {
        totalWatched++;
        if (w.online) totalOnline++;
      }
    }
    els.statServers.textContent = serversCache.length;
    els.statWatched.textContent = totalWatched;
    els.statOnline.textContent  = totalOnline;
  }

  // ─── Watchlist cards ──────────────────────────────────────────────────
  function renderTags(s) {
    const tags = [];
    if (s.tier) tags.push(`<span class="tag tier-${s.tier}">${fmt.escape(s.tier)}</span>`);
    if (s.region) tags.push(`<span class="tag region">${fmt.escape(String(s.region).toUpperCase())}</span>`);
    if (s.max_group != null) {
      const GL = {1:"solo", 2:"duo", 3:"trio", 4:"quad"};
      const lbl = s.max_group === 0 ? "no limit" : (GL[s.max_group] || `max ${s.max_group}`);
      tags.push(`<span class="tag group" title="Max group size (detected from server name/tags)">${lbl}</span>`);
    }
    for (const t of (s.tags || []).slice(0, 4)) {
      if (t === s.tier) continue;
      tags.push(`<span class="tag">${fmt.escape(t)}</span>`);
    }
    return tags.join("");
  }

  function renderWatchlistSub(s) {
    const friendsHere = friendsCache.filter(f => f.server_id === s.id);
    const friendsLine = friendsHere.length ? `
      <div class="friends-here" title="Your Steam friends on this server right now — real Steam names, even when the server hides names">
        Friends here: ${friendsHere.map(f => `<strong>${fmt.escape(f.persona)}</strong>`).join(", ")}
      </div>` : "";
    const anonWarn = friendsLine + (s.name_anonymized ? `
      <div class="anon-warn anon-warn-compact" title="Facepunch streamer mode / censorplayerlist — this server's public player list shows random names, so name-watching won't work here. Workaround: add players by SteamID64 or Steam profile URL instead — Steam presence tracking sees them anyway (requires their profile's game details to be public).">
        ⚠ Names randomized here — <strong>use SteamID64 instead</strong> <span class="anon-warn-info">ⓘ</span>
      </div>` : "");
    const items = (s.watched_players || []).map(w => {
      const nameHTML = w.bm_player_id
        ? `<a class="player-name plink" href="/player/${w.bm_player_id}" target="_blank" rel="noopener">${fmt.escape(w.name)}</a>`
        : `<span class="player-name">${fmt.escape(w.name)}</span>`;
      // Steam-presence status (steam-watched players work despite streamer mode)
      let dotCls = w.online ? "is-online" : "";
      let meta   = w.online ? "ONLINE" : "OFFLINE";
      let steamBadge = "";
      if (w.steam_id) {
        const p = w.steam;
        steamBadge = `<span class="tag steam-tag" title="Tracked via Steam presence (SteamID ${fmt.escape(w.steam_id)})">steam</span>`;
        if (!p) {
          meta = "PENDING";       // erster Presence-Poll steht noch aus
        } else if (p.in_rust && p.on_this_server) {
          dotCls = "is-online"; meta = "ONLINE";
        } else if (p.in_rust) {
          dotCls = "is-online";
          meta = `IN RUST · ${fmt.escape(p.server_addr || "other server")}`;
        } else if (!p.visible) {
          meta = "PROFILE PRIVATE";
        } else {
          meta = "OFFLINE / DETAILS HIDDEN";
        }
      }
      return `
      <div class="watched-player ${dotCls ? "is-online" : "is-offline"}" data-watch-id="${w.id}">
        <span class="player-dot ${dotCls}"></span>
        ${nameHTML}${steamBadge}
        <span class="player-meta">${meta}</span>
        <button class="btn-rhythm" type="button" title="Online rhythm — last 7 days"
          data-rhythm-watch="${w.id}"
          data-rhythm-name="${fmt.escape((w.steam && w.steam.persona) || w.name)}">▦</button>
        <button class="btn-remove" title="Remove" data-remove-watch="${s.id}:${w.id}">×</button>
      </div>
    `;
    }).join("");

    return `
      <div class="watchlist">
        ${anonWarn}
        <div class="watchlist-header">
          <strong>Watched Players</strong>
          <span>${s.watched_players?.length || 0}</span>
        </div>
        ${items || `<div class="watchlist-empty">No players watched on this server yet. Add a name below to get notified when they come online.</div>`}
        <form class="add-watch-form" data-add-watch="${s.id}">
          <input type="text" placeholder="Player name — or SteamID64 / profile URL" required />
          <button type="submit">+ Watch</button>
        </form>
      </div>
    `;
  }

  function renderCard(s) {
    const snap   = s.snapshot;
    const online = snap?.online;
    const pop    = snap?.players_count ?? "—";
    const max    = snap?.max_players ?? "—";
    const pct    = (snap?.players_count && snap?.max_players)
                  ? Math.round(100 * snap.players_count / snap.max_players) : 0;
    const ping   = snap?.ping_ms != null ? `${snap.ping_ms}<span class="sub">ms</span>` : "—";
    const map    = snap?.map || "—";
    const wipe   = s.wipe?.days_until_next != null
                  ? `Wipe in ${s.wipe.days_until_next}d` : "—";
    const updated = snap?.ts ? fmt.relTime(snap.ts) : "—";

    return `
      <article class="server-card ${online ? "" : "is-offline"}" data-server-id="${s.id}">
        <header class="card-header">
          <span class="online-dot ${online ? "" : "is-off"}" title="${online ? "online" : "offline"}"></span>
          <div class="card-header-main">
            <h3 class="card-title" data-open-detail="${s.id}">${brandLogoFor(s.name) ? `<img class="card-logo" src="${brandLogoFor(s.name)}" alt="" loading="lazy" />` : ""}${fmt.escape(s.name)}</h3>
            <div class="card-host">${fmt.escape(s.host)}:${s.port}</div>
            <div class="card-tags">${renderTags(s)}</div>
          </div>
        </header>
        <div class="card-body">
          <div class="card-stats">
            <div class="card-stat">
              <span class="card-stat-label">Players</span>
              <span class="card-stat-value">${pop}<span class="sub"> / ${max}</span></span>
              <div class="fill-bar"><div class="fill-bar-inner" style="width:${pct}%"></div></div>
            </div>
            <div class="card-stat">
              <span class="card-stat-label">Map</span>
              <span class="card-stat-value" style="font-size:13px;">${fmt.escape(map)}</span>
            </div>
            <div class="card-stat">
              <span class="card-stat-label">Ping</span>
              <span class="card-stat-value">${ping}</span>
            </div>
            <div class="card-stat">
              <span class="card-stat-label">Queue</span>
              <span class="card-stat-value">${s.queued ?? 0}</span>
            </div>
          </div>
          ${renderWatchlistSub(s)}
          <button class="btn-player-list" data-open-detail="${s.id}" title="Full player list, history, chart">
            ▤ View Player List &amp; History
          </button>
        </div>
        <footer class="card-footer">
          <span class="last-update">${snap?.error
            ? `<span style="color:var(--red)">${fmt.escape(snap.error)}</span>`
            : `updated ${updated}${s.via_battlemetrics
                ? ' · via <a class="via-bm" href="https://www.battlemetrics.com/" target="_blank" rel="noopener">BattleMetrics</a>'
                : ""}`}</span>
          <span class="wipe-info">${wipe}</span>
          <button class="btn-small danger" data-remove-server="${s.id}" title="Remove">×</button>
        </footer>
      </article>
    `;
  }

  function renderServers() {
    if (!serversCache.length) {
      els.servers.innerHTML = "";
      els.serversEmpty.hidden = false;
      if (els.serversToolbar) els.serversToolbar.hidden = true;
    } else {
      els.serversEmpty.hidden = true;
      if (els.serversToolbar) els.serversToolbar.hidden = false;
      els.servers.innerHTML = serversCache.map(renderCard).join("");
    }
    refreshStats();
  }

  // ─── Grid/List View-Toggle (per-Browser gemerkt) ──────────────────────
  function applyViewMode(mode) {
    els.servers?.classList.toggle("view-list", mode === "list");
    els.viewGridBtn?.classList.toggle("is-active", mode !== "list");
    els.viewListBtn?.classList.toggle("is-active", mode === "list");
  }
  (function initViewMode() {
    let saved = "grid";
    try { saved = localStorage.getItem("rm_view_mode") || "grid"; } catch (e) {}
    applyViewMode(saved);
  })();
  els.viewGridBtn?.addEventListener("click", () => {
    applyViewMode("grid");
    try { localStorage.setItem("rm_view_mode", "grid"); } catch (e) {}
  });
  els.viewListBtn?.addEventListener("click", () => {
    applyViewMode("list");
    try { localStorage.setItem("rm_view_mode", "list"); } catch (e) {}
  });

  // ─── Clear Watchlist ───────────────────────────────────────────────
  els.btnClearWatchlist?.addEventListener("click", async () => {
    if (!serversCache.length) return;
    const n = serversCache.length;
    if (!confirm(`Remove all ${n} server${n === 1 ? "" : "s"} from your watchlist? All collected data will be lost. This can't be undone.`)) return;
    els.btnClearWatchlist.disabled = true;
    try {
      for (const s of serversCache.slice()) {
        await api(`/api/server/${s.id}`, { method: "DELETE" });
      }
      await loadServers();
    } catch (err) {
      alert(err.message);
    } finally {
      els.btnClearWatchlist.disabled = false;
    }
  });

  function diffNotifications() {
    for (const s of serversCache) {
      for (const w of s.watched_players || []) {
        const key  = `${s.id}|${w.name.toLowerCase()}`;
        const prev = onlineStateMap.get(key);
        if (prev === false && w.online) notifyPlayerOnline(s.name, w.name);
        onlineStateMap.set(key, !!w.online);
      }
    }
  }

  async function loadServers() {
    if (!isAuthed()) return;
    try {
      const data = await api("/api/servers");
      serversCache = data;
      renderServers();
      diffNotifications();
      els.pollIndicator.classList.remove("pulse");
      void els.pollIndicator.offsetWidth;
      els.pollIndicator.classList.add("pulse");
      els.footerTs.textContent = "Synced " + new Date().toLocaleTimeString();
    } catch (e) {
      if (e.status === 401) { await loadMe(); return; }
      console.error(e);
      els.footerTs.textContent = "Error: " + e.message;
    }
  }

  els.servers.addEventListener("click", async (e) => {
    const t = e.target;
    const openId = t.closest?.("[data-open-detail]")?.dataset.openDetail;
    if (openId) return openDetail(parseInt(openId, 10));
    const rmServer = t.closest?.("[data-remove-server]")?.dataset.removeServer;
    if (rmServer) {
      if (!confirm("Remove this server from your watchlist? All collected data will be lost.")) return;
      try { await api(`/api/server/${rmServer}`, { method: "DELETE" }); await loadServers(); }
      catch (err) { alert(err.message); }
      return;
    }
    const rhythmBtn = t.closest?.("[data-rhythm-watch]");
    if (rhythmBtn) {
      return openRhythm(parseInt(rhythmBtn.dataset.rhythmWatch, 10), rhythmBtn.dataset.rhythmName);
    }
    const rmWatch = t.closest?.("[data-remove-watch]")?.dataset.removeWatch;
    if (rmWatch) {
      const [sid, wid] = rmWatch.split(":");
      try { await api(`/api/server/${sid}/watch/${wid}`, { method: "DELETE" }); await loadServers(); }
      catch (err) { alert(err.message); }
    }
  });

  els.servers.addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-add-watch]");
    if (!form) return;
    e.preventDefault();
    const sid = form.dataset.addWatch;
    const input = form.querySelector("input");
    const name = input.value.trim();
    if (!name) return;
    try {
      await api(`/api/server/${sid}/watch`, { method: "POST", body: JSON.stringify({ name }) });
      input.value = "";
      await loadServers();
    } catch (err) { alert(err.message); }
  });


  // ─── Freunde, die gerade Rust spielen ─────────────────────────────────
  // Nutzt die Steam-Freundesliste + Presence. Braucht eine oeffentliche
  // Freundesliste; ist sie privat, blendet sich der Block einfach aus.
  async function loadFriends() {
    if (!els.friendsSection || !isAuthed()) return;
    try {
      const d = await api("/api/me/friends");
      const nextFriends = d.available ? d.friends : [];
      const placement = list => JSON.stringify(list.map(f => [f.steam_id, f.persona, f.server_id]));
      const friendsMoved = placement(nextFriends) !== placement(friendsCache);
      friendsCache = nextFriends;
      if (friendsMoved && serversCache.length) renderServers();
      if (!d.available || !d.friends.length) {
        els.friendsSection.hidden = true;
        return;
      }
      els.friendsSection.hidden = false;
      els.friendsCount.textContent =
        `${d.playing} of ${d.total_friends} friends`;
      els.friendsList.innerHTML = d.friends.map(f => {
        const where = f.server_name
          ? fmt.escape(f.server_name)
          : (f.server_addr ? fmt.escape(f.server_addr) : "unknown server");
        const act = f.on_watchlist
          ? `<button class="btn-small" data-friend-watch="${fmt.escape(f.steam_id)}"
               data-friend-server="${f.server_id || ""}"
               data-friend-name="${fmt.escape(f.persona)}">+ Track</button>`
          : (f.server_ip
             ? `<button class="btn-small" data-friend-addserver="${fmt.escape(f.server_ip)}"
                  data-friend-port="${Number(f.server_query_port) || ""}"
                  data-friend-steam="${fmt.escape(f.steam_id)}"
                  data-friend-name="${fmt.escape(f.persona)}">+ Server & Track</button>`
             : "");
        const av = f.avatar ? `<img class="friend-av" src="${fmt.escape(f.avatar)}" alt="" />` : "";
        return `<div class="friend">
            ${av}
            <span class="friend-name">${fmt.escape(f.persona)}</span>
            <span class="friend-where">on ${where}</span>
            <span class="friend-act">${act}</span>
          </div>`;
      }).join("");
    } catch (e) {
      els.friendsSection.hidden = true;
    }
  }

  els.friendsRefresh?.addEventListener("click", loadFriends);

  els.friendsList?.addEventListener("click", async (e) => {
    const track = e.target.closest("[data-friend-watch]");
    const addsrv = e.target.closest("[data-friend-addserver]");
    const btn = track || addsrv;
    if (!btn) return;
    btn.disabled = true; btn.textContent = "…";
    try {
      if (addsrv) {
        // Server zuerst auf die Watchlist, dann den Freund per SteamID tracken
        const r = await api("/api/servers", {
          method: "POST",
          body: JSON.stringify({ host: addsrv.dataset.friendAddserver, port: parseInt(addsrv.dataset.friendPort, 10) || 28015 })
        });
        await api(`/api/server/${r.id}/watch`, {
          method: "POST",
          body: JSON.stringify({ name: addsrv.dataset.friendSteam })
        });
      } else {
        await api(`/api/server/${track.dataset.friendServer}/watch`, {
          method: "POST",
          body: JSON.stringify({ name: track.dataset.friendWatch })
        });
      }
      await loadServers();
      await loadFriends();
    } catch (err) {
      alert(err.message);
      btn.disabled = false; btn.textContent = "+ Track";
    }
  });

  els.addForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const host = els.addHost.value.trim();
    const port = parseInt(els.addPort.value, 10);
    if (!host || !port) return;
    try {
      await api("/api/servers", { method: "POST", body: JSON.stringify({ host, port }) });
      els.addHost.value = ""; els.addPort.value = "";
      await loadServers();
      setTimeout(loadServers, 1500);
    } catch (err) { alert(err.message); }
  });

  // ─── Detail drawer ────────────────────────────────────────────────────
  document.querySelectorAll("[data-close-drawer]").forEach(el =>
    el.addEventListener("click", closeDrawer));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && currentDrawer) closeDrawer();
  });

  // Sort-Dropdown delegated change handler (drawer re-rendert komplett, daher delegation)
  els.drawerBody?.addEventListener("change", (e) => {
    const sel = e.target.closest?.(".sort-select");
    if (!sel) return;
    const target = sel.dataset.sortTarget;
    if (!target || !(target in detailSort)) return;
    detailSort[target] = sel.value;
    refreshDetail();   // re-render mit neuem Sort
  });

  // Add-Watch-Form IM Drawer (Steam-Feld): eigener Submit-Handler, da der
  // Watchlist-Handler nur auf els.servers hört.
  els.drawerBody?.addEventListener("submit", async (e) => {
    const form = e.target.closest("[data-add-watch]");
    if (!form) return;
    e.preventDefault();
    const sid = form.dataset.addWatch;
    const input = form.querySelector("input");
    const val = input.value.trim();
    if (!val) return;
    const btn = form.querySelector("button");
    if (btn) { btn.disabled = true; btn.textContent = "…"; }
    try {
      const res = await api(`/api/server/${sid}/watch`,
                            { method: "POST", body: JSON.stringify({ name: val }) });
      input.value = "";
      if (res && res.steam_id) {
        // kleine Bestätigung, damit klar ist dass Steam-Tracking griff
        input.placeholder = `✓ tracking ${res.persona || res.steam_id} via Steam`;
      }
      await loadServers();
      await refreshDetail();
    } catch (err) {
      alert(err.message);
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "+ Track via Steam"; }
    }
  });

  // Quick-Watch (+ Button) im Drawer: ein-Klick Spieler zur Watchlist
  els.drawerBody?.addEventListener("click", async (e) => {
    // Chart-Zeitraum umschalten (24h / 7d / 30d)
    const rangeBtn = e.target.closest?.("[data-chart-hours]");
    if (rangeBtn) {
      drawerHours = parseInt(rangeBtn.dataset.chartHours, 10) || 24;
      refreshDetail();
      return;
    }
    const btn = e.target.closest?.(".p-quick-watch");
    if (!btn) return;
    e.preventDefault();
    const sid  = btn.dataset.quickWatchSid;
    const name = btn.dataset.quickWatchName;
    if (!sid || !name) return;
    // Optimistic UI: Button sofort disablen, visual feedback
    btn.disabled = true;
    btn.textContent = "…";
    btn.style.opacity = "0.5";
    try {
      await api(`/api/server/${sid}/watch`,
                { method: "POST", body: JSON.stringify({ name }) });
      // Watchlist im Hintergrund neu laden, dann Drawer re-rendern
      await loadServers();
      await refreshDetail();
    } catch (err) {
      btn.disabled = false;
      btn.textContent = "+";
      btn.style.opacity = "";
      alert("Couldn't add: " + err.message);
    }
  });

  // ─── Online-Rhythmus (7 Tage × 24 Stunden, lokale Zeitzone des Viewers) ──
  // API liefert UTC-Intervalle; das Einsortieren in Wochentag/Stunde passiert
  // hier, damit die Heatmap in der Zeitzone des Betrachters stimmt.
  const RHYTHM_DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const RHYTHM_LEVEL_LIMITS = [15, 30, 45];   // Minuten-Obergrenzen fuer lvl-1..3, darueber lvl-4
  const MINUTES_PER_HOUR = 60;
  let rhythmRequest = 0;

  const pad2 = (n) => String(n).padStart(2, "0");

  function nextLocalHour(ts) {
    const d = new Date(ts * 1000);
    const next = new Date(d.getFullYear(), d.getMonth(), d.getDate(), d.getHours() + 1).getTime() / 1000;
    return next > ts ? next : ts + 3600;   // Schutz gegen DST-Randfaelle
  }

  function bucketRhythm(intervals) {
    const seconds = RHYTHM_DAY_LABELS.map(() => new Array(24).fill(0));
    for (const [start, end] of intervals) {
      for (let t = start; t < end; ) {
        const d = new Date(t * 1000);
        const stop = Math.min(end, nextLocalHour(t));
        seconds[(d.getDay() + 6) % 7][d.getHours()] += stop - t;
        t = stop;
      }
    }
    // Echte Summe je lokaler Stunde: bei DST-Rueckstellung kann eine Zelle >60 min haben
    return seconds.map(row => row.map(sec => Math.round(sec / 60)));
  }

  function rhythmLevel(minutes) {
    minutes = Math.min(MINUTES_PER_HOUR, minutes);   // Schattierung bleibt auf 60 min gedeckelt
    if (minutes <= 0) return 0;
    const idx = RHYTHM_LEVEL_LIMITS.findIndex(limit => minutes <= limit);
    return idx === -1 ? RHYTHM_LEVEL_LIMITS.length + 1 : idx + 1;
  }

  function renderRhythm(data) {
    if (!data.available) {
      return `<p class="rhythm-empty">No online sessions recorded for this player in the last ${data.days} days.</p>`;
    }
    const grid = bucketRhythm(data.intervals || []);
    const hourHead = Array.from({ length: 24 }, (_, h) => `<span class="rhythm-hour">${pad2(h)}</span>`).join("");
    const rows = grid.map((row, day) =>
      `<span class="rhythm-day">${RHYTHM_DAY_LABELS[day]}</span>` +
      row.map((min, h) =>
        `<span class="rhythm-cell lvl-${rhythmLevel(min)}" title="${RHYTHM_DAY_LABELS[day]} ${pad2(h)}:00 — ${min} min online"></span>`
      ).join("")
    ).join("");
    const legend = [0, 1, 2, 3, 4].map(l => `<span class="rhythm-cell lvl-${l}"></span>`).join("");
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "local time";
    const sources = (data.sources || []).join(" + ");
    return `
      <div class="rhythm-grid"><span></span>${hourHead}${rows}</div>
      <div class="rhythm-legend">
        <span>less</span>${legend}<span>more</span>
        <span class="rhythm-meta">last ${data.days} days · ${fmt.escape(tz)} · source: ${fmt.escape(sources)}</span>
      </div>`;
  }

  async function openRhythm(watchId, name) {
    if (!Number.isInteger(watchId) || watchId <= 0) return;
    const req = ++rhythmRequest;
    els.rhythmTitle.textContent = name || "Player";
    els.rhythmBody.innerHTML = `<p class="rhythm-status">Loading …</p>`;
    els.rhythmModal.hidden = false;
    try {
      const data = await api(`/api/me/players/${watchId}/rhythm`);
      if (req !== rhythmRequest) return;
      els.rhythmBody.innerHTML = renderRhythm(data);
    } catch (e) {
      if (req !== rhythmRequest) return;
      els.rhythmBody.innerHTML = `<p class="rhythm-status is-error">Error: ${fmt.escape(e.message)}</p>`;
    }
  }

  function closeRhythm() {
    rhythmRequest++;   // laufende Antwort verwerfen
    els.rhythmModal.hidden = true;
  }
  document.querySelectorAll("[data-close-rhythm]").forEach(el =>
    el.addEventListener("click", closeRhythm));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !els.rhythmModal.hidden) closeRhythm();
  });

  async function openDetail(sid) {
    currentDrawer = sid;
    drawerHours = 24;
    els.drawer.hidden = false;
    els.drawerBody.innerHTML = `<p style="color:var(--text-dim)">Loading …</p>`;
    await refreshDetail();
    if (detailTimer) clearInterval(detailTimer);
    detailTimer = setInterval(refreshDetail, 10000);
  }
  function closeDrawer() {
    els.drawer.hidden = true;
    currentDrawer = null;
    if (detailTimer) { clearInterval(detailTimer); detailTimer = null; }
  }
  async function refreshDetail() {
    if (!currentDrawer) return;
    try {
      const d = await api(`/api/server/${currentDrawer}?hours=${drawerHours}`);
      els.drawerTitle.textContent = d.name || `${d.host}:${d.port}`;
      els.drawerBody.innerHTML = renderDetail(d);
    } catch (e) {
      els.drawerBody.innerHTML = `<p style="color:var(--red)">Error: ${fmt.escape(e.message)}</p>`;
    }
  }

  function renderDetail(d) {
    const snap = d.snapshot || {};
    const watched = (serversCache.find(s => s.id === d.id)?.watched_players || []);
    const watchedSet = new Set(watched.map(w => w.name.toLowerCase()));
    // name → bm_id lookup für Verlinkung. Für nicht-gewatchte Spieler haben wir
    // im Frontend keinen direkten Zugriff auf BM-IDs (siehe Backend für Public-Page).
    const watchedBmid = new Map();
    for (const w of watched) {
      if (w.bm_player_id) watchedBmid.set(w.name.toLowerCase(), w.bm_player_id);
    }
    const nameLink = (name) => {
      const bid = watchedBmid.get((name || "").toLowerCase());
      return bid
        ? `<a class="pname plink" href="/player/${bid}" target="_blank" rel="noopener">${fmt.escape(name)}</a>`
        : `<span class="pname">${fmt.escape(name)}</span>`;
    };
    let players = [];
    try { players = JSON.parse(snap.players_json || "[]"); } catch (e) {}

    // ── Currently online (sortable: A-Z, Z-A) ──
    const sortedPlayers = players.slice();
    if (detailSort.current === "name_asc")
      sortedPlayers.sort((a,b) => (a||"").toLowerCase().localeCompare((b||"").toLowerCase()));
    else if (detailSort.current === "name_desc")
      sortedPlayers.sort((a,b) => (b||"").toLowerCase().localeCompare((a||"").toLowerCase()));
    const anon = !!d.name_anonymized;
    // Gelernte Platzhalter → echter Name (siehe Alias-Lernen im Backend)
    const aliasMap = d.alias_map || {};
    const playerItems = sortedPlayers.map(name => {
      const lc = (name || "").toLowerCase();
      const real = aliasMap[lc];
      const w = watchedSet.has(lc);
      const escName = fmt.escape(name);
      if (real) {
        return `<div class="p is-watched is-alias" title="Platzhalter '${escName}' gehört zu ${fmt.escape(real)}">
                  <span class="pname"><strong>${fmt.escape(real)}</strong>
                  <span class="alias-raw">(${escName})</span></span>
                  <span class="p-watched-mark">★</span>
                </div>`;
      }
      // Auf Streamer-Mode-Servern sind die Namen fake → "+"-Watch bringt nichts,
      // deshalb dort ausblenden.
      const quickBtn = anon ? ""
        : (w
          ? `<span class="p-watched-mark" title="On your watchlist">★</span>`
          : `<button class="p-quick-watch" type="button"
               data-quick-watch-sid="${d.id}"
               data-quick-watch-name="${escName}"
               title="Add ${escName} to your watchlist">+</button>`);
      return `<div class="p ${w ? "is-watched" : ""}">${nameLink(name)}${quickBtn}</div>`;
    }).join("") || `<div style="color:var(--text-dim); padding:6px;">No players online — or the server doesn't expose its list.</div>`;

    // Steam-getrackte Spieler dieses Servers (funktionieren trotz Streamer-Mode)
    const steamWatched = watched.filter(w => w.steam_id);
    const steamRows = steamWatched.map(w => {
      const p = w.steam;
      let cls = "is-offline", meta = "PENDING";
      if (p) {
        if (p.in_rust && p.on_this_server) {
          cls = "is-online";
          meta = w.alias ? `ONLINE HERE · alias „${fmt.escape(w.alias)}"` : "ONLINE HERE";
        }
        else if (p.in_rust) { cls = "is-online"; meta = `IN RUST · ${fmt.escape(p.server_addr || "other")}`; }
        else if (!p.visible) { cls = "is-offline"; meta = "PROFILE PRIVATE"; }
        else { cls = "is-offline"; meta = "OFFLINE / HIDDEN"; }
      }
      return `<div class="watched-player ${cls}" data-watch-id="${w.id}">
        <span class="player-dot ${cls === "is-online" ? "is-online" : ""}"></span>
        <span class="player-name">${fmt.escape((w.steam && w.steam.persona) || w.name)}</span>
        <span class="tag steam-tag">steam</span>
        <span class="player-meta">${meta}</span>
        <button class="btn-remove" title="Remove" data-remove-watch="${d.id}:${w.id}">×</button>
      </div>`;
    }).join("");

    const anonBanner = anon ? `
      <div class="anon-warn anon-warn-compact" style="margin-bottom:12px;" title="Streamer mode active (censorplayerlist) — the online list below shows random placeholder names, not real players. To track someone here, add them by SteamID64 or profile URL — Steam presence works regardless of streamer mode (needs their profile game details set to public).">
        ⚠ <strong>Streamer mode</strong> — names below are fake, use SteamID64 to track <span class="anon-warn-info">ⓘ</span>
      </div>` : "";

    const steamBox = `
      <section class="detail-section">
        <h3>Steam-tracked players (${steamWatched.length})</h3>
        ${steamRows || `<div style="color:var(--text-dim); padding:6px;">None yet — add a SteamID64 or profile URL below to track someone even under streamer mode.</div>`}
        <form class="add-watch-form" data-add-watch="${d.id}" style="margin-top:8px;">
          <input type="text" placeholder="SteamID64 or steamcommunity.com/… profile URL" required />
          <button type="submit">+ Track via Steam</button>
        </form>
      </section>`;

    // ── Active sessions (sortable: longest active, recently joined, A-Z) ──
    const now = Math.floor(Date.now() / 1000);
    const activeSorted = (d.active_sessions || []).slice();
    if (detailSort.active === "duration_desc")
      activeSorted.sort((a,b) => (now - a.start_ts < now - b.start_ts ? 1 : -1));
    else if (detailSort.active === "start_desc")
      activeSorted.sort((a,b) => b.start_ts - a.start_ts);
    else if (detailSort.active === "name_asc")
      activeSorted.sort((a,b) => a.player_name.toLowerCase().localeCompare(b.player_name.toLowerCase()));
    const activeRows = activeSorted.map(s => {
      const dur = now - s.start_ts;
      return `<div class="sess"><span class="sname">${nameLink(s.player_name)}</span><span class="sstart">since ${fmt.timeShort(s.start_ts)}</span><span class="sdur">${fmt.duration(dur)}</span></div>`;
    }).join("");

    // ── Past sessions (sortable: recently ended, longest, A-Z) ──
    const pastSorted = (d.recent_sessions || []).slice();
    if (detailSort.past === "ended_desc")
      pastSorted.sort((a,b) => b.end_ts - a.end_ts);
    else if (detailSort.past === "duration_desc")
      pastSorted.sort((a,b) => (b.end_ts - b.start_ts) - (a.end_ts - a.start_ts));
    else if (detailSort.past === "name_asc")
      pastSorted.sort((a,b) => a.player_name.toLowerCase().localeCompare(b.player_name.toLowerCase()));
    const closedRows = pastSorted.map(s => {
      const dur = s.end_ts - s.start_ts;
      return `<div class="sess"><span class="sname">${nameLink(s.player_name)}</span><span class="sstart">${fmt.timeShort(s.start_ts)}–${fmt.timeShort(s.end_ts)}</span><span class="sdur">${fmt.duration(dur)}</span></div>`;
    }).join("");

    const sortSelectHTML = (target, options, current) =>
      `<select class="sort-select" data-sort-target="${target}">${
        options.map(([v,l]) => `<option value="${v}"${v===current?" selected":""}>${fmt.escape(l)}</option>`).join("")
      }</select>`;

    const wipeStr = d.wipe?.days_until_next != null
      ? `next wipe in ~${d.wipe.days_until_next} days` : "—";

    // Verlaufs-Statistik unter dem Chart: Peak / Durchschnitt / Uptime
    let histStats = "";
    {
      const h = d.history || [];
      if (h.length >= 2) {
        const vals = h.map(r => r.players_count ?? 0);
        const peak = Math.max(...vals);
        const avg  = Math.round(vals.reduce((a,b) => a+b, 0) / vals.length);
        const up   = Math.round(100 * h.filter(r => r.online).length / h.length);
        histStats = `<div class="hist-stats">
          <span title="Highest player count in this window">peak <strong>${peak}</strong></span>
          <span title="Average player count in this window">avg <strong>${avg}</strong></span>
          <span title="Share of polls where the server answered">uptime <strong>${up}%</strong></span>
        </div>`;
      }
    }

    // Server blockt die oeffentliche Spielerliste (A2S_PLAYER) komplett:
    // Count > 0, aber keine Namen — und KEINE erkannte Anonymisierung.
    const listBlocked = !anon && (snap.players_count ?? 0) > 0 && players.length === 0;
    const listBlockedNote = listBlocked
      ? `<div class="list-blocked-note">This server blocks its public player list (A2S_PLAYER) — names can't be shown. Population count still works.</div>`
      : "";

    return `
      <section class="detail-section">
        <h3>Server</h3>
        <dl class="detail-meta">
          <dt>Status</dt><dd>${snap.online ? '<span style="color:var(--green)">● ONLINE</span>' : '<span style="color:var(--red)">● OFFLINE</span>'}</dd>
          <dt>Players</dt><dd>${snap.players_count ?? "—"} / ${snap.max_players ?? "—"}</dd>
          <dt>Map</dt><dd>${fmt.escape(snap.map ?? "—")}</dd>
          <dt>Ping</dt><dd>${snap.ping_ms ?? "—"} ms</dd>
          <dt>Tier</dt><dd>${fmt.escape(d.tier ?? "—")}</dd>
          <dt>Wipe</dt><dd>${wipeStr}</dd>
          <dt>Address</dt><dd>${fmt.escape(d.host)}:${d.port}</dd>
          <dt>Last sync</dt><dd>${fmt.relTime(snap.ts)}</dd>
        </dl>
      </section>
      <section class="detail-section">
        <h3>Population (last ${drawerHours === 24 ? "24h" : drawerHours === 168 ? "7 days" : "30 days"})
          <span class="chart-range">
            ${[[24,"24h"],[168,"7d"],[720,"30d"]].map(([h,l]) =>
              `<button class="chart-range-btn${h===drawerHours?" is-active":""}" data-chart-hours="${h}">${l}</button>`).join("")}
          </span>
        </h3>
        <div class="chart-wrap">${renderChart(d.history || [], snap.max_players)}</div>
        ${histStats}
      </section>
      ${anonBanner}
      ${steamBox}
      <section class="detail-section">
        <h3>Currently online (${players.length})${anon ? ' <span style="color:var(--text-dim); font-weight:400; text-transform:none; letter-spacing:0;">— randomized names, not real</span>' : ''}
          ${sortSelectHTML("current", [["name_asc","A → Z"],["name_desc","Z → A"]], detailSort.current)}
        </h3>
        ${listBlockedNote}
        <div class="player-list"${anon ? ' style="opacity:0.5;"' : ''}>${playerItems}</div>
      </section>
      ${activeRows ? `<section class="detail-section">
        <h3>Active sessions (${activeSorted.length})
          ${sortSelectHTML("active", [["duration_desc","Longest active"],["start_desc","Recently joined"],["name_asc","A → Z"]], detailSort.active)}
        </h3>
        <div class="session-list">${activeRows}</div></section>` : ""}
      ${closedRows ? `<section class="detail-section">
        <h3>Past sessions (${pastSorted.length})
          ${sortSelectHTML("past", [["ended_desc","Recently ended"],["duration_desc","Longest"],["name_asc","A → Z"]], detailSort.past)}
        </h3>
        <div class="session-list">${closedRows}</div></section>` : ""}
      <section class="detail-section">
        <h3>Raw keywords</h3>
        <code style="display:block; white-space:pre-wrap; padding:8px;">${fmt.escape(d.keywords_raw || "—")}</code>
      </section>
    `;
  }

  function renderChart(hist, maxPlayers) {
    if (!hist.length) return `<div style="color:var(--text-dim); padding:24px; text-align:center;">No data yet — collecting.</div>`;
    const W = 600, H = 120, P = 8;
    const xs = hist.map(r => r.ts);
    const ys = hist.map(r => r.players_count ?? 0);
    const xMin = Math.min(...xs), xMax = Math.max(...xs);
    // Y-Skala: Slots des Servers ODER hoechster Messwert (manche Server melden
    // Fake-Pop ueber max_players) — plus 5% Luft, damit die Linie nie am
    // oberen Rand klebt und wie "abgeschnitten" aussieht.
    const yMax = Math.max(1, Number(maxPlayers) || 0, ...ys) * 1.05;
    const xrange = Math.max(1, xMax - xMin);
    const pts = hist.map(r => {
      const x = P + ((r.ts - xMin) / xrange) * (W - 2*P);
      const y = (H - P) - ((r.players_count ?? 0) / yMax) * (H - 2*P);
      return [x, y];
    });
    const path = pts.map((p,i) => `${i===0?"M":"L"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
    const areaPath = path + ` L${P + (W-2*P)},${H-P} L${P},${H-P} Z`;
    const gridY = [0.25, 0.5, 0.75].map(f => {
      const y = (H - P) - f * (H - 2*P);
      const val = Math.round(yMax * f);
      return `<line x1="${P}" y1="${y}" x2="${W-P}" y2="${y}" stroke="#2a2a2a" stroke-dasharray="2 4" />
              <text x="${W-P-2}" y="${y-2}" text-anchor="end" fill="#555" font-size="9">${val}</text>`;
    }).join("");
    return `
      <svg viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="none" xmlns="http://www.w3.org/2000/svg">
        ${gridY}
        <path d="${areaPath}" fill="rgba(205,65,42,0.18)" />
        <path d="${path}" stroke="#cd412a" stroke-width="1.5" fill="none" />
        ${pts.map(p => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="1.5" fill="#e8c428" />`).join("")}
        <text x="${P}" y="12" fill="#555" font-size="9">${new Date(xMin*1000).toLocaleString()}</text>
        <text x="${W-P}" y="12" text-anchor="end" fill="#555" font-size="9">${new Date(xMax*1000).toLocaleString()}</text>
      </svg>
      ${hist.length < 10 ? `<div style="color:var(--text-dim); font-size:11px; text-align:center; padding:4px 0 0;">⏳ Only ${hist.length} snapshot${hist.length === 1 ? "" : "s"} so far — chart fills in as data is collected.</div>` : ""}
    `;
  }

  // ─── Server browser ───────────────────────────────────────────────────
  const BROWSE_PAGE = 100;
  let browseOffset = 0, browseLoadingMore = false;

  function browseQS(forceRefresh, offset = 0) {
    const params = new URLSearchParams();
    if (els.bfQ.value.trim()) params.set("q",       els.bfQ.value.trim());
    if (els.bfRegion.value)   params.set("region",  els.bfRegion.value);
    if (els.bfTier.value)     params.set("tier",    els.bfTier.value);
    if (els.bfSort.value)     params.set("sort",    els.bfSort.value);
    if (els.bfGroup?.value && els.bfGroup.value !== "all") params.set("group", els.bfGroup.value);
    if (els.bfWiped?.value && els.bfWiped.value !== "0")   params.set("max_wipe_h", els.bfWiped.value);
    if (els.bfMinPop.value)   params.set("min_pop", els.bfMinPop.value);
    // Seitenweise laden: 1000 Zeilen auf einmal waren ~400 KB pro Klick und
    // ~700 KB innerHTML — auf dem Handy spuerbar. Rest kommt beim Scrollen.
    params.set("limit", String(BROWSE_PAGE));
    if (offset) params.set("offset", String(offset));
    return "/api/browse?" + params.toString() + (forceRefresh ? "&_=" + Date.now() : "");
  }

  function browseStatusText(data, shown) {
    const ageStr = data.cache_age_sec != null
                 ? ` (cached ${data.cache_age_sec}s ago)`
                 : data.fetch_secs ? ` (fetched in ${data.fetch_secs}s)` : "";
    return `${shown.toLocaleString()} of ${data.matched.toLocaleString()} shown · ` +
           `${data.total_online.toLocaleString()} online · ` +
           `source: ${data.source || "unknown"}${ageStr}`;
  }

  async function loadBrowse(forceRefresh = false) {
    els.browseLoading.hidden = false;
    els.browseStatus.textContent = "loading …";
    browseOffset = 0;
    try {
      const data = await api(browseQS(forceRefresh, 0));
      browseCache = data;
      renderBrowse(data);
      browseOffset = (data.servers || []).length;
      els.browseStatus.textContent = browseStatusText(data, browseOffset);
    } catch (e) {
      els.browseResults.innerHTML = `<div class="browse-empty" style="color:var(--red)">Error: ${fmt.escape(e.message)}</div>`;
      els.browseStatus.textContent = "Error loading";
    } finally {
      els.browseLoading.hidden = true;
    }
  }

  async function loadBrowseMore() {
    if (browseLoadingMore || !browseCache || !browseCache.has_more) return;
    browseLoadingMore = true;
    try {
      const data = await api(browseQS(false, browseOffset));
      const rows = data.servers || [];
      if (rows.length) {
        els.browseResults.insertAdjacentHTML("beforeend", browseRowsHTML(rows));
        browseOffset += rows.length;
        browseCache.has_more = data.has_more;
        browseCache.servers = browseCache.servers.concat(rows);
        els.browseStatus.textContent = browseStatusText(data, browseOffset);
      } else {
        browseCache.has_more = false;
      }
    } catch (e) {
      /* stiller Fehlschlag beim Nachladen — die erste Seite steht ja */
    } finally {
      browseLoadingMore = false;
    }
  }

  // Nachladen, sobald der Nutzer ans Ende scrollt
  window.addEventListener("scroll", () => {
    if (!browseCache || !browseCache.has_more) return;
    if (els.browseResults.hidden) return;
    const rect = els.browseResults.getBoundingClientRect();
    if (rect.bottom - window.innerHeight < 600) loadBrowseMore();
  }, { passive: true });

  // Spalte → Backend-Sortier-Werte. Klick auf einen Spaltenkopf togglet
  // asc/desc; erste Sortierung pro Spalte nimmt die sinnvolle Default-Richtung.
  // Manuell recherchierte Logos fuer bekannte Server-Communities (Top-Server nach Pop).
  // Kein Auto-Fetch moeglich: weder Steam Web API noch BattleMetrics liefern ein Logo-Feld.
  const BRAND_LOGOS = [
    { match: "atlas",      logo: "/logos/atlas.png" },
    { match: "rustreborn", logo: "/logos/rustreborn.png" },
    { match: "survivors.gg", logo: "/logos/survivors.png" },
    { match: "warbandits", logo: "/logos/warbandits.svg" },
    { match: "rustoria",   logo: "/logos/rustoria.png" },
    { match: "rustafied",  logo: "/logos/rustafied.png" },
    { match: "thisisrust", logo: "/logos/thisisrust.png" },
    { match: "rustburg",   logo: "/logos/rustburg.png" },
  ];
  function brandLogoFor(name) {
    const n = (name || "").toLowerCase();
    const hit = BRAND_LOGOS.find(b => n.includes(b.match));
    return hit ? hit.logo : null;
  }

  const SORT_COLS = {
    name: { asc: "name",    desc: "name_desc", first: "asc"  },
    pop:  { asc: "pop_asc", desc: "pop_desc",  first: "desc" },
    map:  { asc: "map",     desc: "map_desc",  first: "asc"  },
    wipe: { asc: "wipe",    desc: "wipe_desc", first: "asc"  },
  };

  function sortIndicator(col) {
    const c = SORT_COLS[col];
    const cur = els.bfSort.value;
    if (cur === c.asc)  return `<span class="sort-arrow">▲</span>`;
    if (cur === c.desc) return `<span class="sort-arrow">▼</span>`;
    return "";
  }

  function renderBrowse(data) {
    const rows = data.servers || [];
    if (!rows.length) {
      els.browseResults.innerHTML = `<div class="browse-empty">No servers match your filters.</div>`;
      return;
    }
    const header = `
      <div class="browse-row head">
        <div class="sortable" data-sort="name" title="Sort by name">Server${sortIndicator("name")}</div>
        <div class="sortable" data-sort="pop" style="text-align:right;" title="Sort by population">Pop${sortIndicator("pop")}</div>
        <div class="sortable" data-sort="map" title="Sort by map">Map${sortIndicator("map")}</div>
        <div class="sortable" data-sort="wipe" title="Sort by next wipe">Wipe${sortIndicator("wipe")}</div>
        <div style="text-align:right;">Action</div>
      </div>`;
    els.browseResults.innerHTML = header + browseRowsHTML(rows);
  }

  // Zeilen-HTML separat, damit Erst-Render und Nachladen dieselbe Funktion nutzen
  function browseRowsHTML(rows) {
    return rows.map(s => {
      const tags = [];
      if (s.tier) tags.push(`<span class="tag tier-${s.tier}">${fmt.escape(s.tier)}</span>`);
      if (s.region) tags.push(`<span class="tag region">${fmt.escape(String(s.region).toUpperCase())}</span>`);
      // Gruppenlimit-Chip (aus Name/Tags erkannt): solo/duo/trio/quad/max N
      if (s.max_group != null) {
        const GL = {1:"solo", 2:"duo", 3:"trio", 4:"quad"};
        const lbl = s.max_group === 0 ? "no limit" : (GL[s.max_group] || `max ${s.max_group}`);
        tags.push(`<span class="tag group" title="Max group size (detected from server name/tags)">${lbl}</span>`);
      }
      // Fresh-Wipe-Badge: in den ersten 48h nach Wipe
      const lw = s.wipe?.last_wipe_ts;
      const wipeAgeH = lw ? Math.floor((Date.now()/1000 - lw) / 3600) : null;
      if (wipeAgeH != null && wipeAgeH >= 0 && wipeAgeH < 48) {
        tags.push(`<span class="tag fresh" title="Wiped ${wipeAgeH}h ago">wiped ${wipeAgeH}h</span>`);
      }
      // Pop-Verdacht: mehr Spieler als Slots ohne Queue = vermutlich Fake-Pop
      if (s.pop_suspect) {
        tags.push(`<span class="tag suspect" title="Reports more players than slots without a queue — population may be faked">⚠ pop?</span>`);
      }
      for (const t of (s.tags || []).slice(0, 3)) {
        if (t === s.tier) continue;
        tags.push(`<span class="tag">${fmt.escape(t)}</span>`);
      }
      const wipeStr = s.wipe?.days_until_next != null ? `in ${s.wipe.days_until_next}d` : "—";
      const queueStr = s.queued > 0 ? `<span class="queue">+${s.queued}q</span>` : "";
      const btn = s.in_watchlist
        ? `<button class="btn-watch is-watched" disabled>✓ Watching</button>`
        : `<button class="btn-watch" data-add-from-browse="${fmt.escape(s.host)}:${s.port}">+ Watch</button>`;
      return `
        <div class="browse-row ${s.in_watchlist ? "is-watched" : ""}">
          <div class="brow-name">
            ${brandLogoFor(s.name) ? `<img class="brow-logo" src="${brandLogoFor(s.name)}" alt="" loading="lazy" />` : ""}
            <a class="brow-link" href="/server/${fmt.escape(encodeURIComponent(s.host))}:${Number(s.port)}"><strong>${fmt.escape(s.name)}</strong></a>
            <div class="brow-tags">${tags.join("")}</div>
          </div>
          <div class="brow-pop">${s.players_count}<span class="max">/${s.max_players}</span>${queueStr}</div>
          <div class="brow-map">${fmt.escape(s.map || "—")}</div>
          <div class="brow-wipe">${wipeStr}</div>
          <div class="brow-actions">${btn}</div>
        </div>`;
    }).join("");
  }

  let filterTimer = null;
  function scheduleFilterReload() {
    if (filterTimer) clearTimeout(filterTimer);
    filterTimer = setTimeout(() => loadBrowse(false), 250);
  }
  [els.bfQ, els.bfMinPop].forEach(el => el?.addEventListener("input", scheduleFilterReload));
  [els.bfRegion, els.bfTier, els.bfSort, els.bfGroup, els.bfWiped].forEach(el => el?.addEventListener("change", () => loadBrowse(false)));
  els.bfRefresh.addEventListener("click", () => loadBrowse(true));

  els.browseResults.addEventListener("click", async (e) => {
    // Klick auf einen sortierbaren Spaltenkopf → Sortierung togglen.
    // Cache-Key ist nur die Region, d.h. der Re-Fetch trifft den Server-Cache
    // und zählt nicht gegen das Browse-Rate-Limit.
    const head = e.target.closest("[data-sort]");
    if (head) {
      const c = SORT_COLS[head.dataset.sort];
      if (!c) return;
      const cur = els.bfSort.value;
      els.bfSort.value = cur === c[c.first]
        ? (c.first === "asc" ? c.desc : c.asc)
        : c[c.first];
      loadBrowse(false);
      return;
    }
    const btn = e.target.closest("[data-add-from-browse]");
    if (!btn) return;
    const [host, portStr] = btn.dataset.addFromBrowse.split(":");
    const port = parseInt(portStr, 10);
    btn.disabled = true; btn.textContent = "loading …";
    try {
      await api("/api/servers", { method: "POST", body: JSON.stringify({ host, port }) });
      btn.textContent = "✓ Watching";
      btn.classList.add("is-watched");
      if (browseCache) {
        for (const s of browseCache.servers) {
          if (s.host === host && s.port === port) s.in_watchlist = true;
        }
      }
      loadServers();
    } catch (err) {
      alert(err.message);
      btn.disabled = false; btn.textContent = "+ Watch";
    }
  });

  // Guest clicking + Watch in browser → prompt to sign in
  els.browseResults.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-add-from-browse]");
    if (btn && !isAuthed()) {
      e.stopImmediatePropagation();
      e.preventDefault();
      if (confirm("You need to sign in with Steam to watch servers. Sign in now?")) {
        window.location.href = "/auth/steam/login";
      }
    }
  }, true);

  // ─── Settings form ────────────────────────────────────────────────────
  function showSettingsStatus(msg, ok) {
    if (!els.settingsStatus) return;
    els.settingsStatus.textContent = msg;
    els.settingsStatus.className = "settings-status " + (ok ? "ok" : "error");
    if (msg) setTimeout(() => {
      if (els.settingsStatus.textContent === msg) {
        els.settingsStatus.textContent = "";
        els.settingsStatus.className = "settings-status";
      }
    }, 6000);
  }
  els.settingsForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = {
      discord_webhook: els.setDiscordWebhook.value.trim(),
      notify_online:   els.setNotifyOnline.checked,
      notify_offline:  els.setNotifyOffline.checked,
    };
    try {
      await api("/api/me/settings", { method: "POST", body: JSON.stringify(body) });
      showSettingsStatus("✓ Settings saved", true);
      // refresh /api/me damit der State synchron ist
      await loadMe();
    } catch (err) {
      showSettingsStatus("Error: " + err.message, false);
    }
  });
  els.btnTestWebhook?.addEventListener("click", async () => {
    // Vorher speichern, falls der User die URL grad geändert hat
    const body = {
      discord_webhook: els.setDiscordWebhook.value.trim(),
      notify_online:   els.setNotifyOnline.checked,
      notify_offline:  els.setNotifyOffline.checked,
    };
    showSettingsStatus("sending …", true);
    try {
      await api("/api/me/settings", { method: "POST", body: JSON.stringify(body) });
      const r = await api("/api/me/test-webhook", { method: "POST" });
      if (r.ok) showSettingsStatus("✓ Test message sent — check Discord!", true);
      else      showSettingsStatus("Discord rejected: " + r.message, false);
    } catch (err) {
      showSettingsStatus("Error: " + err.message, false);
    }
  });

  // ─── Wipe-Calendar (ICS-Feed) ────────────────────────────────────────
  function showCalStatus(msg, ok) {
    if (!els.calStatus) return;
    els.calStatus.textContent = msg;
    els.calStatus.className = "settings-status " + (ok ? "ok" : "error");
    if (msg) setTimeout(() => {
      if (els.calStatus.textContent === msg) {
        els.calStatus.textContent = "";
        els.calStatus.className = "settings-status";
      }
    }, 6000);
  }
  async function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try { await navigator.clipboard.writeText(text); return true; }
      catch (e) {/* fall through */}
    }
    // Fallback for non-secure contexts
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) {}
    ta.remove();
    return ok;
  }
  function toggleCalVisibility(input, btn) {
    if (!input || !btn) return;
    const hidden = input.type === "password";
    input.type = hidden ? "text" : "password";
    btn.textContent = hidden ? "Hide" : "Show";
  }
  els.btnCalShow?.addEventListener("click", () => toggleCalVisibility(els.calWebcalUrl, els.btnCalShow));
  els.btnCalShowHttps?.addEventListener("click", () => toggleCalVisibility(els.calHttpsUrl, els.btnCalShowHttps));

  els.btnCalCopy?.addEventListener("click", async () => {
    const url = els.calWebcalUrl?.value || "";
    if (!url) return;
    const ok = await copyToClipboard(url);
    showCalStatus(ok ? "✓ webcal:// URL copied" : "couldn't copy — select manually", ok);
  });
  els.btnCalCopyHttps?.addEventListener("click", async () => {
    const url = els.calHttpsUrl?.value || "";
    if (!url) return;
    const ok = await copyToClipboard(url);
    showCalStatus(ok ? "✓ HTTPS URL copied" : "couldn't copy — select manually", ok);
  });
  els.btnCalReset?.addEventListener("click", async () => {
    if (!confirm("Generate a new subscribe link? Existing subscribers (e.g. your calendar app) will stop receiving updates and need to be re-added.")) return;
    showCalStatus("regenerating …", true);
    try {
      const r = await api("/api/me/calendar/reset", { method: "POST" });
      if (els.calWebcalUrl) els.calWebcalUrl.value = r.webcal_url || "";
      if (els.calHttpsUrl)  els.calHttpsUrl.value  = r.url || "";
      if (els.calWebcalOpen) els.calWebcalOpen.href = r.webcal_url || "#";
      showCalStatus("✓ new link generated", true);
    } catch (err) {
      showCalStatus("Error: " + err.message, false);
    }
  });

  // ─── My Rust Stats (Steam GetUserStatsForGame) ───────────────────────
  const mystats = {
    guest:    document.getElementById("mystats-guest"),
    body:     document.getElementById("mystats-body"),
    loading:  document.getElementById("mystats-loading"),
    content:  document.getElementById("mystats-content"),
    privBox:  document.getElementById("mystats-private"),
    errBox:   document.getElementById("mystats-error"),
    refresh:  document.getElementById("btn-mystats-refresh"),
  };
  function fmtIntStats(n) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString("en-US").replace(/,/g, " ");
  }
  function fmtDuration(s) {
    if (!s) return "—";
    s = Number(s);
    if (s < 60) return s + "s";
    if (s < 3600) return Math.floor(s/60) + "m";
    if (s < 86400) {
      const h = Math.floor(s/3600); const m = Math.floor((s%3600)/60);
      return m ? h+"h "+String(m).padStart(2,"0")+"m" : h+"h";
    }
    const d = Math.floor(s/86400); const h = Math.floor((s%86400)/3600);
    return h ? d+"d "+h+"h" : d+"d";
  }
  function statCard(label, value, sub) {
    return '<div class="stat-card">'
      + '<div class="stat-num">' + fmt.escape(String(value)) + '</div>'
      + '<div class="stat-label">' + fmt.escape(label) + '</div>'
      + (sub ? '<div class="stat-sub">' + fmt.escape(sub) + '</div>' : '')
      + '</div>';
  }
  function statRow(label, value) {
    return '<div class="stat-row"><span>' + fmt.escape(label)
         + '</span><span class="stat-row-v">' + fmt.escape(String(value)) + '</span></div>';
  }
  function renderMyStatsBlock(row) {
    const kills  = row.kill_player || 0;
    const deaths = row.deaths || 0;
    const kd     = deaths ? (kills / deaths).toFixed(2) : "—";
    const hs     = row.headshot || 0;
    const hsPct  = kills ? (100 * hs / kills).toFixed(1) + "%" : "—";
    const bf     = row.bullet_fired || 0;
    const bhp    = row.bullet_hit_player || 0;
    const acc    = bf ? (100 * bhp / bf).toFixed(1) + "%" : "—";

    const cards = [
      statCard("Kills",     fmtIntStats(kills),  "K/D " + kd),
      statCard("Deaths",    fmtIntStats(deaths)),
      statCard("Headshots", fmtIntStats(hs),     hsPct + " of kills"),
      statCard("Hit rate",  acc,                 fmtIntStats(bf) + " fired"),
    ].join("");

    const bulletHits = [
      ["Players",   row.bullet_hit_player],
      ["Buildings", row.bullet_hit_building],
      ["Signs",     row.bullet_hit_sign],
      ["Wolves",    row.bullet_hit_wolf],
      ["Bears",     row.bullet_hit_bear],
      ["Boars",     row.bullet_hit_boar],
      ["Stags",     row.bullet_hit_stag],
      ["Horses",    row.bullet_hit_horse],
      ["Corpses",   row.bullet_hit_corpse],
    ].filter(([_,v]) => v).map(([l,v]) => statRow(l, fmtIntStats(v))).join("");
    const bulletList = bulletHits ?
      '<div class="stat-list"><div class="stat-list-title">Bullets hit, by target</div>' + bulletHits + '</div>' : '';

    const harvest = [
      ["Wood",       row.harvested_wood],
      ["Stones",     row.harvested_stones],
      ["Cloth",      row.harvested_cloth],
      ["Leather",    row.harvested_leather],
      ["Sulfur ore", row.harvested_sulfur_ore],
      ["Metal ore",  row.harvested_metal_ore],
      ["HQ metal",   row.harvested_hq_metal_ore],
      ["Scrap",      row.acquired_scrap],
      ["Sulfur (gathered)", row.acquired_sulfur],
      ["Metal frags",row.acquired_metalfrag],
      ["Low-grade fuel", row.acquired_lowgradefuel],
    ].filter(([_,v]) => v).map(([l,v]) => statRow(l, fmtIntStats(v))).join("");
    const harvestList = harvest ?
      '<div class="stat-list"><div class="stat-list-title">Harvested / acquired</div>' + harvest + '</div>' : '';

    const misc = [
      ["Playtime (tracked by Rust)", fmtDuration(row.seconds_played)],
      ["Wounded",        fmtIntStats(row.wounded)],
      ["C4 thrown",      fmtIntStats(row.c4_thrown)],
      ["Rockets fired",  fmtIntStats(row.rocket_fired)],
      ["Melee thrown",   fmtIntStats(row.melee_thrown)],
      ["Arrows fired",   fmtIntStats(row.arrow_fired)],
      ["Arrows hit",     fmtIntStats(row.arrow_hit_player)],
      ["Time cold",      fmtDuration(row.seconds_cold)],
      ["Time hot",       fmtDuration(row.seconds_hot)],
      ["Time comfy",     fmtDuration(row.seconds_comfort)],
    ].filter(([_,v]) => v && v !== "—" && v !== "0").map(([l,v]) => statRow(l, v)).join("");
    const miscList = misc ?
      '<div class="stat-list"><div class="stat-list-title">Other</div>' + misc + '</div>' : '';

    const fetched = row.fetched_at || 0;
    const age = fetched ? Math.max(0, Math.floor(Date.now()/1000) - fetched) : null;
    let ageStr = "";
    if (age === null) ageStr = "";
    else if (age < 60) ageStr = "just now";
    else if (age < 3600) ageStr = Math.floor(age/60) + "m ago";
    else ageStr = Math.floor(age/3600) + "h ago";
    const footer = ageStr
      ? '<p class="hint" style="text-align:right; font-size:11px; margin-top:8px;">counters from Steam · cached ' + fmt.escape(ageStr) + '</p>'
      : '';

    return '<div class="stat-cards">' + cards + '</div>'
         + '<div class="stat-lists">' + bulletList + harvestList + miscList + '</div>'
         + footer;
  }
  let mystatsLoaded = false;
  async function loadMyStats(force) {
    if (!mystats.body) return;
    if (!isAuthed()) {
      if (mystats.guest) mystats.guest.hidden = false;
      mystats.body.hidden = true;
      return;
    }
    if (mystats.guest) mystats.guest.hidden = true;
    mystats.body.hidden = false;
    if (mystatsLoaded && !force) return;
    mystats.loading.hidden = false;
    mystats.content.hidden = true;
    mystats.privBox.hidden = true;
    mystats.errBox.hidden  = true;
    try {
      const endpoint = force ? "/api/me/stats/refresh" : "/api/me/stats";
      const opts = force ? { method: "POST" } : {};
      const data = await api(endpoint, opts);
      mystats.loading.hidden = true;
      if (data.error) {
        mystats.errBox.textContent = data.error;
        mystats.errBox.hidden = false;
        return;
      }
      if (data.is_private) {
        mystats.privBox.hidden = false;
        return;
      }
      mystats.content.innerHTML = renderMyStatsBlock(data);
      mystats.content.hidden = false;
      mystatsLoaded = true;
    } catch (e) {
      mystats.loading.hidden = true;
      mystats.errBox.textContent = "Failed to load stats: " + e.message;
      mystats.errBox.hidden = false;
    }
  }
  mystats.refresh?.addEventListener("click", () => {
    mystatsLoaded = false;
    loadMyStats(true);
  });

  // ─── Rust Flap mini-game integration ─────────────────────────────────
  const playEls = {
    iframe:      document.getElementById("game-iframe"),
    bestValue:   document.getElementById("play-best-value"),
    bestSub:     document.getElementById("play-best-sub"),
    leaderboard: document.getElementById("play-leaderboard"),
  };
  let playMyBest = 0;
  let playLeaderboardLoaded = false;

  function renderLeaderboard(board, myBest, myUserId) {
    if (!playEls.leaderboard) return;
    if (!board || board.length === 0) {
      playEls.leaderboard.innerHTML =
        '<li class="hint" style="text-align:center; padding:14px;">No scores yet — be the first.</li>';
      return;
    }
    const rows = board.map((p, i) => {
      const rank = i + 1;
      const rankCls = rank === 1 ? "gold" : rank === 2 ? "silver" : rank === 3 ? "bronze" : "";
      const isMe = myUserId && String(p.id) === String(myUserId);
      const avatar = (p.avatar_url && isSafeSteamUrl(p.avatar_url))
        ? `<img class="lb-avatar" src="${p.avatar_url}" alt="" loading="lazy" />`
        : `<span class="lb-avatar"></span>`;
      const name = fmt.escape(p.display_name || String(p.id));
      return `<li${isMe ? ' class="me"' : ""}>
        <span class="lb-rank ${rankCls}">${rank}</span>
        ${avatar}
        <span class="lb-name">${name}</span>
        <span class="lb-score">${Number(p.best || 0).toLocaleString("en-US").replace(/,/g, " ")}</span>
      </li>`;
    }).join("");
    playEls.leaderboard.innerHTML = rows;
  }

  function updateMyBestDisplay(best, authenticated) {
    if (!playEls.bestValue) return;
    if (best && best > 0) {
      playEls.bestValue.textContent = Number(best).toLocaleString("en-US").replace(/,/g, " ");
      playEls.bestSub.textContent = authenticated
        ? "synced to your account"
        : "sign in to save it";
    } else {
      playEls.bestValue.textContent = "—";
      playEls.bestSub.textContent = authenticated
        ? "play once to set your best"
        : "sign in to track your high-score";
    }
  }

  async function loadPlayLeaderboard() {
    if (!playEls.leaderboard) return;
    try {
      const data = await api("/api/game/flap/leaderboard");
      playMyBest = data.my_best || 0;
      const myUserId = isAuthed() ? me.user_id : null;
      renderLeaderboard(data.top || [], playMyBest, myUserId);
      updateMyBestDisplay(playMyBest, isAuthed());
      playLeaderboardLoaded = true;
      // Send personal best into the iframe so the game shows the correct "BESTE" value
      const ifr = playEls.iframe;
      if (ifr && ifr.contentWindow) {
        try { ifr.contentWindow.postMessage({ type: "rustflap_set_best", best: playMyBest }, "*"); }
        catch (e) {}
      }
    } catch (e) {
      playEls.leaderboard.innerHTML =
        '<li class="hint" style="text-align:center; padding:14px; color:var(--red)">Couldn\'t load leaderboard.</li>';
    }
  }

  // postMessage-Bridge: empfange Events vom game-iframe
  window.addEventListener("message", async (e) => {
    // Ohne Origin-Pruefung koennte eine fremde Seite per window.open() +
    // postMessage beliebige Spielstaende im Namen des eingeloggten Users posten.
    if (e.origin !== window.location.origin) return;
    if (playEls.iframe && e.source !== playEls.iframe.contentWindow) return;
    if (!e.data || typeof e.data !== "object") return;
    if (e.data.type === "rustflap_ready") {
      // Iframe geladen — schick aktuellen best (falls schon geladen)
      const ifr = playEls.iframe;
      if (ifr && ifr.contentWindow && playMyBest > 0) {
        try { ifr.contentWindow.postMessage({ type: "rustflap_set_best", best: playMyBest }, "*"); }
        catch (e) {}
      }
    }
    if (e.data.type === "rustflap_die") {
      const score = Number(e.data.score || 0);
      const duration_ms = e.data.duration_ms ? Number(e.data.duration_ms) : null;
      if (!isAuthed()) return;       // can't submit without auth
      if (!score || score <= 0) return;
      // Submit only if score > current personal best (small perf optimization)
      try {
        const r = await api("/api/game/flap/score", {
          method: "POST",
          body: JSON.stringify({ score, duration_ms }),
        });
        if (r && r.ok) {
          playMyBest = r.best || score;
          updateMyBestDisplay(playMyBest, true);
          await loadPlayLeaderboard();  // refresh top-10
        }
      } catch (err) { /* silently ignore — game continues */ }
    }
  });

  // ─── Cookie banner ────────────────────────────────────────────────────
  const COOKIE_ACK_KEY = LS_PREFIX + "cookieAck";
  function setupCookieBanner() {
    try {
      if (localStorage.getItem(COOKIE_ACK_KEY) === "1") return;
      els.cookieBanner.hidden = false;
      els.cookieAck.addEventListener("click", () => {
        try { localStorage.setItem(COOKIE_ACK_KEY, "1"); } catch (e) {}
        els.cookieBanner.hidden = true;
      });
    } catch (e) {}
  }

  // ─── Init ─────────────────────────────────────────────────────────────
  setupCookieBanner();
  setNotifBtn();
  (async () => {
    await loadMe();
    loadServers();
    loadFriends();
    setInterval(loadServers, REFRESH_MS);
    setInterval(loadFriends, 60000);   // Presence aendert sich minuetlich
  })();
  function savedTab() {
    try {
      const saved = localStorage.getItem(LS_PREFIX + "tab");
      return VALID_TABS.includes(saved) ? saved : null;
    } catch (e) { return null; }
  }
  function queryTab() {
    const tab = new URLSearchParams(location.search).get("tab");
    return VALID_TABS.includes(tab) ? tab : null;
  }
  const initialTab = PATH_TABS[location.pathname] || queryTab() || savedTab();
  if (initialTab) switchTab(initialTab, "replace");
})();
