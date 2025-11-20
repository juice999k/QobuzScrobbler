import os
import time
import threading
import logging
import requests
import unicodedata
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv
import pylast
import psutil
from win32gui import GetWindowText, EnumWindows
import win32process


# ---------- CONFIG ----------
load_dotenv()
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_API_SECRET = os.getenv("LASTFM_API_SECRET")
LASTFM_USERNAME = os.getenv("LASTFM_USERNAME")
LASTFM_PASSWORD = os.getenv("LASTFM_PASSWORD")

MIN_SCROBBLE_TIME = int(os.getenv("MIN_SCROBBLE_TIME", 30))
CHECK_INTERVAL = float(os.getenv("CHECK_INTERVAL", 2))
UPDATE_CHECK_URL = os.getenv("UPDATE_CHECK_URL") or None
CURRENT_VERSION = os.getenv("CURRENT_VERSION", "1.1.0")
LOG_FILE = os.getenv("LOG_FILE", "qscrobbler.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
BLACKLIST_FILE = os.getenv("BLACKLIST_FILE", "blacklist.txt")

# ---------- LOGGING ----------
logger = logging.getLogger("qscrobbler")
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
handler = RotatingFileHandler(LOG_FILE, maxBytes=3_000_000, backupCount=3, encoding="utf-8")
handler.setFormatter(formatter)
logger.addHandler(handler)
console = logging.StreamHandler()
console.setFormatter(formatter)
logger.addHandler(console)

# ---------- BLACKLIST ----------
def load_blacklist():
    """Load blacklisted albums from file."""
    blacklist = set()
    if os.path.exists(BLACKLIST_FILE):
        try:
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        blacklist.add(line.lower())
            if blacklist:
                logger.debug("Loaded %d blacklisted albums", len(blacklist))
        except Exception as e:
            logger.warning("Failed to load blacklist: %s", e)
    return blacklist

ALBUM_BLACKLIST = load_blacklist()

def is_album_blacklisted(album):
    """Check if album is in blacklist."""
    if not album:
        return False
    return album.lower().strip() in ALBUM_BLACKLIST

# ---------- LAST.FM CLIENT ----------
class LastFMClient:
    def __init__(self, api_key, api_secret, username, password):
        if not all([api_key, api_secret, username, password]):
            logger.error("Last.fm credentials missing. Set them in .env")
            raise RuntimeError("Missing Last.fm credentials")
        self.network = pylast.LastFMNetwork(
            api_key=api_key,
            api_secret=api_secret,
            username=username,
            password_hash=pylast.md5(password)
        )

    def update_now_playing(self, artist, track, album=None, duration=None):
        try:
            kwargs = {"artist": artist, "title": track}
            if album:
                kwargs["album"] = album
            if duration:
                kwargs["duration"] = int(duration)
            self.network.update_now_playing(**kwargs)
            logger.info("Now playing: %s - %s%s", artist, track, f" [{album}]" if album else "")
        except Exception as e:
            logger.exception("Failed to update now playing: %s", e)

    def scrobble(self, artist, track, timestamp, album=None):
        try:
            kwargs = {"artist": artist, "title": track, "timestamp": int(timestamp)}
            if album:
                kwargs["album"] = album
            self.network.scrobble(**kwargs)
            logger.info("Scrobbled: %s - %s%s", artist, track, f" [{album}]" if album else "")
        except Exception as e:
            logger.exception("Failed to scrobble: %s", e)
            raise

# ---------- WINDOW MONITOR ----------
def find_qobuz_title():
    """Find the Qobuz window and extract its title."""
    for proc in psutil.process_iter(['pid', 'name']):
        name = proc.info.get('name') or ""
        if 'qobuz' in name.lower():
            pid = proc.info['pid']
            titles = []
            def cb(hwnd, acc):
                if win32process.GetWindowThreadProcessId(hwnd)[1] == pid:
                    title = GetWindowText(hwnd)
                    if title:
                        acc.append(title)
                return True
            EnumWindows(cb, titles)
            for t in titles:
                if " - " in t:
                    return t.strip()
    return None

def parse_title(title):
    """Parse artist and track from Qobuz window title."""
    if not title:
        return None, None
    t = title.strip()
    for sep in [" — Qobuz", " - Qobuz", " – Qobuz"]:
        if t.endswith(sep):
            t = t[:-len(sep)].strip()
    parts = t.split(" - ")
    if len(parts) >= 2:
        track = parts[0].strip()
        artist = parts[1].strip()
        return artist, track
    parts = t.split(" — ")
    if len(parts) >= 2:
        track = parts[0].strip()
        artist = parts[1].strip()
        return artist, track
    return None, None

# ---------- STRING UTILITIES ----------
def normalize_string(s):
    """Normalize string for comparison (lowercase, no accents, trimmed)."""
    if not s:
        return ""
    s = s.lower().strip()
    s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII')
    s = ' '.join(s.split())
    return s

def strings_match(s1, s2, threshold=0.85):
    """Check if two strings are similar using fuzzy matching."""
    s1_norm = normalize_string(s1)
    s2_norm = normalize_string(s2)
    if s1_norm == s2_norm:
        return True
    if s1_norm in s2_norm or s2_norm in s1_norm:
        return True
    if len(s1_norm) == 0 or len(s2_norm) == 0:
        return False
    max_len = max(len(s1_norm), len(s2_norm))
    matches = sum(a == b for a, b in zip(s1_norm, s2_norm))
    ratio = matches / max_len
    return ratio >= threshold

# ---------- ARTIST CORRECTION ----------
def get_corrected_artist(artist):
    """Get the correct artist name from Last.fm (handles accents, typos, etc.)."""
    if not LASTFM_API_KEY:
        return artist
    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "artist.getCorrection",
            "api_key": LASTFM_API_KEY,
            "artist": artist,
            "format": "json"
        }
        r = requests.get(url, params=params, timeout=6)
        if not r.ok:
            return artist
        data = r.json()
        corrections = data.get("corrections", {})
        if corrections and isinstance(corrections, dict):
            correction = corrections.get("correction", {})
            if correction and isinstance(correction, dict):
                corrected = correction.get("artist", {})
                if corrected and isinstance(corrected, dict):
                    name = corrected.get("name")
                    if name and name.lower() != artist.lower():
                        logger.info("  | Artist corrected: %s -> %s", artist, name)
                        return name
        return artist
    except Exception as e:
        logger.debug("Artist correction failed: %s", e)
        return artist

