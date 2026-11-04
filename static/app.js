// State
let currentPlaylist = {
  is_playlist: false,
  playlist_title: "",
  source_type: "YouTube",
  items: [], // Each item has: id, index, url, raw_title, title, artist, album, release_date, duration, thumbnail, format_choice, selected
};

let activePollTimer = null;
let enrichAbortController = null;

// DOM Elements
const urlInput = document.getElementById("urlInput");
const pasteBtn = document.getElementById("pasteBtn");
const analyzeBtn = document.getElementById("analyzeBtn");
const preferPlaylistCheck = document.getElementById("preferPlaylistCheck");
const enrichProgressText = document.getElementById("enrichProgressText");
const errorBanner = document.getElementById("errorBanner");

const outputDirInput = document.getElementById("outputDirInput");
const browseFolderBtn = document.getElementById("browseFolderBtn");
const openFolderBtn = document.getElementById("openFolderBtn");
const namingFormatSelect = document.getElementById("namingFormatSelect");
const videoQualitySelect = document.getElementById("videoQualitySelect");
const subfolderCheck = document.getElementById("subfolderCheck");
const embedCoverCheck = document.getElementById("embedCoverCheck");
const ffmpegStatusBadge = document.getElementById("ffmpegStatusBadge");

const loadingSection = document.getElementById("loadingSection");
const playlistSection = document.getElementById("playlistSection");
const sourceTypeBadge = document.getElementById("sourceTypeBadge");
const itemCountBadge = document.getElementById("itemCountBadge");
const playlistTitleInput = document.getElementById("playlistTitleInput");
const selectAllCheck = document.getElementById("selectAllCheck");
const searchFilterInput = document.getElementById("searchFilterInput");
const swapTitleArtistBtn = document.getElementById("swapTitleArtistBtn");
const selectedCountSpan = document.getElementById("selectedCountSpan");
const downloadSelectedBtn = document.getElementById("downloadSelectedBtn");
const itemsContainer = document.getElementById("itemsContainer");

const jobProgressBanner = document.getElementById("jobProgressBanner");
const jobStatusTitle = document.getElementById("jobStatusTitle");
const jobStatusCounter = document.getElementById("jobStatusCounter");
const jobOverallProgressBar = document.getElementById("jobOverallProgressBar");
const downloadBrowserZipBtn = document.getElementById("downloadBrowserZipBtn");

// Helpers
function formatDuration(seconds) {
  if (!seconds || isNaN(seconds)) return "";
  const s = Math.floor(Number(seconds));
  const hrs = Math.floor(s / 3600);
  const mins = Math.floor((s % 3600) / 60);
  const secs = s % 60;
  if (hrs > 0) {
    return `${hrs}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${mins}:${String(secs).padStart(2, "0")}`;
}

function showError(msg) {
  if (!msg) {
    errorBanner.classList.add("hidden");
    errorBanner.textContent = "";
    return;
  }
  errorBanner.textContent = msg;
  errorBanner.classList.remove("hidden");
}

function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Initialize Config
async function initConfig() {
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    if (data.default_output_dir && !outputDirInput.value) {
      outputDirInput.value = data.default_output_dir;
    }
    if (data.ffmpeg_ready) {
      ffmpegStatusBadge.textContent = "✓ FFmpeg připraven";
      ffmpegStatusBadge.classList.add("ok");
    } else {
      ffmpegStatusBadge.textContent = "⚠️ FFmpeg chybí";
    }
  } catch (err) {
    console.error("Chyba načtení konfigurace:", err);
  }
}

// Clipboard Paste
pasteBtn.addEventListener("click", async () => {
  try {
    const text = await navigator.clipboard.readText();
    if (text) {
      urlInput.value = text.trim();
      urlInput.focus();
    }
  } catch (err) {
    urlInput.focus();
  }
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    analyzeUrl();
  }
});

analyzeBtn.addEventListener("click", analyzeUrl);

// Folder Browse & Open
browseFolderBtn.addEventListener("click", async () => {
  browseFolderBtn.disabled = true;
  const origText = browseFolderBtn.textContent;
  browseFolderBtn.textContent = "⏳ Otevírám dialog…";
  try {
    const res = await fetch("/api/browse-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_dir: outputDirInput.value }),
    });
    const data = await res.json();
    if (data.selected && data.path) {
      outputDirInput.value = data.path;
    }
  } catch (err) {
    showError("Nepodařilo se otevřít dialog výběru složky.");
  } finally {
    browseFolderBtn.disabled = false;
    browseFolderBtn.textContent = origText;
  }
});

