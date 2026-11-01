// Govee light show player card: drop xLights .fseq shows (and songs) on it, press play. Ships with govee_light_show.
//
//   type: custom:govee-light-show-player-card
//   entity: media_player.light_show_player   # optional: autodetected (the media_player with a "queue" attribute)
//   title: Light shows                       # optional

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

class GoveeC9PlayerCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("media_player.") && hass.states[id].attributes.queue !== undefined
    );
    return { entity: entity || "", title: "Light shows" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "entity", selector: { entity: { domain: "media_player", integration: "govee_light_show" } } },
        { name: "title", selector: { text: {} } },
      ],
    };
  }

  setConfig(config) {
    this._config = { ...config };
    this._built = false;
    if (this._hass) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._detectEntity();
    this._subscribe();
    if (!this._shows) this._loadShows();
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  connectedCallback() {
    if (this._hass) {
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
    this._subscribing = true;
    try {
      this._unsub = await this._hass.connection.subscribeMessage((m) => this._onEvent(m), {
        type: "govee_light_show/player/subscribe",
      });
      this._error("");
    } catch (err) {
      this._error(err?.message || "Can't reach the light show player");
    }
    this._subscribing = false;
    this._startTimer();
  }

  _unsubscribe() {
    if (this._unsub) this._unsub();
    this._unsub = null;
  }

  _onEvent(m) {
    const prev = this._state;
    this._state = m;
    this._stamp = Date.now();
    const queueChanged = !prev || JSON.stringify(prev.queue) !== JSON.stringify(m.queue);
    if (queueChanged && this._shows) this._loadShows();
    this._renderState();
  }

  async _loadShows() {
    if (this._loading || !this._hass) return;
    this._loading = true;
    try {
      const r = await this._hass.callWS({ type: "govee_light_show/shows/list" });
      this._shows = r.shows || [];
      this._error("");
    } catch (err) {
      this._shows = this._shows || [];
      this._error(err?.message || "Can't load the shows");
    }
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
    if (s.state === "playing") {
      const at = s.position_updated_at ? Date.parse(s.position_updated_at) : NaN;
      p += (Date.now() - (isNaN(at) ? this._stamp : at)) / 1000;
    }
    return Math.min(Math.max(p, 0), s.duration || p);
  }

  _service(service, data = {}) {
    const entity_id = this._entityId();
    if (!entity_id) return this._error("No light show player found");
    return this._hass.callService("media_player", service, { entity_id, ...data });
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
      await this._hass.callWS({ type: "govee_light_show/shows/reorder", order: list });
    } catch (err) {
      this._error(err?.message || "Couldn't reorder");
      this._loadShows();
    }
  }

  async _delete(show) {
    this._confirming = null;
    try {
      await this._hass.callWS({ type: "govee_light_show/shows/delete", show_id: show.id });
      this._shows = (this._shows || []).filter((s) => s.id !== show.id);
      this._renderList();
      this._loadShows();
    } catch (err) {
      this._error(err?.message || "Couldn't delete");
    }
  }

  _seek(frac) {
    const d = this._state?.duration;
    if (!d || this._state.state === "idle") return;
    const pos = Math.round(frac * d * 10) / 10;
    this._state = { ...this._state, position: pos, position_updated_at: new Date().toISOString() };
    this._stamp = Date.now();
    this._service("media_seek", { seek_position: pos });
    this._tick();
  }

  // --- upload ------------------------------------------------------------
  _addFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
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

  _upload(file, item) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("show", file.name.replace(/\.[^.]+$/, ""));
      form.append("file", file, file.name);
      xhr.open("POST", "/api/govee_light_show/shows/upload");
      xhr.setRequestHeader("Authorization", `Bearer ${this._hass.auth.data.access_token}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          item.pct = Math.round((e.loaded / e.total) * 100);
          this._renderUploads();
        }
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else if (xhr.status === 401) reject(new Error("Admin access needed to add shows"));
        else reject(new Error(`Upload failed (${xhr.status})`));
      };
      xhr.onerror = () => reject(new Error("Network error"));
      xhr.send(form);
    });
  }

  // --- render ------------------------------------------------------------
  _error(msg) {
    const el = this.shadowRoot?.querySelector(".err");
    if (!el) return;
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  _startTimer() {
    if (this._timer) return;
    this._timer = setInterval(() => this._tick(), 250);
  }

  _tick() {
    const r = this.shadowRoot;
    if (!r || !this._state) return;
    const pos = this._position();
    const dur = this._state.duration || 0;
    r.querySelector(".fill").style.width = dur ? `${Math.min(100, (pos / dur) * 100)}%` : "0%";
    r.querySelector(".time").textContent = `${fmt(pos)} / ${fmt(dur)}`;
  }

  _renderState() {
    const r = this.shadowRoot;
    if (!r || !this._built) return;
    const s = this._state || { state: "idle", queue: [] };
    const playing = s.state === "playing";
    const show = (this._shows || []).find((x) => x.id === s.show_id);
    const title = r.querySelector(".np-title");
    title.textContent = s.state === "idle" ? "Nothing playing" : show?.name || s.show_id || "Playing";
    title.classList.toggle("dim", s.state === "idle");
    r.querySelector(".np-sub").textContent =
      (s.state === "paused" ? "Paused" : s.state === "playing" ? "Now playing" : "Press play to start the first show") +
      (s.speaker ? ` · ${this._speakerName(s.speaker)}` : " · lights only");
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
    r.querySelector(".bar").classList.toggle("idle", s.state === "idle");
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
      const bar = document.createElement("div");
      bar.className = "up-bar";
      const fill = document.createElement("div");
      fill.style.width = `${u.pct}%`;
      bar.append(fill);
      row.append(name, st, bar);
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
    r.querySelector(".count").textContent = shows.length ? `${shows.length} show${shows.length === 1 ? "" : "s"}` : "";
    list.innerHTML = "";
    if (!this._shows) {
      list.innerHTML = `<div class="empty">Loading shows…</div>`;
      return;
    }
    if (!shows.length) {
      list.innerHTML = `<div class="empty"><b>No shows yet</b><br>Export your sequence from xLights (File → Export / the .fseq next to your .xsq) and drop it here with the song.</div>`;
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
        s.has_audio ? `<span class="note" title="Has a song">${icon("note", 13)} song</span>` : `<span>lights only</span>`
      }`;
      info.append(nm, meta);
      row.append(play, info);
      if (this._confirming === s.id) {
        const q = document.createElement("span");
        q.className = "q";
        q.textContent = "Delete?";
        const yes = document.createElement("button");
        yes.className = "txt danger";
        yes.textContent = "Delete";
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
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 16px 16px; box-sizing: border-box; min-width: 0; max-width: 100%; overflow: hidden; }
        * { box-sizing: border-box; }
        button { font: inherit; color: inherit; cursor: pointer; border: none; background: none; padding: 0; margin: 0;
                 -webkit-tap-highlight-color: transparent; }
        button:disabled { opacity: 0.3; cursor: default; }
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
        .bar { position: relative; height: 28px; margin-top: 10px; cursor: pointer; touch-action: none; }
        .bar.idle { cursor: default; opacity: 0.6; }
        .track { position: absolute; left: 0; right: 0; top: 11px; height: 6px; border-radius: 3px;
                 background: var(--divider-color, rgba(127,127,127,.35)); overflow: hidden; }
        .fill { height: 100%; width: 0; background: var(--primary-color); }
        .time { font-size: 12px; color: var(--secondary-text-color); font-variant-numeric: tabular-nums; margin-top: -2px; }
        .ctl { display: flex; align-items: center; justify-content: center; gap: 4px; margin-top: 8px; }
        .ib { display: inline-flex; align-items: center; justify-content: center; width: 44px; height: 44px;
              border-radius: 50%; color: var(--primary-text-color); flex: none; }
        .ib:hover:not(:disabled) { background: rgba(127,127,127,.18); }
        .ib.sm { width: 36px; height: 36px; color: var(--secondary-text-color); }
        .ib.del:hover:not(:disabled) { color: var(--error-color); }
        .pp { width: 68px; height: 68px; background: var(--primary-color); color: var(--text-primary-color, #fff);
              margin: 0 6px; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
        .pp:hover:not(:disabled) { background: var(--primary-color); filter: brightness(1.1); }
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
        .txt { font-size: 13px; font-weight: 500; padding: 8px 10px; border-radius: 8px; color: var(--primary-text-color); }
        .txt:hover { background: rgba(127,127,127,.18); }
        .txt.danger { background: var(--error-color); color: #fff; }
        .empty { padding: 18px 8px; text-align: center; font-size: 14px; color: var(--secondary-text-color); line-height: 1.5; }
        .empty b { color: var(--primary-text-color); font-size: 15px; }
        .drop { margin-top: 10px; border: 2px dashed var(--divider-color, rgba(127,127,127,.5)); border-radius: 14px;
                padding: 16px 12px; text-align: center; color: var(--secondary-text-color); transition: all .12s; }
        .drop.over { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent);
                     color: var(--primary-text-color); }
        .drop .big { font-size: 15px; color: var(--primary-text-color); margin: 2px 0 2px; }
        .drop .small { font-size: 12px; }
        .add { display: inline-flex; align-items: center; gap: 6px; margin-top: 10px; padding: 10px 18px; border-radius: 22px;
               background: var(--primary-color); color: var(--text-primary-color, #fff); font-weight: 500; font-size: 14px; }
        .add:hover { filter: brightness(1.1); }
        .uploads { margin-top: 8px; }
        .up { display: grid; grid-template-columns: 1fr auto; gap: 2px 8px; font-size: 12px; padding: 4px 0; }
        .up-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .up-st { color: var(--secondary-text-color); }
        .up.error .up-st { color: var(--error-color); }
        .up.done .up-st { color: var(--success-color, #43a047); }
        .up-bar { grid-column: 1 / 3; height: 3px; border-radius: 2px; background: var(--divider-color, rgba(127,127,127,.3)); overflow: hidden; }
        .up-bar div { height: 100%; background: var(--primary-color); transition: width .15s; }
        .up.error .up-bar { display: none; }
        .err { color: var(--error-color); font-size: 12px; margin-top: 8px; }
        @media (max-width: 380px) {
          ha-card { padding: 10px 10px 14px; }
          .ctl { gap: 0; }
          .ib { width: 40px; height: 40px; }
          .pp { width: 62px; height: 62px; }
          .ib.sm { width: 34px; height: 34px; }
        }
      </style>
      <ha-card>
        <div class="head"><span class="title"></span><span class="count"></span></div>
        <div class="np">
          <div class="np-title">Nothing playing</div>
          <div class="np-sub"></div>
          <div class="bar idle" role="slider" aria-label="Seek"><div class="track"><div class="fill"></div></div></div>
          <div class="time">0:00 / 0:00</div>
          <div class="ctl">
            <button class="ib toggle shuf" aria-label="Shuffle" title="Shuffle">${icon("shuffle")}</button>
            <button class="ib prev" aria-label="Previous" title="Previous">${icon("prev", 30)}</button>
            <button class="ib pp" aria-label="Play">${icon("play", 40)}</button>
            <button class="ib stop" aria-label="Stop" title="Stop">${icon("stop", 28)}</button>
            <button class="ib next" aria-label="Next" title="Next">${icon("next", 30)}</button>
            <button class="ib toggle rep" aria-label="Repeat" title="Repeat: off">${icon("repeat")}</button>
          </div>
        </div>
        <div class="sec"><span class="t">Shows</span></div>
        <div class="list"></div>
        <div class="drop">
          ${icon("upload", 28)}
          <div class="big">Drop .fseq and song files here</div>
          <div class="small">Same name = one show, e.g. Thriller.fseq + Thriller.m4a</div>
          <button class="add">${icon("upload", 18)} Add files</button>
          <input type="file" multiple accept=".fseq,audio/*" hidden>
        </div>
        <div class="uploads"></div>
        <div class="err" hidden></div>
      </ha-card>`;
    const r = this.shadowRoot;
    r.querySelector(".title").textContent = this._config.title || "Light shows";
    r.querySelector(".pp").onclick = () => this._playPause();
    r.querySelector(".stop").onclick = () => this._service("media_stop");
    r.querySelector(".prev").onclick = () => this._service("media_previous_track");
    r.querySelector(".next").onclick = () => this._service("media_next_track");
    r.querySelector(".shuf").onclick = () => this._toggleShuffle();
    r.querySelector(".rep").onclick = () => this._cycleRepeat();

    const bar = r.querySelector(".bar");
    const seekAt = (e) => {
      const b = bar.getBoundingClientRect();
      return Math.min(1, Math.max(0, (e.clientX - b.left) / b.width));
    };
    bar.addEventListener("pointerdown", (e) => {
      if (!this._state?.duration || this._state.state === "idle") return;
      this._seeking = true;
      bar.setPointerCapture(e.pointerId);
      r.querySelector(".fill").style.width = `${seekAt(e) * 100}%`;
    });
    bar.addEventListener("pointermove", (e) => {
      if (this._seeking) r.querySelector(".fill").style.width = `${seekAt(e) * 100}%`;
    });
    bar.addEventListener("pointerup", (e) => {
      if (!this._seeking) return;
      this._seeking = false;
      this._seek(seekAt(e));
    });
    bar.addEventListener("pointercancel", () => (this._seeking = false));

    const drop = r.querySelector(".drop");
    const input = r.querySelector("input");
    r.querySelector(".add").onclick = () => input.click();
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

    // the seek bar must not be overwritten while dragging
    const origTick = this._tick.bind(this);
    this._tick = () => {
      if (!this._seeking) origTick();
    };

    this._renderState();
    this._renderUploads();
    this._startTimer();
  }
}

customElements.define("govee-light-show-player-card", GoveeC9PlayerCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "govee-light-show-player-card",
  name: "Govee light show player",
  description: "EZPlayer-style light show player: add xLights shows, press play.",
  preview: true,
});