def get_artist_info(artist):
    """Get canonical artist name from Last.fm artist.getInfo."""
    if not LASTFM_API_KEY:
        return artist
    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "artist.getInfo",
            "api_key": LASTFM_API_KEY,
            "artist": artist,
            "format": "json"
        }
        r = requests.get(url, params=params, timeout=6)
        if not r.ok:
            return artist
        data = r.json()
        artist_info = data.get("artist", {})
        name = artist_info.get("name")
        if name:
            if name != artist:
                logger.info("  | Artist resolved: %s -> %s", artist, name)
            return name
        return artist
    except Exception as e:
        logger.debug("Artist info lookup failed: %s", e)
        return artist

def resolve_artist(artist):
    """Try to get the correct artist name using Last.fm APIs."""
    # First try correction API
    corrected = get_corrected_artist(artist)
    if corrected != artist:
        return corrected
    # Then try artist.getInfo for canonical name
    resolved = get_artist_info(artist)
    return resolved

# ---------- QOBUZ METADATA ----------
def qobuz_search_public(query, artist_hint=None, track_hint=None):
    """Search Qobuz public API for track metadata."""
    try:
        url = "https://www.qobuz.com/api.json/0.2/search"
        params = {"query": query, "type": "track", "limit": 10}
        r = requests.get(url, params=params, timeout=6)
        if not r.ok:
            logger.debug("Qobuz API returned status %d", r.status_code)
            return None
        j = r.json()
        candidates = []
        if "tracks" in j and isinstance(j["tracks"], dict):
            items = j["tracks"].get("items") or j["tracks"].get("data") or []
            candidates = items
        elif "tracks" in j and isinstance(j["tracks"], list):
            candidates = j["tracks"]
        if not candidates:
            return None
        best_match = None
        best_score = 0
        for c in candidates:
            title = c.get("title") or c.get("name") or c.get("track_name")
            artists = c.get("performer") or c.get("artist") or c.get("artists")
            if isinstance(artists, list) and len(artists) > 0:
                artist_name = artists[0].get("name") if isinstance(artists[0], dict) else str(artists[0])
            elif isinstance(artists, dict):
                artist_name = artists.get("name")
            else:
                artist_name = str(artists) if artists else None
            album = None
            if "album" in c and isinstance(c["album"], dict):
                album = c["album"].get("title") or c["album"].get("name")
            
            # Skip blacklisted albums
            if is_album_blacklisted(album):
                logger.debug("Skipping blacklisted album: %s", album)
                continue
            
            duration = c.get("duration") or c.get("length")
            if duration and duration > 10000:
                duration = int(duration / 1000)
            if not title or not artist_name:
                continue
            score = 0
            if artist_hint and strings_match(artist_name, artist_hint):
                score += 50
            if track_hint and strings_match(title, track_hint):
                score += 50
            if album:
                score += 10
            if score > best_score:
                best_score = score
                best_match = {
                    "artist": artist_name, "track": title, "album": album,
                    "duration": int(duration) if duration else None
                }
        return best_match
    except Exception as e:
        logger.debug("Qobuz search error: %s", e)
    return None