openFolderBtn.addEventListener("click", async () => {
  try {
    await fetch("/api/open-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: outputDirInput.value }),
    });
  } catch (err) {
    showError("Složku se nepodařilo otevřít v Průzkumníku.");
  }
});

// Analyze URL
async function analyzeUrl() {
  const url = urlInput.value.trim();
  if (!url) {
    showError("Vložte prosím odkaz z www.youtube.com nebo music.youtube.com.");
    return;
  }

  if (enrichAbortController) {
    enrichAbortController.abort();
  }

  showError("");
  loadingSection.classList.remove("hidden");
  playlistSection.classList.add("hidden");
  enrichProgressText.classList.add("hidden");
  analyzeBtn.disabled = true;

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        prefer_playlist: preferPlaylistCheck.checked,
      }),
    });

    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Chyba při načítání odkazu.");
    }

    currentPlaylist = {
      is_playlist: data.is_playlist,
      playlist_title: data.playlist_title || "",
      source_type: data.source_type || "YouTube",
      items: (data.items || []).map((item) => ({
        ...item,
        format_choice: item.format_choice || "mp3",
        selected: true,
      })),
    };

    renderPlaylistSection();
    loadingSection.classList.add("hidden");
    playlistSection.classList.remove("hidden");

    // Start background metadata enrichment for items missing exact release_date
    runBackgroundEnrichment();
  } catch (err) {
    loadingSection.classList.add("hidden");
    showError(err.message || "Nepodařilo se načíst zadaný odkaz.");
  } finally {
    analyzeBtn.disabled = false;
  }
}

// Background enrichment for playlists (to populate release_date, exact track/artist/album)
async function runBackgroundEnrichment() {
  const itemsToEnrich = currentPlaylist.items.filter((it) => !it.enriched || !it.release_date);
  if (itemsToEnrich.length === 0) return;

  enrichAbortController = new AbortController();
  const signal = enrichAbortController.signal;

  const total = itemsToEnrich.length;
  let completed = 0;
  enrichProgressText.textContent = `⏳ Dotahuji kompletní metadata a data zveřejnění (0/${total})…`;
  enrichProgressText.classList.remove("hidden");

  const batchSize = 6;
  for (let i = 0; i < itemsToEnrich.length; i += batchSize) {
    if (signal.aborted) return;
    const batch = itemsToEnrich.slice(i, i + batchSize);
    try {
      const res = await fetch("/api/enrich", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          playlist_title: playlistTitleInput.value || currentPlaylist.playlist_title,
          items: batch.map((b) => ({ id: b.id, url: b.url, index: b.index })),
        }),
        signal,
      });
      if (!res.ok) continue;
      const data = await res.json();
      const enrichedMap = data.enriched_items || {};

      for (const vidId of Object.keys(enrichedMap)) {
        const info = enrichedMap[vidId];
        const target = currentPlaylist.items.find((x) => x.id === vidId);
        if (target && info) {
          if (!target.userEditedTitle && info.title) target.title = info.title;
          if (!target.userEditedArtist && info.artist) target.artist = info.artist;
          if (!target.userEditedAlbum && info.album) target.album = info.album;
          if (!target.userEditedDate && info.release_date) target.release_date = info.release_date;
          if (info.description) target.description = info.description;
          if (info.genre) target.genre = info.genre;
          target.enriched = true;
          updateTrackRowInputs(target);
        }
      }

      completed = Math.min(total, i + batch.length);
      enrichProgressText.textContent = `⏳ Dotahuji kompletní metadata a data zveřejnění (${completed}/${total})…`;
    } catch (e) {
      if (signal.aborted) return;
    }
  }

  enrichProgressText.textContent = `✓ Všechna metadata skladeb načtena (${total}/${total})`;
  setTimeout(() => {
    enrichProgressText.classList.add("hidden");
  }, 4000);
}

