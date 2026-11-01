// Light Show Studio player card: drop xLights .fseq shows (and songs) on it, press play. Ships with light_show_studio.
//
//   type: custom:light-show-studio-player-card
//   entity: media_player.light_show_player   # optional: autodetected (the media_player with a "queue" attribute)
//   title: Light shows                       # optional
//   mode: player | upload                    # optional: "player" hides the add-shows area, "upload" shows only it

const ICON = {
  play: "M8 5v14l11-7z",
  pause: "M6 19h4V5H6zm8-14v14h4V5z",
  stop: "M6 6h12v12H6z",
  prev: "M6 6h2v12H6zm3.5 6 8.5 6V6z",
  next: "M6 18l8.5-6L6 6zM16 6v12h2V6z",
  shuffle: "M10.59 9.17 5.41 4 4 5.41l5.17 5.17zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4zm.33 9.41-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04z",
  repeat: "M7 7h10v3l4-4-4-4v3H5v6h2zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2z",
  repeat1: "M7 7h10v3l4-4-4-4v3H5v6h2zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2zm-4-2V9h-1l-2 1v1h1.5v4z",
  up: "M7.41 15.41 12 10.83l4.59 4.58L18 14l-6-6-6 6z",
  down: "M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6z",
  del: "M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z",
  note: "M12 3v10.55A4 4 0 1 0 14 17V7h4V3z",
  upload: "M9 16h6v-6h4l-7-7-7 7h4zm-4 2h14v2H5z",
};

const icon = (name, size = 24) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"><path fill="currentColor" d="${ICON[name]}"/></svg>`;

const fmt = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
};

const MAX_BYTES = 300 * 1024 * 1024;

class LightShowStudioPlayerCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("media_player.") && hass.states[id].attributes.queue !== undefined
    );
    return { entity: entity || "", title: "Light shows" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", selector: { entity: { domain: "media_player", integration: "light_show_studio" } } },
        { name: "title", selector: { text: {} } },
        { name: "mode", selector: { select: { mode: "dropdown", options: [
          { value: "", label: "Everything" }, { value: "player", label: "Player only" }, { value: "upload", label: "Add shows only" }] } } },
      ],
    };
  }

  setConfig(config) {
    this._config = { ...config };
    this._built = false;
    this._retryAt = 0;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._detectEntity();
    if (this._built) this._applyVisibility();
    if (this._upload_only()) return;
    this._subscribe();
    if (!this._shows) this._loadShows();
  }

  _upload_only() {
    return this._config?.mode === "upload";
  }

  _isAdmin() {
    return !!this._hass?.user?.is_admin;
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  connectedCallback() {
    this._retryAt = 0;
    if (this._hass && !this._upload_only()) {
      this._subscribe();
      this._startTimer();
    }
  }

  disconnectedCallback() {
    this._unsubscribe();
    clearInterval(this._timer);
    this._timer = null;
  }

  // --- data --------------------------------------------------------------
  _entityId() {
    return this._config?.entity || this._auto || this._state?.entity_id || null;
  }

  _detectEntity() {
    if (this._config?.entity || !this._hass) return;
    if (this._auto && this._hass.states[this._auto]) return; // already found
    const id = Object.keys(this._hass.states).find(
      (k) => k.startsWith("media_player.") && this._hass.states[k].attributes.queue !== undefined
    );
    if (id !== this._auto) {
      this._auto = id;
      this._renderState();
    }
  }

  async _subscribe() {
    if (this._unsub || this._subscribing || !this.isConnected || !this._hass) return;
    if (Date.now() < (this._retryAt || 0)) return; // a failed subscribe waits 10 s (or a new setConfig / reconnect)
    this._subscribing = true;
    try {
      const unsub = await this._hass.connection.subscribeMessage((m) => this._onEvent(m), {
        type: "light_show_studio/player/subscribe",
      });
      if (!this.isConnected) unsub(); // removed from the page while we waited: don't leak the subscription
      else {
        this._unsub = unsub;
        this._noPlayerErr = false;
        this._clearError("sub");
        this._startTimer();
      }
    } catch (err) {
      this._retryAt = Date.now() + 10000;
      this._noPlayerErr = err?.code === "not_found";
      this._error(this._noPlayerErr ? "" : this._msg(err, "Can't reach the light show player. Trying again soon."), "sub");
      this._applyVisibility();
    }
    this._subscribing = false;
  }

  _msg(err, fallback) {
    return (typeof err === "string" ? err : err?.message) || fallback;
  }

  _unsubscribe() {
    if (this._unsub) {
      try {
        this._unsub();
      } catch (e) {
        /* connection already closed */
      }
    }
    this._unsub = null;
  }

  _onEvent(m) {
    const prev = this._state;
    this._state = m;
    this._recv = Date.now(); // progress runs on our own clock, not the server's timestamp
    this._noPlayerErr = false;
    const queueChanged = !prev || JSON.stringify(prev.queue) !== JSON.stringify(m.queue);
    if (queueChanged && this._shows) this._loadShows();
    this._renderState();
  }

  async _loadShows() {
    if (!this._hass) return;
    if (this._loading) {
      this._reload = true; // asked again mid-flight: run once more when this one ends
      return;
    }
    this._loading = true;
    do {
      this._reload = false;
      try {
        const r = await this._hass.callWS({ type: "light_show_studio/shows/list" });
        this._shows = r.shows || [];
        this._clearError("shows");
      } catch (err) {
        this._shows = this._shows || [];
        this._error(this._msg(err, "Can't load the shows"), "shows");
      }
    } while (this._reload);
    this._loading = false;
    this._renderList();
  }

  // shows in queue order (falls back to library order)
  _ordered() {
    const shows = this._shows || [];
    const queue = this._state?.queue;
    if (!queue || !queue.length) return shows;
    const byId = new Map(shows.map((s) => [s.id, s]));
    const out = queue.map((id) => byId.get(id)).filter(Boolean);
    shows.forEach((s) => {
      if (!queue.includes(s.id)) out.push(s);
    });
    return out;
  }

  _position() {
    const s = this._state;
    if (!s) return 0;
    let p = s.position || 0;
    if (s.state === "playing") p += (Date.now() - (this._recv || Date.now())) / 1000;
    return Math.min(Math.max(p, 0), s.duration || p);
  }

  _service(service, data = {}) {
    const entity_id = this._entityId();
    if (!entity_id) return this._error("No light show player found");
    return Promise.resolve(this._hass.callService("media_player", service, { entity_id, ...data })).then(
      () => this._clearError("action"),
      (err) => this._error(this._msg(err, "That didn't work. Try again."))
    );
  }

  // --- actions -----------------------------------------------------------
  _playPause() {
    this._service(this._state?.state === "playing" ? "media_pause" : "media_play");
  }

  _toggleShuffle() {
    this._service("shuffle_set", { shuffle: !this._state?.shuffle });
  }

  _cycleRepeat() {
    const order = ["off", "all", "one"];
    const next = order[(order.indexOf(this._state?.repeat || "off") + 1) % 3];
    this._service("repeat_set", { repeat: next });
  }

  async _setTransition(value) {
    try {
      await this._hass.callWS({ type: "light_show_studio/player/set_transition", transition: value });
      this._clearError("transition");
      if (this._state) this._state.transition = value;
    } catch (err) {
      this._error(this._msg(err, "Couldn't change the transition"), "transition");
      this._renderState();
    }
  }

  _playShow(show) {
    this._service("select_source", { source: show.name });
  }

  async _move(i, dir) {
    const list = this._ordered().map((s) => s.id);
    const j = i + dir;
    if (j < 0 || j >= list.length) return;
    [list[i], list[j]] = [list[j], list[i]];
    // optimistic
    const byId = new Map(this._shows.map((s) => [s.id, s]));
    this._shows = list.map((id) => byId.get(id));
    if (this._state) this._state = { ...this._state, queue: list };
    this._renderList();
    try {
      await this._hass.callWS({ type: "light_show_studio/shows/reorder", order: list });
    } catch (err) {
      this._error(this._msg(err, "Couldn't reorder"));
      this._loadShows();
    }
  }

  async _delete(show) {
    this._confirming = null;
    try {
      await this._hass.callWS({ type: "light_show_studio/shows/delete", show_id: show.id });
      this._shows = (this._shows || []).filter((s) => s.id !== show.id);
      this._renderList();
      this._loadShows();
    } catch (err) {
      this._error(this._msg(err, "Couldn't delete"));
    }
  }

  _seek(frac) {
    const d = this._state?.duration;
    if (!d || this._state.state === "idle") return;
    const pos = Math.round(frac * d * 10) / 10;
    this._state = { ...this._state, position: pos };
    this._recv = Date.now();
    this._service("media_seek", { seek_position: pos });
    this._tick();
  }

  // --- upload ------------------------------------------------------------
  _addFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (!this._isAdmin()) return this._error("Only admins can add shows");
    const ok = /\.(fseq|mp3|m4a|aac|wav|ogg|flac)$/i;
    this._uploads = this._uploads || [];
    const batch = [];
    for (const f of files) {
      const item = { name: f.name, pct: 0, state: "wait", msg: "" };
      if (!ok.test(f.name)) {
        item.state = "error";
        item.msg = "Only .fseq and song files";
      } else if (f.size > MAX_BYTES) {
        item.state = "error";
        item.msg = "Over 300 MB";
      } else {
        batch.push({ f, item });
      }
      this._uploads.push(item);
    }
    this._renderUploads();
    this._runUploads(batch);
  }

  // Uploads run one at a time; each carries its base name as `show`, so Thriller.fseq + Thriller.m4a land in one show.
  async _runUploads(batch) {
    for (const { f, item } of batch) {
      item.state = "up";
      this._renderUploads();
      try {
        await this._upload(f, item);
        item.state = "done";
        item.pct = 100;
      } catch (err) {
        item.state = "error";
        item.msg = err.message || "Upload failed";
      }
      this._renderUploads();
    }
    await this._loadShows();
    setTimeout(() => {
      this._uploads = (this._uploads || []).filter((u) => u.state === "error" || u.state === "up" || u.state === "wait");
      this._renderUploads();
    }, 2500);
  }

  async _token(refresh) {
    const auth = this._hass?.auth;
    if (!auth) throw new Error("Not signed in. Reload the page.");
    if (refresh || auth.expired) await auth.refreshAccessToken();
    return auth.accessToken;
  }

  async _upload(file, item, retried = false) {
    const token = await this._token(retried);
    try {
      await this._post(file, item, token);
    } catch (err) {
      if (err.status === 401 && !retried) return this._upload(file, item, true); // token went stale: refresh once
      throw err;
    }
  }

  _post(file, item, token) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("show", file.name.replace(/\.[^.]+$/, ""));
      form.append("file", file, file.name);
      const fail = (message, status) => reject(Object.assign(new Error(message), { status }));
      xhr.open("POST", "/api/light_show_studio/shows/upload");
      xhr.timeout = 30 * 60 * 1000;
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          item.pct = Math.round((e.loaded / e.total) * 100);
          this._renderUploads();
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) return resolve();
        let server = "";
        try {
          server = JSON.parse(xhr.responseText).message || "";
        } catch (e) {
          /* not JSON */
        }
        if (xhr.status === 403) fail("Only admins can add shows", 403);
        else if (xhr.status === 401) fail(server || "Sign in again, then retry", 401);
        else fail(server || `Upload failed (${xhr.status})`, xhr.status);
      };
      xhr.onerror = () => fail("Network error. Check the connection and try again.", 0);
      xhr.onabort = () => fail("Upload cancelled", 0);
      xhr.ontimeout = () => fail("Upload timed out", 0);
      xhr.send(form);
    });
  }

  // --- render ------------------------------------------------------------
  _clearError(src) {
    if (this._errSrc === src || !this._errSrc) this._error("");
  }

  _error(msg, src = "action") {
    this._errSrc = msg ? src : "";
    const el = this.shadowRoot?.querySelector(".err");
    if (!el) return;
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  _startTimer() {
    if (this._timer || !this.isConnected || this._upload_only()) return;
    this._timer = setInterval(() => this._tick(), 250);
  }

  _tick() {
    const r = this.shadowRoot;
    if (!r || !this._state || this._seeking) return;
    const pos = this._position();
    const dur = this._state.duration || 0;
    r.querySelector(".fill").style.width = dur ? `${Math.min(100, (pos / dur) * 100)}%` : "0%";
    r.querySelector(".time").textContent = `${fmt(pos)} / ${fmt(dur)}`;
    const bar = r.querySelector(".bar");
    bar.setAttribute("aria-valuemax", String(Math.round(dur)));
    bar.setAttribute("aria-valuenow", String(Math.round(pos)));
    bar.setAttribute("aria-valuetext", `${fmt(pos)} of ${fmt(dur)}`);
  }

  // which parts show: mode, admin rights, and whether there is a player at all
  _applyVisibility() {
    const r = this.shadowRoot;
    if (!r || !this._built) return;
    const upload = this._upload_only(), playerOnly = this._config.mode === "player", admin = this._isAdmin();
    const noPlayer = !upload && !this._entityId() && !this._state;
    r.querySelector(".playpart").hidden = upload || noPlayer;
    r.querySelector(".setup").hidden = upload || !noPlayer;
    r.querySelector(".addpart").hidden = playerOnly || !admin;
    r.querySelector(".noadmin").hidden = playerOnly || admin;
    r.querySelector(".uploads").hidden = playerOnly || !admin;
    r.querySelector(".count").hidden = upload || noPlayer;
    if (this._adminShown !== admin) {
      this._adminShown = admin;
      this._renderList();
    }
  }

  _renderState() {
    const r = this.shadowRoot;
    if (!r || !this._built) return;
    this._applyVisibility();
    const s = this._state || { state: "idle", queue: [] };
    const playing = s.state === "playing";
    const show = (this._shows || []).find((x) => x.id === s.show_id);
    const title = r.querySelector(".np-title");
    title.textContent = s.state === "idle" ? "Nothing playing" : show?.name || s.show_id || "Playing";
    title.classList.toggle("dim", s.state === "idle");
    r.querySelector(".np-sub").textContent =
      (s.state === "paused" ? "Paused" : s.state === "playing" ? "Now playing" : "Press play to start the first show") +
      (s.speaker ? ` · ${this._speakerName(s.speaker)}` : " · No music");
    r.querySelector(".pp").innerHTML = icon(playing ? "pause" : "play", 40);
    r.querySelector(".pp").setAttribute("aria-label", playing ? "Pause" : "Play");
    const sh = r.querySelector(".shuf");
    sh.classList.toggle("on", !!s.shuffle);
    sh.setAttribute("aria-pressed", String(!!s.shuffle));
    const rp = r.querySelector(".rep");
    rp.innerHTML = icon(s.repeat === "one" ? "repeat1" : "repeat");
    rp.classList.toggle("on", !!s.repeat && s.repeat !== "off");
    rp.title = `Repeat: ${s.repeat || "off"}`;
    rp.setAttribute("aria-label", rp.title);
    const tr = r.querySelector(".trsel");
    if (tr) {
      if (this.shadowRoot.activeElement !== tr) tr.value = s.transition || "fade";
      tr.disabled = !this._isAdmin();
    }
    const bar = r.querySelector(".bar");
    bar.classList.toggle("idle", s.state === "idle");
    bar.tabIndex = s.state === "idle" ? -1 : 0;
    bar.setAttribute("aria-disabled", String(s.state === "idle"));
    this._tick();
    this._renderList();
  }

  _speakerName(id) {
    return this._hass?.states[id]?.attributes.friendly_name || id;
  }

  _renderUploads() {
    const box = this.shadowRoot?.querySelector(".uploads");
    if (!box) return;
    box.innerHTML = "";
    (this._uploads || []).forEach((u) => {
      const row = document.createElement("div");
      row.className = `up ${u.state}`;
      const name = document.createElement("span");
      name.className = "up-name";
      name.textContent = u.name;
      const st = document.createElement("span");
      st.className = "up-st";
      st.textContent =
        u.state === "error" ? u.msg : u.state === "done" ? "Added" : u.state === "wait" ? "Waiting" : `${u.pct}%`;
      row.append(name, st);
      if (u.state === "error") {
        const x = document.createElement("button");
        x.className = "up-x";
        x.textContent = "×";
        x.title = "Dismiss";
        x.setAttribute("aria-label", `Dismiss ${u.name}`);
        x.onclick = () => {
          this._uploads = this._uploads.filter((v) => v !== u);
          this._renderUploads();
        };
        st.append(x);
      }
      const bar = document.createElement("div");
      bar.className = "up-bar";
      const fill = document.createElement("div");
      fill.style.width = `${u.pct}%`;
      bar.append(fill);
      row.append(bar);
      box.append(row);
    });
  }

  _renderList() {
    const r = this.shadowRoot;
    if (!r || !this._built) return;
    const list = r.querySelector(".list");
    const shows = this._ordered();
    const cur = this._state?.show_id;
    const active = this._state && this._state.state !== "idle";
    const admin = this._isAdmin();
    r.querySelector(".count").textContent = shows.length ? `${shows.length} show${shows.length === 1 ? "" : "s"}` : "";
    list.innerHTML = "";
    if (!this._shows) {
      list.innerHTML = `<div class="empty">Loading shows…</div>`;
      return;
    }
    if (!shows.length) {
      list.innerHTML = admin
        ? `<div class="empty"><b>No shows yet</b><br>In xLights, render your sequence — the .fseq is saved next to the .xsq. Add it below with the song.</div>`
        : `<div class="empty"><b>No shows yet</b><br>Ask an admin to add shows.</div>`;
      return;
    }
    shows.forEach((s, i) => {
      const row = document.createElement("div");
      row.className = "row" + (active && s.id === cur ? " cur" : "");
      const play = document.createElement("button");
      play.className = "ib play";
      play.innerHTML = icon(active && s.id === cur && this._state.state === "playing" ? "pause" : "play", 22);
      play.title = `Play ${s.name}`;
      play.setAttribute("aria-label", `Play ${s.name}`);
      play.onclick = () => {
        if (active && s.id === cur) this._playPause();
        else this._playShow(s);
      };
      const info = document.createElement("div");
      info.className = "info";
      const nm = document.createElement("div");
      nm.className = "nm";
      nm.textContent = s.name;
      const meta = document.createElement("div");
      meta.className = "meta";
      meta.innerHTML = `<span>${fmt(s.duration)}</span>${
        s.has_audio ? `<span class="note" title="Has a song">${icon("note", 13)} song</span>` : `<span>No music</span>`
      }`;
      info.append(nm, meta);
      row.append(play, info);
      if (!admin) {
        list.append(row);
        return;
      }
      if (this._confirming === s.id) {
        const q = document.createElement("span");
        q.className = "q";
        q.textContent = "Delete?";
        const yes = document.createElement("button");
        yes.className = "txt danger";
        yes.textContent = "Delete";
        yes.setAttribute("aria-label", `Delete ${s.name}`);
        yes.onclick = () => this._delete(s);
        const no = document.createElement("button");
        no.className = "txt";
        no.textContent = "Cancel";
        no.onclick = () => {
          this._confirming = null;
          this._renderList();
        };
        row.append(q, yes, no);
      } else {
        const mk = (cls, ic, label, fn, disabled) => {
          const b = document.createElement("button");
          b.className = `ib sm ${cls}`;
          b.innerHTML = icon(ic, 20);
          b.title = label;
          b.setAttribute("aria-label", `${label} ${s.name}`);
          b.disabled = !!disabled;
          b.onclick = fn;
          return b;
        };
        row.append(
          mk("", "up", "Move up", () => this._move(i, -1), i === 0),
          mk("", "down", "Move down", () => this._move(i, 1), i === shows.length - 1),
          mk("del", "del", "Delete", () => {
            this._confirming = s.id;
            this._renderList();
          })
        );
      }
      list.append(row);
    });
  }

  _build() {
    if (!this._config) return;
    this._built = true;
    this._adminShown = undefined;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const touch = !!window.matchMedia?.("(pointer: coarse)").matches;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        [hidden] { display: none !important; }
        ha-card { padding: 12px 16px 16px; box-sizing: border-box; min-width: 0; max-width: 100%; overflow: hidden; }
        * { box-sizing: border-box; }
        button { font: inherit; color: inherit; cursor: pointer; border: none; background: none; padding: 0; margin: 0;
                 -webkit-tap-highlight-color: transparent; }
        button:disabled { opacity: 0.3; cursor: default; }
        :focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        svg { display: block; }
        .drop svg { margin: 0 auto; }
        .head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 10px; }
        .title { font-size: 16px; font-weight: 500; }
        .count { font-size: 12px; color: var(--secondary-text-color); }
        .np { background: var(--secondary-background-color, rgba(127,127,127,.12)); border-radius: 14px;
              padding: 16px 14px 14px; text-align: center; }
        .np-title { font-size: 20px; font-weight: 500; line-height: 1.25; overflow: hidden; text-overflow: ellipsis;
                    white-space: nowrap; color: var(--primary-text-color); }
        .np-title.dim { color: var(--secondary-text-color); }
        .np-sub { font-size: 12px; color: var(--secondary-text-color); margin-top: 2px; overflow: hidden;
                  text-overflow: ellipsis; white-space: nowrap; }
        .bar { position: relative; height: 40px; margin-top: 6px; cursor: pointer; touch-action: pan-y; border-radius: 6px; }
        .bar.idle { cursor: default; opacity: 0.6; }
        .track { position: absolute; left: 0; right: 0; top: 17px; height: 6px; border-radius: 3px;
                 background: var(--divider-color, rgba(127,127,127,.35)); overflow: hidden; }
        .fill { height: 100%; width: 0; background: var(--primary-color); }
        .time { font-size: 12px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; margin-top: -6px; }
        .ctl { display: flex; align-items: center; justify-content: center; gap: 4px; margin-top: 8px; }
        .ib { display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px;
              border-radius: 50%; color: var(--primary-text-color); flex: none; }
        .ib:hover:not(:disabled) { background: rgba(127,127,127,.18); }
        .ib.sm { width: 36px; height: 36px; color: var(--secondary-text-color); }
        .ib.del:hover:not(:disabled) { color: var(--error-color); }
        .pp { width: 68px; height: 68px; background: var(--primary-color); color: var(--text-primary-color, #fff);
              margin: 0 6px; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
        .pp:hover:not(:disabled) { background: var(--primary-color); filter: brightness(1.1); }
        .tr { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 6px 8px; margin-top: 6px;
              font-size: 12px; color: var(--secondary-text-color); }
        .trsel { font: inherit; font-size: 13px; color: var(--primary-text-color); min-height: 36px; max-width: 100%;
                 padding: 4px 8px; border-radius: 8px; border: 1px solid var(--divider-color, rgba(127,127,127,.4));
                 background: var(--card-background-color, transparent); }
        .trsel:disabled { opacity: 0.5; }
        .toggle { color: var(--secondary-text-color); }
        .toggle.on { color: var(--primary-color); background: rgba(127,127,127,.15); }
        .sec { display: flex; align-items: baseline; justify-content: space-between; margin: 18px 0 6px; }
        .sec .t { font-size: 13px; font-weight: 500; text-transform: uppercase; letter-spacing: .06em;
                  color: var(--secondary-text-color); }
        .list { display: flex; flex-direction: column; }
        .row { display: flex; align-items: center; gap: 2px; padding: 4px 4px 4px 0; border-radius: 10px;
               border-bottom: 1px solid var(--divider-color, rgba(127,127,127,.2)); min-width: 0; }
        .row:last-child { border-bottom: none; }
        .row.cur { background: color-mix(in srgb, var(--primary-color) 14%, transparent); border-bottom-color: transparent; }
        .row.cur .nm { color: var(--primary-color); font-weight: 500; }
        .play { color: var(--primary-color); }
        .info { flex: 1; min-width: 0; padding: 0 6px; }
        .nm { font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .meta { display: flex; gap: 10px; font-size: 12px; color: var(--secondary-text-color); margin-top: 1px; }
        .note { display: inline-flex; align-items: center; gap: 2px; color: var(--primary-color); }
        .q { font-size: 13px; color: var(--error-color); margin-right: 4px; }
        .txt { font-size: 13px; font-weight: 500; padding: 10px 10px; min-height: 40px; border-radius: 8px; color: var(--primary-text-color); }
        .txt:hover { background: rgba(127,127,127,.18); }
        .txt.danger { background: var(--error-color); color: #fff; }
        .empty { padding: 18px 8px; text-align: center; font-size: 14px; color: var(--secondary-text-color); line-height: 1.5; }
        .empty b { color: var(--primary-text-color); font-size: 15px; }
        .setup { padding: 18px 10px; text-align: center; font-size: 14px; line-height: 1.5; color: var(--secondary-text-color);
                 background: var(--secondary-background-color, rgba(127,127,127,.12)); border-radius: 14px; }
        .noadmin { padding: 12px; text-align: center; font-size: 14px; color: var(--secondary-text-color); }
        .drop { margin-top: 10px; border: 2px dashed var(--divider-color, rgba(127,127,127,.5)); border-radius: 14px;
                padding: 16px 12px; text-align: center; color: var(--secondary-text-color); transition: all .12s; }
        .drop.over { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent);
                     color: var(--primary-text-color); }
        .drop .big { font-size: 15px; color: var(--primary-text-color); margin: 2px 0 2px; }
        .drop .small { font-size: 12px; }
        .add { display: inline-flex; align-items: center; gap: 6px; margin-top: 10px; padding: 10px 18px; min-height: 40px; border-radius: 22px;
               background: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 500; font-size: 14px; }
        .add:hover { filter: brightness(1.1); }
        .uploads { margin-top: 8px; }
        .up { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; font-size: 12px; padding: 4px 0; align-items: center; }
        .up-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .up-st { color: var(--secondary-text-color); display: inline-flex; align-items: center; gap: 4px; }
        .up.error .up-st { color: var(--error-color); }
        .up.done .up-st { color: var(--success-color, #43a047); }
        .up-x { font-size: 20px; line-height: 1; width: 40px; height: 40px; margin: -10px -8px -10px 0; border-radius: 50%; }
        .up-x:hover { background: rgba(127,127,127,.18); }
        .up-bar { grid-column: 1 / 3; height: 3px; border-radius: 2px; background: var(--divider-color, rgba(127,127,127,.3)); overflow: hidden; }
        .up-bar div { height: 100%; background: var(--primary-color); transition: width .15s; }
        .up.error .up-bar { display: none; }
        .err { color: var(--error-color); font-size: 12px; margin-top: 8px; }
        @media (max-width: 380px) {
          ha-card { padding: 10px 10px 14px; }
          .ctl { gap: 0; }
          .ib { width: 40px; height: 40px; }
          .pp { width: 62px; height: 62px; }
          .ib.sm { width: 40px; height: 40px; }
        }
        @media (pointer: coarse) { .ib.sm { width: 40px; height: 40px; } }
      </style>
      <ha-card>
        <div class="head"><span class="title"></span><span class="count"></span></div>
        <div class="setup" hidden>No Light Show Player yet.<br><b>Set up the Light Show Player: Settings → Devices &amp; services → Add integration → Light Show Studio → Add the Light Show Player.</b></div>
        <div class="playpart">
          <div class="np">
            <div class="np-title">Nothing playing</div>
            <div class="np-sub"></div>
            <div class="bar idle" role="slider" tabindex="-1" aria-label="Seek" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0"><div class="track"><div class="fill"></div></div></div>
            <div class="time">0:00 / 0:00</div>
            <div class="ctl">
              <button class="ib toggle shuf" aria-label="Shuffle" title="Shuffle">${icon("shuffle")}</button>
              <button class="ib prev" aria-label="Previous" title="Previous">${icon("prev", 30)}</button>
              <button class="ib pp" aria-label="Play">${icon("play", 40)}</button>
              <button class="ib stop" aria-label="Stop" title="Stop">${icon("stop", 28)}</button>
              <button class="ib next" aria-label="Next" title="Next">${icon("next", 30)}</button>
              <button class="ib toggle rep" aria-label="Repeat: off" title="Repeat: off">${icon("repeat")}</button>
            </div>
            <div class="tr">
              <label for="trsel">Between shows</label>
              <select id="trsel" class="trsel" aria-label="Between shows">
                <option value="none">None</option>
                <option value="fade">Fade</option>
                <option value="crossfade">Crossfade</option>
                <option value="effect">Effect</option>
              </select>
            </div>
          </div>
          <div class="sec"><span class="t">Shows</span></div>
          <div class="list"></div>
        </div>
        <div class="addpart drop">
          ${icon("upload", 28)}
          <div class="big">${touch ? "Tap to add shows (.fseq + song)" : "Drop .fseq and song files here"}</div>
          <div class="small">Same name = one show, e.g. Thriller.fseq + Thriller.m4a</div>
          <button class="add" aria-label="Add files">${icon("upload", 18)} Add files</button>
          <input type="file" multiple accept=".fseq,audio/*" hidden>
        </div>
        <div class="noadmin" hidden>Only admins can add shows.</div>
        <div class="uploads" aria-live="polite"></div>
        <div class="err" role="alert" hidden></div>
      </ha-card>`;
    const r = this.shadowRoot;
    r.querySelector(".title").textContent =
      this._config.title || (this._upload_only() ? "Add shows" : "Light shows");
    r.querySelector(".pp").onclick = () => this._playPause();
    r.querySelector(".stop").onclick = () => this._service("media_stop");
    r.querySelector(".prev").onclick = () => this._service("media_previous_track");
    r.querySelector(".next").onclick = () => this._service("media_next_track");
    r.querySelector(".shuf").onclick = () => this._toggleShuffle();
    r.querySelector(".rep").onclick = () => this._cycleRepeat();
    r.querySelector(".trsel").onchange = (e) => this._setTransition(e.target.value);

    const bar = r.querySelector(".bar");
    const barFill = () => r.querySelector(".fill");
    const seekAt = (e) => {
      const b = bar.getBoundingClientRect();
      return Math.min(1, Math.max(0, (e.clientX - b.left) / b.width));
    };
    bar.addEventListener("pointerdown", (e) => {
      if (!this._state?.duration || this._state.state === "idle") return;
      this._seeking = true;
      bar.setPointerCapture(e.pointerId);
      barFill().style.width = `${seekAt(e) * 100}%`;
    });
    bar.addEventListener("pointermove", (e) => {
      if (this._seeking) barFill().style.width = `${seekAt(e) * 100}%`;
    });
    bar.addEventListener("pointerup", (e) => {
      if (!this._seeking) return;
      this._seeking = false;
      this._seek(seekAt(e));
    });
    bar.addEventListener("pointercancel", () => (this._seeking = false));
    bar.addEventListener("keydown", (e) => {
      const d = this._state?.duration;
      if (!d || this._state.state === "idle") return;
      const step = { ArrowLeft: -5, ArrowDown: -5, ArrowRight: 5, ArrowUp: 5, PageDown: -30, PageUp: 30 }[e.key];
      let pos = this._position();
      if (step !== undefined) pos += step;
      else if (e.key === "Home") pos = 0;
      else if (e.key === "End") pos = d - 1;
      else return;
      e.preventDefault();
      this._seek(Math.min(Math.max(pos, 0), d) / d);
    });

    const drop = r.querySelector(".drop");
    const input = r.querySelector("input");
    r.querySelector(".add").onclick = () => input.click();
    drop.addEventListener("click", (e) => touch && e.target === drop && input.click());
    input.onchange = () => {
      this._addFiles(input.files);
      input.value = "";
    };
    ["dragenter", "dragover"].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.add("over");
      })
    );
    ["dragleave", "drop"].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.remove("over");
      })
    );
    drop.addEventListener("drop", (e) => this._addFiles(e.dataTransfer?.files));

    this._applyVisibility();
    this._renderState();
    this._renderUploads();
    this._startTimer();
  }
}

customElements.define("light-show-studio-player-card", LightShowStudioPlayerCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "light-show-studio-player-card",
  name: "Light Show Studio player",
  description: "EZPlayer-style light show player: add xLights shows, press play.",
  preview: true,
});