# ---------- MUSICBRAINZ FALLBACK ----------
# Valid official release types
VALID_RELEASE_TYPES = {"Album", "EP"}

def musicbrainz_search(artist, track):
    """Search MusicBrainz for album info. Only returns official releases."""
    try:
        headers = {"User-Agent": "QScrobbler/1.1 (qobuz-lastfm-scrobbler)"}
        query = f'recording:"{track}" AND artist:"{artist}"'
        url = "https://musicbrainz.org/ws/2/recording"
        params = {"query": query, "fmt": "json", "limit": 10}
        r = requests.get(url, params=params, headers=headers, timeout=8)
        if not r.ok:
            logger.debug("MusicBrainz returned status %d", r.status_code)
            return None
        data = r.json()
        recordings = data.get("recordings", [])
        
        for rec in recordings:
            artists = rec.get("artist-credit", [])
            artist_match = any(
                strings_match(a.get("name", ""), artist)
                for a in artists if isinstance(a, dict)
            )
            if not artist_match:
                continue
            
            releases = rec.get("releases", [])
            
            # First pass: look for official albums only
            for release in releases:
                album_title = release.get("title")
                if not album_title:
                    continue
                
                # Skip blacklisted
                if is_album_blacklisted(album_title):
                    logger.debug("Skipping blacklisted album: %s", album_title)
                    continue
                
                release_group = release.get("release-group", {})
                primary_type = release_group.get("primary-type", "")
                secondary_types = release_group.get("secondary-types", [])
                
                # Skip compilations, bootlegs, mixtapes, etc.
                if secondary_types:
                    skip_types = {"Compilation", "DJ-mix", "Mixtape/Street", "Demo", "Live", "Bootleg"}
                    if any(st in skip_types for st in secondary_types):
                        logger.debug("Skipping non-official release: %s (%s)", album_title, secondary_types)
                        continue
                
                # Only accept Album or EP
                if primary_type in VALID_RELEASE_TYPES:
                    logger.debug("MusicBrainz found official album: %s", album_title)
                    return {"album": album_title, "source": "musicbrainz"}
            
            # Second pass: accept Album/EP without secondary types
            for release in releases:
                album_title = release.get("title")
                if not album_title or is_album_blacklisted(album_title):
                    continue
                
                release_group = release.get("release-group", {})
                primary_type = release_group.get("primary-type", "")
                secondary_types = release_group.get("secondary-types", [])
                
                if primary_type in VALID_RELEASE_TYPES and not secondary_types:
                    logger.debug("MusicBrainz found release: %s", album_title)
                    return {"album": album_title, "source": "musicbrainz"}
        
        return None
    except Exception as e:
        logger.debug("MusicBrainz search error: %s", e)
        return None