function updateTrackRowInputs(item) {
  const row = document.querySelector(`.track-row[data-key="${item.index}_${item.id}"]`);
  if (!row) return;
  const titleIn = row.querySelector('input[data-field="title"]');
  const artistIn = row.querySelector('input[data-field="artist"]');
  const albumIn = row.querySelector('input[data-field="album"]');
  const dateIn = row.querySelector('input[data-field="release_date"]');

  if (titleIn && document.activeElement !== titleIn) titleIn.value = item.title || "";
  if (artistIn && document.activeElement !== artistIn) artistIn.value = item.artist || "";
  if (albumIn && document.activeElement !== albumIn) albumIn.value = item.album || "";
  if (dateIn && document.activeElement !== dateIn) dateIn.value = item.release_date || "";
}

// Render Playlist UI
function renderPlaylistSection() {
  sourceTypeBadge.textContent = currentPlaylist.source_type;
  const count = currentPlaylist.items.length;
  itemCountBadge.textContent = `${count} ${count === 1 ? "položka" : count >= 2 && count <= 4 ? "položky" : "položek"}`;
  playlistTitleInput.value = currentPlaylist.playlist_title || "";

  renderTrackRows();
  updateSelectionSummary();
}

function renderTrackRows() {
  itemsContainer.innerHTML = "";
  const filterText = (searchFilterInput.value || "").toLowerCase().trim();

  currentPlaylist.items.forEach((item) => {
    const matchText = `${item.title} ${item.artist} ${item.album} ${item.raw_title}`.toLowerCase();
    const isHiddenByFilter = filterText && !matchText.includes(filterText);

    const row = document.createElement("div");
    const itemKey = `${item.index}_${item.id}`;
    row.className = `track-row ${item.selected ? "" : "unselected"} ${isHiddenByFilter ? "hidden" : ""}`;
    row.dataset.key = itemKey;

    const durationStr = formatDuration(item.duration);

    row.innerHTML = `
      <div class="track-check-col">
        <input type="checkbox" class="track-select-check" ${item.selected ? "checked" : ""} />
        <span class="track-index">#${item.index}</span>
      </div>

      <div class="track-thumb-wrapper">
        <img class="track-thumb" src="${escapeHtml(item.thumbnail)}" alt="Náhled" loading="lazy" />
        ${durationStr ? `<span class="track-duration">${durationStr}</span>` : ""}
      </div>

      <div class="track-meta-col">
        <div class="meta-inputs-grid">
          <div class="meta-field">
            <span>Název skladby / videa</span>
            <input type="text" data-field="title" value="${escapeHtml(item.title)}" placeholder="Název skladby" />
          </div>
          <div class="meta-field">
            <span>Interpret / Autor</span>
            <input type="text" data-field="artist" value="${escapeHtml(item.artist)}" placeholder="Interpret" />
          </div>
          <div class="meta-field">
            <span>Album / Playlist</span>
            <input type="text" data-field="album" value="${escapeHtml(item.album)}" placeholder="Album" />
          </div>
          <div class="meta-field">
            <span>Datum zveřejnění</span>
            <input type="text" data-field="release_date" value="${escapeHtml(item.release_date)}" placeholder="RRRR-MM-DD" />
          </div>
        </div>

        <div class="track-status-row hidden" data-role="status-row">
          <div class="track-progress-mini">
            <div class="track-progress-mini-bar" data-role="progress-bar"></div>
          </div>
          <span class="status-pill" data-role="status-text">Ve frontě…</span>
        </div>
      </div>

      <div class="track-actions-col">
        <div class="item-format-selector" title="Vyberte formát stažení pro tuto položku">
          <button type="button" class="fmt-btn ${item.format_choice === "mp3" ? "active" : ""}" data-fmt="mp3">
            🎵 MP3
          </button>
          <button type="button" class="fmt-btn ${item.format_choice === "mp4" ? "active" : ""}" data-fmt="mp4">
            🎬 Video
          </button>
          <button type="button" class="fmt-btn ${item.format_choice === "both" ? "active" : ""}" data-fmt="both">
            Obojí
          </button>
        </div>

        <button type="button" class="btn btn-secondary btn-sm single-dl-btn" title="Stáhnout pouze tuto položku">
          ⬇ Stáhnout
        </button>
      </div>
    `;

    // Event: checkbox toggle
    const checkEl = row.querySelector(".track-select-check");
    checkEl.addEventListener("change", () => {
      item.selected = checkEl.checked;
      row.classList.toggle("unselected", !item.selected);
      updateSelectionSummary();
    });

    // Event: metadata input edits
    row.querySelectorAll(".meta-field input").forEach((inp) => {
      inp.addEventListener("input", () => {
        const field = inp.dataset.field;
        item[field] = inp.value;
        if (field === "title") item.userEditedTitle = true;
        if (field === "artist") item.userEditedArtist = true;
        if (field === "album") item.userEditedAlbum = true;
        if (field === "release_date") item.userEditedDate = true;
      });
    });

    // Event: per-item format buttons
    const fmtBtns = row.querySelectorAll(".fmt-btn");
    fmtBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        const fmt = btn.dataset.fmt;
        item.format_choice = fmt;
        fmtBtns.forEach((b) => b.classList.toggle("active", b.dataset.fmt === fmt));
        updateBulkFormatButtonsHighlight();
      });
    });

    // Event: single item download
    const singleDlBtn = row.querySelector(".single-dl-btn");
    singleDlBtn.addEventListener("click", () => {
      startDownload([item]);
    });

    itemsContainer.appendChild(row);
  });
}

