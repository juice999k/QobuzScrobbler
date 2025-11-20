<img width="500" height="500" alt="QScrobbler" src="https://github.com/user-attachments/assets/1bc92207-965f-41a1-ad0f-42254525c539" />























# QScrobbler

A lightweight Windows scrobbler for Qobuz that sends your listening history to Last.fm.

## Features

- **Automatic scrobbling** - Detects what's playing in Qobuz and scrobbles to Last.fm
- **Smart album detection** - Uses multiple sources (Qobuz → Last.fm → MusicBrainz) to find album metadata
- **Artist name correction** - Automatically fixes artist names with accents/typos using Last.fm database
- **Album blacklist**  - Ignore fake albums, bootlegs, or leaks via configurable blacklist
- **Strict album filtering** - Only accepts official albums/EPs, skips compilations and bootlegs
- **Configurable** - Adjust scrobble timing, check intervals, and more via `.env`

## Requirements

- Windows 10/11
- Python 3.8+
- Qobuz Desktop App
- Last.fm account

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/qscrobbler.git
cd qscrobbler
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the project root (see [Configuration](#configuration))

4. Run the scrobbler:
```bash
python qscrobbler.py
```

## Configuration

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```env
# Required - Last.fm API credentials
# Get yours at: https://www.last.fm/api/account/create
LASTFM_API_KEY=your_api_key
LASTFM_API_SECRET=your_api_secret
LASTFM_USERNAME=your_username
LASTFM_PASSWORD=your_password

# Optional - Scrobbler settings
MIN_SCROBBLE_TIME=30          # Minimum seconds before scrobbling (default: 30)
CHECK_INTERVAL=2              # How often to check Qobuz window (default: 2)
LOG_LEVEL=INFO                # Logging level: DEBUG, INFO, WARNING, ERROR
LOG_FILE=qscrobbler.log       # Log file path
```

## How It Works

1. QScrobbler monitors the Qobuz desktop app window title
2. When a track is detected, it fetches album metadata from:
   - Qobuz public API (primary)
   - Last.fm API (fallback)
   - MusicBrainz API (last resort)
3. Sends "Now Playing" status to Last.fm
4. After the minimum play time, scrobbles the track

## Usage

### Running normally
```bash
python qscrobbler.py
```

### Running at startup (Windows)

1. Create a shortcut to `qscrobbler.py`
2. Press `Win + R`, type `shell:startup`, press Enter
3. Move the shortcut to the opened folder

## Album Blacklist

Create a `blacklist.txt` file to ignore fake albums, bootlegs, or leaks:

```txt
# Lines starting with # are comments
Mollyworld
Narcissist
```

The blacklist is case-insensitive. You can reload it from the tray menu without restarting the app.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Last.fm credentials missing" | Check your `.env` file has all 4 Last.fm values |
| No album detected | This is normal for rare tracks; the scrobble still works |
| Track not scrobbling | Must play for at least `MIN_SCROBBLE_TIME` seconds |
| Wrong artist name | Last.fm auto-correction should fix this; if not, check Last.fm database |
| Wrong album detected | Add the fake album name to `blacklist.txt` |

## License

MIT License - feel free to use, modify, and distribute.

## Contributing

Pull requests are welcome.