# ---------- LAST.FM ALBUM SEARCH ----------
def lastfm_search_album(artist, track):
    """Search Last.fm for album info using existing API key."""
    if not LASTFM_API_KEY:
        return None
    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "track.getInfo", "api_key": LASTFM_API_KEY,
            "artist": artist, "track": track, "format": "json"
        }
        r = requests.get(url, params=params, timeout=6)
        if not r.ok:
            return None
        data = r.json()
        track_info = data.get("track", {})
        album_info = track_info.get("album", {})
        album_title = album_info.get("title")
        
        if album_title:
            # Skip blacklisted
            if is_album_blacklisted(album_title):
                logger.debug("Skipping blacklisted album from Last.fm: %s", album_title)
                return None
            logger.debug("Last.fm found album: %s", album_title)
            return {"album": album_title, "source": "lastfm"}
        return None
    except Exception as e:
        logger.debug("Last.fm search error: %s", e)
        return None

# ---------- METADATA WITH FALLBACKS ----------
def get_track_metadata(artist, track):
    """Fetch track metadata. Priority: Qobuz > Last.fm > MusicBrainz"""
    query = f"{track} {artist}"
    meta = qobuz_search_public(query, artist_hint=artist, track_hint=track)
    if meta and meta.get("album"):
        logger.debug("Album found via Qobuz: %s", meta.get("album"))
        return meta
    if meta is None:
        meta = {}
    
    logger.debug("Qobuz returned no album, trying Last.fm...")
    lastfm_meta = lastfm_search_album(artist, track)
    if lastfm_meta and lastfm_meta.get("album"):
        meta["album"] = lastfm_meta["album"]
        logger.info("  | Album via Last.fm: %s", meta["album"])
        return meta
    
    logger.debug("Last.fm returned no album, trying MusicBrainz...")
    time.sleep(0.5)
    mb_meta = musicbrainz_search(artist, track)
    if mb_meta and mb_meta.get("album"):
        meta["album"] = mb_meta["album"]
        logger.info("  | Album via MusicBrainz: %s", meta["album"])
        return meta
    
    logger.warning("  | No album found for: %s - %s", artist, track)
    return meta if meta else None