// Update playlist title -> sync album field on unedited items
playlistTitleInput.addEventListener("input", () => {
  const newTitle = playlistTitleInput.value;
  currentPlaylist.playlist_title = newTitle;
  currentPlaylist.items.forEach((it) => {
    if (!it.userEditedAlbum) {
      it.album = newTitle;
      updateTrackRowInputs(it);
    }
  });
});

// Swap Title <-> Artist button
swapTitleArtistBtn.addEventListener("click", () => {
  currentPlaylist.items.forEach((it) => {
    const prevTitle = it.title;
    it.title = it.artist;
    it.artist = prevTitle;
    it.userEditedTitle = true;
    it.userEditedArtist = true;
    updateTrackRowInputs(it);
  });
});

// Search filter
searchFilterInput.addEventListener("input", () => {
  const filterText = (searchFilterInput.value || "").toLowerCase().trim();
  currentPlaylist.items.forEach((item) => {
    const row = document.querySelector(`.track-row[data-key="${item.index}_${item.id}"]`);
    if (!row) return;
    const matchText = `${item.title} ${item.artist} ${item.album} ${item.raw_title}`.toLowerCase();
    const hide = filterText && !matchText.includes(filterText);
    row.classList.toggle("hidden", Boolean(hide));
  });
});

// Select All toggle
selectAllCheck.addEventListener("change", () => {
  const checked = selectAllCheck.checked;
  currentPlaylist.items.forEach((it) => {
    it.selected = checked;
  });
  document.querySelectorAll(".track-row").forEach((row) => {
    const cb = row.querySelector(".track-select-check");
    if (cb) cb.checked = checked;
    row.classList.toggle("unselected", !checked);
  });
  updateSelectionSummary();
});

function updateSelectionSummary() {
  const selectedCount = currentPlaylist.items.filter((i) => i.selected).length;
  selectedCountSpan.textContent = String(selectedCount);
  downloadSelectedBtn.disabled = selectedCount === 0;
  selectAllCheck.checked =
    currentPlaylist.items.length > 0 && selectedCount === currentPlaylist.items.length;
}

// Bulk format buttons
document.querySelectorAll(".btn-format-bulk").forEach((btn) => {
  btn.addEventListener("click", () => {
    const fmt = btn.dataset.bulkFormat;
    currentPlaylist.items.forEach((it) => {
      it.format_choice = fmt;
    });
    document.querySelectorAll(".track-row").forEach((row) => {
      row.querySelectorAll(".fmt-btn").forEach((b) => {
        b.classList.toggle("active", b.dataset.fmt === fmt);
      });
    });
    updateBulkFormatButtonsHighlight();
  });
});

function updateBulkFormatButtonsHighlight() {
  const allMp3 = currentPlaylist.items.every((i) => i.format_choice === "mp3");
  const allMp4 = currentPlaylist.items.every((i) => i.format_choice === "mp4");
  const allBoth = currentPlaylist.items.every((i) => i.format_choice === "both");

  document.querySelectorAll(".btn-format-bulk").forEach((b) => {
    b.classList.remove("active-mp3", "active-mp4", "active-both");
    if (b.dataset.bulkFormat === "mp3" && allMp3) b.classList.add("active-mp3");
    if (b.dataset.bulkFormat === "mp4" && allMp4) b.classList.add("active-mp4");
    if (b.dataset.bulkFormat === "both" && allBoth) b.classList.add("active-both");
  });
}

