![streamrip web interface](https://github.com/AnOddName/streamrip-web-gui/blob/main/demo/home_screen.png?raw=true)

> [!IMPORTANT]
> This repository is a fork of AnOddName's [streamrip-web-gui](https://github.com/AnOddName/streamrip-web-gui).

# Streamrip Web GUI

A web interface for [Streamrip](https://github.com/nathom/streamrip), providing a GUI for downloading music from various streaming services.

Streamrip is lit but CLI-only. Having to SSH into my stupid little server each time I wanted to download a track was too much effort for me.
(Mainly Qobuz for me low key I don't even know if Tidal/Deezer work because I don't have accounts for them)

Intended to be used with Docker/Docker Compose, but it runs locally too.

![Python](https://img.shields.io/badge/python-3.11-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-green.svg)

## Features

- **Multi-Service Support**: Download from Qobuz, Tidal, Deezer, SoundCloud
- **Built-in Search**: Search and download directly from the web interface
- **Download Management**: Track active downloads, view history, and browse downloaded files
- **Duplicate Detection**: Items already in streamrip's database show up as "Already Downloaded" instead of silently re-running
- **Configuration Editor**: Edit streamrip settings directly from the web interface
- **Installable PWA**: Add it to your phone or desktop as an app
- **Mobile Friendly**: The UI adapts to phone-sized screens
- **Docker Ready**: Easy deployment with Docker Compose

## Screenshots

![search](https://github.com/AnOddName/streamrip-web-gui/blob/main/demo/search.png?raw=true)
![download](https://github.com/AnOddName/streamrip-web-gui/blob/main/demo/active_dl.png?raw=true)

## How It Works

The web app is a thin wrapper around the `rip` command line tool. When you download something, it runs `rip` for you and streams the progress to your browser.

Two settings control everything about where files go:

| Setting | What it does |
|---|---|
| `STREAMRIP_CONFIG` | Path to streamrip's `config.toml` (credentials, quality settings, its downloads database) |
| `DOWNLOAD_DIR` | Where downloaded music is saved. This **always overrides** the `downloads.folder` inside `config.toml` |

Everything else (credentials, file naming, conversion) lives in your normal streamrip config.

## Prerequisites

- A working streamrip setup with valid streaming service credentials (see below)
- Docker and Docker Compose (for containerized deployment), **or** Python 3.11+ (for running locally)

## Step 1: Install and Configure Streamrip

You MUST install and configure streamrip first. The web GUI does not work without it.

```bash
pip install streamrip
rip config open
```

Add your credentials following the [Streamrip configuration guide](https://github.com/nathom/streamrip/wiki/Configuration):

- **Qobuz**: email and password (or token)
- **Tidal**: email and password
- **Deezer**: ARL
- **SoundCloud**: works without authentication

Verify it works on its own before continuing:

```bash
rip search qobuz track "test"
```

This creates streamrip's config at `~/.config/streamrip/config.toml` along with its downloads database (used to detect already-downloaded music).

## Step 2a: Run with Docker Compose (recommended)

1. Clone the repository:

```bash
git clone https://github.com/anoddname/streamrip-web-gui.git
cd streamrip-web-gui
```

2. Create your `.env` file from the template:

```bash
cp .env.example .env
```

3. Edit `.env` and set the values for your machine:

| Variable | What to put there | Example |
|---|---|---|
| `CONFIG_DIR` | The directory containing streamrip's `config.toml` | `/home/alice/.config/streamrip` |
| `MUSIC_DIR` | Where downloaded music should land | `/home/alice/Music` |
| `PUID` / `PGID` | The user/group ID that owns those two directories (run `id -u` and `id -g`) | `1000` / `1000` |
| `PORT` | Host port for the web interface | `5002` |
| `MAX_CONCURRENT_DOWNLOADS` | How many downloads run at once | `2` |

4. Build and start:

```bash
docker compose up -d --build
```

5. Open `http://localhost:5002` (or whatever `PORT` you chose).

**Why no paths inside `docker-compose.yml`?** The compose file mounts your two directories at the *same paths inside the container* as on the host. That way the absolute paths streamrip wrote into `config.toml` (like its database location) stay valid everywhere, and Docker, local runs, and the plain `rip` CLI all share one config and one downloads database. You never need to edit paths inside the container.

### Using the pre-built image (not updated with this fork)

If you don't want to build from source, replace `build: ./` with `image: anoddname/streamrip-web-gui:latest` in `docker-compose.yml`. Note the published image may lag behind the latest changes in this repo.

## Step 2b: Run Locally (no Docker)

1. Clone and install dependencies:

```bash
git clone https://github.com/anoddname/streamrip-web-gui.git
cd streamrip-web-gui
pip install flask requests
```

2. Run it:

```bash
python app.py
```

3. Open `http://localhost:5000`.

Locally the app uses sensible defaults — no setup needed if your streamrip config is in the standard place:

| Setting | Default | Override with |
|---|---|---|
| Streamrip config | `~/.config/streamrip/config.toml` | `STREAMRIP_CONFIG=/path/to/config.toml` |
| Download folder | `~/Music` | `DOWNLOAD_DIR=/path/to/music` |

Example with overrides:

```bash
DOWNLOAD_DIR=~/Downloads/Music python app.py
```

If the download folder isn't usable, the app tells you loudly at startup instead of failing silently later.

## Install as an App (PWA)

The web interface can be installed like a native app (icon, standalone window):

- **Desktop Chrome/Edge**: click the install icon in the address bar
- **Android Chrome**: menu → "Add to Home screen"
- **iOS Safari**: share button → "Add to Home Screen"

Browsers only offer installation over `http://localhost` or HTTPS. If you access the app from another device over plain HTTP (e.g. `http://192.168.1.x:5002`), the install option won't appear — put it behind an HTTPS reverse proxy(like [tailscale serve](https://tailscale.com/docs/features/tailscale-serve)) if you want the full app experience remotely.

## Usage

### Downloading from URL

1. Paste a streaming service URL in the input field
2. Select quality (MP3 128/320 or FLAC 16/24-bit)
3. Click DOWNLOAD

### Searching for Music

1. Select a streaming service from the dropdown
2. Choose search type (Albums, Tracks, or Artists)
3. Enter your search query
4. Click DOWNLOAD next to any result

Search downloads use the quality selected in the URL section's dropdown.

### Download Statuses

| Badge | Meaning |
|---|---|
| `DOWNLOADING` | rip is running |
| `COMPLETED` | finished successfully |
| `ALREADY DOWNLOADED` | every track was already in streamrip's database, nothing was re-downloaded |
| `FAILED` | rip exited with an error — expand the item's output, or check the server logs for the full error |

Finished downloads move to the HISTORY tab after a few seconds. History lives in your browser session only — reloading the page clears it (the downloaded files are of course untouched).

## Troubleshooting

1. **"Config file not found" warning in logs**: streamrip isn't configured, or it's somewhere unexpected. Run `rip config open` to create one, or point `STREAMRIP_CONFIG` (in `.env` for Docker) at the right file.

2. **"DOWNLOAD_DIR ... is not usable" error at startup**: the download folder doesn't exist or isn't writable by the app. Fix the path in `.env` (Docker) or set `DOWNLOAD_DIR` (local). In Docker, also check `PUID`/`PGID` match the owner of the folder (`id -u`, `id -g`).

3. **Every download fails immediately**: check the server logs (`docker logs streamrip`) — the app logs rip's full error output on failure. Usually it's a permissions problem (see #2) or invalid credentials.

4. **Searches time out / Tidal or Deezer errors**: check that your credentials in the streamrip config are valid. Test outside the GUI with `rip search <service> track "something"`.

5. **A download says completed but only got the cover image**: that should now show as `ALREADY DOWNLOADED` instead. The music is already on disk from a previous run; streamrip's database remembers it. To force a re-download, remove the entry from streamrip's database (or delete `downloads.db` to forget everything).

6. **No install-app option in the browser**: see the PWA section — you need `localhost` or HTTPS.

7. **No album art when running locally**: CORS issue with some services; downloads still work.

## Disclaimer

This tool is for educational purposes only. Ensure you comply with the terms of service of the streaming platforms you use. Support artists by purchasing their music.

---

Fueled by spite