# ---------- SCROBBLER CORE ----------
class QobuzScrobbler:
    def __init__(self):
        self.lfm = None
        try:
            self.lfm = LastFMClient(LASTFM_API_KEY, LASTFM_API_SECRET, LASTFM_USERNAME, LASTFM_PASSWORD)
        except Exception:
            logger.warning("Last.fm not configured, scrobbling disabled")
        self.current = None
        self.start_time = None
        self.scrobbled = False
        self.lock = threading.Lock()
        self.last_meta = None
        self.running = True
        self.pending_scrobble = None
        self.artist_cache = {}  # Cache corrected artist names

    def reload_blacklist(self):
        """Reload the album blacklist from file."""
        global ALBUM_BLACKLIST
        ALBUM_BLACKLIST = load_blacklist()
        logger.info("Blacklist reloaded: %d entries", len(ALBUM_BLACKLIST))

    def get_cached_artist(self, artist):
        """Get corrected artist name, using cache."""
        if artist in self.artist_cache:
            return self.artist_cache[artist]
        corrected = resolve_artist(artist)
        self.artist_cache[artist] = corrected
        return corrected

    def force_scrobble(self):
        """Manually trigger a scrobble for the current track."""
        with self.lock:
            if self.current and not self.scrobbled:
                artist, track = self.current
                try:
                    timestamp = int(time.time())
                    meta = self.last_meta or {}
                    album = meta.get("album")
                    if self.lfm:
                        self.lfm.scrobble(artist, track, timestamp, album=album)
                    self.scrobbled = True
                    logger.info("Force scrobbled: %s - %s", artist, track)
                except Exception:
                    logger.exception("Force scrobble failed")

    def scrobble_pending(self):
        """Scrobble the pending track if it meets minimum play time."""
        if self.pending_scrobble:
            artist, track, start_time, meta = self.pending_scrobble
            played = time.time() - start_time
            if played >= MIN_SCROBBLE_TIME:
                try:
                    timestamp = int(start_time)
                    album = meta.get("album")
                    if self.lfm:
                        self.lfm.scrobble(artist, track, timestamp, album=album)
                    logger.info("  | Played for %.1fs", played)
                except Exception:
                    logger.exception("Failed to scrobble pending track")
            else:
                logger.info("Track too short to scrobble: %s - %s (%.1fs < %ds)", 
                           artist, track, played, MIN_SCROBBLE_TIME)
            self.pending_scrobble = None

    def stop(self):
        """Stop the scrobbler gracefully."""
        logger.info("Stopping scrobbler...")
        self.scrobble_pending()
        self.running = False


    def check_update(self):
        """Check for new versions if update URL is configured."""
        if not UPDATE_CHECK_URL:
            return
        try:
            r = requests.get(UPDATE_CHECK_URL, timeout=5)
            if r.ok:
                latest = r.text.strip()
                if latest != CURRENT_VERSION:
                    logger.info("New version available: %s (current: %s)", latest, CURRENT_VERSION)
        except Exception as e:
            logger.debug("Update check failed: %s", e)

    def loop(self):
        """Main loop that monitors Qobuz and handles scrobbling."""
        logger.info("=" * 55)
        logger.info("QScrobbler v%s started", CURRENT_VERSION)
        logger.info("  Min scrobble time: %ds", MIN_SCROBBLE_TIME)
        logger.info("  Check interval: %.1fs", CHECK_INTERVAL)
        logger.info("  Last.fm: %s", "connected" if self.lfm else "not configured")
        logger.info("  Album sources: Qobuz > Last.fm > MusicBrainz")
        logger.info("  Blacklisted albums: %d", len(ALBUM_BLACKLIST))
        logger.info("=" * 55)
        

        while self.running:
            try:
                title = find_qobuz_title()
                if title:
                    artist_raw, track = parse_title(title)
                    if artist_raw and track:
                        # Correct artist name via Last.fm
                        artist = self.get_cached_artist(artist_raw)
                        
                        if (self.current is None) or (self.current != (artist, track)):
                            if self.current is not None:
                                self.scrobble_pending()
                            
                            self.current = (artist, track)
                            self.start_time = time.time()
                            self.scrobbled = False
                            
                            logger.info("")
                            logger.info("Now playing: %s - %s", artist, track)
                            
                            logger.debug("Fetching metadata...")
                            meta = get_track_metadata(artist, track) or {}
                            self.last_meta = meta
                            
                            album = meta.get("album")
                            duration = meta.get("duration")
                            
                            if album:
                                logger.info("  | Album: %s", album)
                            if duration:
                                logger.info("  | Duration: %d:%02d", duration // 60, duration % 60)
                            
                            self.pending_scrobble = (artist, track, self.start_time, meta)
                            
                            if self.lfm:
                                try:
                                    self.lfm.update_now_playing(artist, track, album=album, duration=duration)
                                except Exception:
                                    pass
                else:
                    if self.current:
                        logger.debug("Qobuz window not found")
                        self.scrobble_pending()
                        self.current = None
                        self.start_time = None
                        self.scrobbled = False
                        self.last_meta = None

                self.check_update()
                time.sleep(CHECK_INTERVAL)
            except KeyboardInterrupt:
                logger.info("")
                logger.info("Keyboard interrupt received")
                self.stop()
            except Exception:
                logger.exception("Error in main loop")
                time.sleep(CHECK_INTERVAL)

def main():
    if not LASTFM_API_KEY:
        logger.warning("Last.fm not configured. Set credentials in .env to enable scrobbling.")
        logger.warning("Script will still run and display current track.")
    scrobbler = QobuzScrobbler()
    try:
        scrobbler.loop()
    except Exception:
        logger.exception("Fatal error")
    finally:
        logger.info("Goodbye!")

if __name__ == "__main__":
    main()