// Download Selected
downloadSelectedBtn.addEventListener("click", () => {
  const selectedItems = currentPlaylist.items.filter((i) => i.selected);
  if (selectedItems.length === 0) return;
  startDownload(selectedItems);
});

async function startDownload(itemsToDownload) {
  showError("");
  const payload = {
    playlist_title: playlistTitleInput.value.trim(),
    is_playlist: currentPlaylist.is_playlist,
    output_dir: outputDirInput.value.trim(),
    create_playlist_subfolder: subfolderCheck.checked,
    naming_format: namingFormatSelect.value,
    embed_cover: embedCoverCheck.checked,
    video_quality: videoQualitySelect.value,
    items: itemsToDownload.map((it) => ({
      id: it.id,
      index: it.index,
      url: it.url,
      raw_title: it.raw_title || "",
      title: it.title || it.raw_title || "Skladba",
      artist: it.artist || "Neznámý interpret",
      album: it.album || playlistTitleInput.value.trim() || "",
      release_date: it.release_date || "",
      thumbnail: it.thumbnail || "",
      description: it.description || "",
      genre: it.genre || "",
      format_choice: it.format_choice || "mp3",
    })),
  };

  try {
    const res = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || "Chyba při spuštění stahování.");
    }

    pollJobStatus(data.job_id);
  } catch (err) {
    showError(err.message || "Nepodařilo se spustit stahování.");
  }
}

function pollJobStatus(jobId) {
  if (activePollTimer) {
    clearInterval(activePollTimer);
  }

  jobProgressBanner.classList.remove("hidden");
  if (downloadBrowserZipBtn) {
    downloadBrowserZipBtn.classList.add("hidden");
  }

  const fetchStatus = async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) return;
      const job = await res.json();

      if (job.error) {
        showError(job.error);
        clearInterval(activePollTimer);
        return;
      }

      const itemsMap = job.items || {};
      const keys = Object.keys(itemsMap);
      let sumProgress = 0;

      keys.forEach((key) => {
        const st = itemsMap[key];
        sumProgress += st.progress || 0;

        const row = document.querySelector(`.track-row[data-key="${key}"]`);
        if (row) {
          const statusRow = row.querySelector('[data-role="status-row"]');
          const progBar = row.querySelector('[data-role="progress-bar"]');
          const statusText = row.querySelector('[data-role="status-text"]');

          statusRow.classList.remove("hidden");
          progBar.style.width = `${st.progress || 0}%`;
          statusText.className = `status-pill ${st.status}`;
          statusText.textContent = st.speed ? `${st.status_text} (${st.speed})` : st.status_text;

          // If release_date was discovered during download, update input field
          if (st.release_date) {
            const dateInp = row.querySelector('input[data-field="release_date"]');
            if (dateInp && !dateInp.value) {
              dateInp.value = st.release_date;
            }
          }
        }
      });

      const overallPct = keys.length > 0 ? sumProgress / keys.length : 0;
      jobOverallProgressBar.style.width = `${overallPct.toFixed(1)}%`;
      jobStatusCounter.textContent = `${job.completed_count} / ${job.total_count} hotovo${
        job.failed_count > 0 ? ` (${job.failed_count} chyb)` : ""
      }`;

      if (job.status === "completed") {
        jobStatusTitle.textContent = `✓ Stahování dokončeno! Uloženo do: ${job.target_dir}`;
        if (downloadBrowserZipBtn && job.completed_count > 0) {
          downloadBrowserZipBtn.href = `/api/jobs/${jobId}/download-zip`;
          downloadBrowserZipBtn.classList.remove("hidden");
        }
        clearInterval(activePollTimer);
      } else {
        jobStatusTitle.textContent = `Stahování a zápis metadat probíhá… (${overallPct.toFixed(0)} %)`;
      }
    } catch (e) {
      console.error("Chyba při kontrole stavu stahování:", e);
    }
  };

  fetchStatus();
  activePollTimer = setInterval(fetchStatus, 800);
}

// Init on load
initConfig();
