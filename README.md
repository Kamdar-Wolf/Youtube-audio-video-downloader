# 🎵 YouTube & YT Music Downloader (MP3 / MP4 + Metadata)

Moderní webová aplikace pro stahování playlistů, alb a jednotlivých skladeb či videí ze stránek **[youtube.com](https://www.youtube.com)** a **[music.youtube.com](https://music.youtube.com)**.

Umožňuje u každé položky v playlistu individuálně (nebo hromadně) zvolit stažení jako **Audio v nejvyšší kvalitě (MP3 320 kbps)** nebo **Video (MP4)** a automaticky do stažených souborů zapíše kompletní metadata včetně přebalu alba / miniatury (**Cover Art**).

---

## ✨ Hlavní funkce

- **Podpora YouTube i YouTube Music**:
  - Načítá celé playlisty, alba i jednotlivé skladby a videa.
  - Pokud vložíte odkaz na video uvnitř playlistu (`watch?v=...&list=...`), automaticky načte celý playlist (lze přepnout v rozhraní).
- **Individuální i hromadný výběr formátu**:
  - U každé skladby si můžete zvlášť vybrat: **🎵 MP3 (Audio 320 kbps)**, **🎬 MP4 (Video až 4K/1080p)** nebo **Obojí**.
  - Hromadná tlačítka umožňují přepnout formát všem položkám jedním kliknutím.
- **Automatická metadata a přebal alba (ID3v2.3 & MP4 tagy)**:
  - Automaticky dotahuje a do souborů přímo zapisuje:
    - **Název skladby** (`TIT2` / `©nam`)
    - **Interpret / Autor** (`TPE1`, `TPE2` / `©ART` – automaticky čistí příponu `- Topic` a správně rozpoznává pořadí `Název - Autor` vs. `Autor - Název`)
    - **Album / Název playlistu** (`TALB` / `©alb`)
    - **Datum zveřejnění a rok vydání** (`TDRC`, `TYER` / `©day`)
    - **Číslo stopy v playlistu** (`TRCK` / `trkn`)
    - **Zdrojový odkaz a popis** (`WOAR`, `COMM` / `©cmt`)
    - **Přebal alba / miniatura (Cover Art)** ve vysokém rozlišení (`APIC` / `covr`)
  - Všechna metadata lze před stažením v tabulce ručně upravit nebo jedním kliknutím prohodit sloupce **Název ↔ Interpret**.
- **Podpora věkově omezených (18+ / NSFW) videí**:
  - Automaticky využívá přihlašovací cookies z prohlížeče (Firefox) nebo souboru `cookies.txt` a `yt-dlp-ejs` (Node.js) pro odemčení věkově omezených videí.
- **Chytrá organizace souborů**:
  - Automatické vytváření podsložky podle názvu playlistu / alba.
  - Volitelný formát pojmenování souborů (`Interpret - Název skladby`, `01. Interpret - Název skladby`, `Název skladby`, `Původní název videa`).
  - Možnost přímého stažení výsledných souborů nebo celého `.ZIP` archivu přes webový prohlížeč.

---

## 🚀 Jak aplikaci spustit

Aplikaci můžete spustit **3 způsoby** – buď přímo v cloudu na GitHubu, jako samostatný `.exe` program, nebo lokálně přes skript.

### Možnost 1: Spuštění přímo na GitHubu v prohlížeči (GitHub Codespaces)
Aplikace obsahuje konfiguraci [`.devcontainer/devcontainer.json`](.devcontainer/devcontainer.json), takže ji lze spustit **přímo na serveru GitHubu bez jakékoliv instalace do počítače**:
1. Na stránce tohoto repozitáře na GitHubu klikněte na zelené tlačítko **`<> Code`**.
2. Přepněte na záložku **Codespaces** a klikněte na **Create codespace on main**.
3. GitHub automaticky připraví prostředí (Python, FFmpeg, Node.js), spustí aplikaci a otevře webové rozhraní.
4. Po stažení skladeb klikněte v aplikaci na tlačítko **„📦 Uložit do zařízení (ZIP / Soubor)“**, které odešle hotové MP3/MP4 přímo do vašeho počítače nebo telefonu.

---

### Možnost 2: Samostatný `.exe` program pro Windows (z GitHub Actions)
Součástí repozitáře je automatická sestavovací linka [`.github/workflows/build-windows.yml`](.github/workflows/build-windows.yml):
1. Na GitHubu otevřete horní záložku **Actions** → **Build Windows Executable (.exe)**.
2. Klikněte na nejnovější sestavení (nebo na tlačítko *Run workflow*).
3. V sekci **Artifacts** si stáhněte **`YouTube-Downloader-Windows-EXE`** (`YouTube-Downloader.exe`) a spusťte jej – nevyžaduje instalaci Pythonu.

---

### Možnost 3: Lokální spuštění na Windows jedním kliknutím (`spustit.bat`)
Pokud máte nainstalovaný [Python 3.10+](https://www.python.org/downloads/) (a volitelně [Node.js](https://nodejs.org/) pro věkově omezená 18+ videa):

1. Stáhněte si tento repozitář (tlačítko **Code → Download ZIP** nebo `git clone`).
2. Dvakrát klikněte na soubor **[`spustit.bat`](spustit.bat)**.
   - Při prvním spuštění skript automaticky nainstaluje potřebné knihovny z [`requirements.txt`](requirements.txt) a sám stáhne binární soubory `FFmpeg`.
   - Automaticky se otevře prohlížeč na adrese `http://127.0.0.1:8765`.

#### Ruční spuštění z příkazové řádky:
```bash
pip install -r requirements.txt
python app.py
```

---

## 🗂️ Struktura projektu

```text
├── app.py                              # FastAPI backend, yt-dlp stahovač a Mutagen ID3/MP4 tagger
├── static/
│   ├── index.html                      # Uživatelské rozhraní (HTML5)
│   ├── styles.css                      # Moderní tmavý vzhled (CSS3)
│   └── app.js                          # Logika webového rozhraní a real-time ukazatel průběhu
├── spustit.bat                         # Spouštěč jedním kliknutím pro Windows (s automatickou instalací)
├── requirements.txt                    # Seznam Python závislostí
├── .devcontainer/devcontainer.json     # Konfigurace pro spuštění přímo v GitHub Codespaces
└── .github/workflows/build-windows.yml # GitHub Actions pro automatické vytvoření Windows .exe
```

---

## 🔞 Poznámka k věkově omezeným (18+) videím

Pokud stahujete videa, která na YouTube vyžadují potvrzení věku (`Sign in to confirm your age`):
1. Nejjednodušší způsob je mít v prohlížeči **Mozilla Firefox** přihlášený účet YouTube – aplikace si při zjištění 18+ videa cookies načte automaticky.
2. Alternativně můžete exportovat cookies do souboru `cookies.txt` (formát Netscape) a vložit jej do hlavní složky vedle `app.py`.
