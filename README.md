# bandcamp-download

CLI for downloading albums you have already purchased on Bandcamp.

It runs on macOS and Linux (including a headless server). Archives are saved as ZIP files — **FLAC by default** — and left unextracted so other tools can organize your library.

This only downloads items in *your* collection. It does not scrape public preview streams.

## Install

Python 3.11+ is required.

```sh
git clone https://github.com/fallenAfter/bandcamp-download.git
cd bandcamp-download
curl -LsSf https://astral.sh/uv/install.sh | sh   # if you don't have uv
uv sync
```

The command is `bcdl`:

```sh
uv run bcdl --help
```

Or install into an existing environment:

```sh
pip install .
bcdl --help
```

## Login

Bandcamp has no API tokens for fans. Authentication is the `identity` cookie from a browser where you are already logged in. **This tool never asks for your password** — Bandcamp's login form is behind reCAPTCHA.

### Copy the cookie (macOS or any desktop)

1. Log in at [https://bandcamp.com](https://bandcamp.com).
2. Open DevTools (`Cmd+Option+I` on Mac, `F12` on Linux).
3. Go to **Application** (Chrome/Edge) or **Storage** (Firefox) → **Cookies** → `https://bandcamp.com`.
4. Copy the **Value** of the `identity` cookie.

Then:

```sh
uv run bcdl login --identity 'paste-the-value-here'
```

You will be prompted if you omit `--identity`. A successful login prints your Bandcamp username.

The cookie is stored at `~/.config/bcdl/cookies.json` with mode `0600`. Treat it as a live login credential: do not commit it or paste it into chat.

`BANDCAMP_IDENTITY` is used by every command, so a headless server can skip the saved file:

```sh
BANDCAMP_IDENTITY='paste-the-value-here' uv run bcdl list
```

### Linux server / headless

Copy the cookie on a machine with a browser, then either:

```sh
BANDCAMP_IDENTITY='paste-the-value-here' uv run bcdl login
```

or copy the config directory (preserves the session):

```sh
rsync -a ~/.config/bcdl/ user@server:.config/bcdl/
```

Netscape `cookies.txt` exports also work:

```sh
uv run bcdl login --cookies-txt cookies.txt
```

Sessions last a long time but not forever. When commands say you are not logged in, run `bcdl login` with a fresh cookie.

Override the config location with `BCDL_HOME` if you do not want `~/.config/bcdl`:

```sh
export BCDL_HOME=/var/lib/bcdl
```

## List purchases

```sh
uv run bcdl list
uv run bcdl list --search "slowdive"
uv run bcdl list --artist "Slowdive"
uv run bcdl list --json
```

Each row shows a **KEY** (use with `--id`) and the album URL. Hidden collection items are omitted unless you pass `--include-hidden`.

`list` also writes a local cache. `download` always refreshes your collection before matching URLs so new purchases and download links stay current.

## Download selected albums

v1 downloads only the albums you name. ZIPs land in the current directory (or `-o`).

```sh
# One album by URL
uv run bcdl download https://artist.bandcamp.com/album/album-title

# By collection key from `bcdl list`
uv run bcdl download --id p123456

# Several at once
uv run bcdl download \
  https://artist.bandcamp.com/album/one \
  https://artist.bandcamp.com/album/two \
  --id p111 --id p222

# From a text file (URLs or ids, `#` comments allowed)
uv run bcdl download --file albums.txt -o ~/Music/bandcamp-zips

# Every owned album by one artist (preview first)
uv run bcdl list --artist "Slowdive"
uv run bcdl download --artist "Slowdive" --dry-run
uv run bcdl download --artist "Slowdive" -o ~/Music/bandcamp-zips
```

`--artist` needs to match exactly one artist in your collection; if a partial name matches several, the matches are listed so you can pick one. Merch purchases with no digital download are skipped, as are unreleased preorders (whose ZIP would only hold the tracks out so far). Add `--include-preorders` if you want the partial ones anyway.

Example `albums.txt`:

```text
# purchased 2024
https://slowdive.bandcamp.com/album/souvlaki
p123456
```

Re-runs skip albums already in the manifest or already on disk. Use `--force` to fetch them again.

```sh
uv run bcdl download --file albums.txt --force
```

Interrupted downloads leave a `.part` file and resume on the next run.

## Formats

Preferred format is **FLAC**. If an album does not offer it, the next available of `alac`, then `mp3-320`, is used.

```sh
uv run bcdl download --id p123456 --format flac      # default
uv run bcdl download --id p123456 --format mp3-320
```

Known Bandcamp names: `flac`, `alac`, `wav`, `aiff-lossless`, `mp3-320`, `mp3-v0`, `aac-hi`, `vorbis`.

Files are named `{artist} - {album} [{format}].zip`.

## Rate limits

Downloads are always one album at a time, including `--artist` discographies. The default pause between albums is 3 seconds (`--delay`). Failed transfers retry (`--retries`, `--retry-wait`), and HTTP 429 responses honour `Retry-After`. Use `--dry-run` to see the queue before fetching. Do not set `--delay 0` on a large artist download.

## State files

Under `~/.config/bcdl/` (or `BCDL_HOME`):

| file | purpose |
|---|---|
| `cookies.json` | `identity` cookie (credential, mode 0600) |
| `collection.json` | cached purchase list from `bcdl list` |
| `manifest.json` | completed downloads, so re-runs skip them |

## Development

```sh
uv sync --extra dev
uv run pytest
```

## Notes

Bandcamp has no public fan collection API. This talks to the same undocumented endpoints the website uses; they can change without notice.

Only download music you have purchased.
