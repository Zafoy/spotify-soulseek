# Soulseek Album & Playlist Downloader (Improved Playlist Downloading)
import os
import sys
import json
import asyncio
import argparse
import logging
import re
from typing import Any
from dotenv import load_dotenv
from collections import defaultdict
from aioslsk.client import SoulSeekClient
from aioslsk.settings import Settings, CredentialsSettings
from aioslsk.transfer.model import Transfer
from aioslsk.exceptions import ConnectionReadError

# Helpers
def getenv_safe(key: str) -> str:
    val = os.getenv(key)
    if val is None:
        print(f"Missing environment variable: {key}")
        sys.exit(1)
    return val

def sanitize(text):
    return re.sub(r'[\\/:*?"<>|]', '', text)

def disable_aioslsk_logging():
    for name in list(logging.root.manager.loggerDict.keys()):
        if name.startswith("aioslsk"):
            logging.getLogger(name).disabled = True

# Track class
class Track:
    def __init__(self, sid: str, info: dict[str, Any]):
        self.sid = sid
        self.name = info['name']
        self.artist = info['artist']
        self.album = info.get('album')
        self.sources = info.get('sources', [])
        self.album_source = any(s['type'] == 'album' for s in self.sources)
        self.playlists = [s['playlist_name'] for s in self.sources if s['type'] == 'playlist']
        self.label = f"{self.name} - {self.artist}"

# Album and Playlist downloader
class Downloader:
    def __init__(self, client, outdir, search_timeout, download_timeout, ext, min_filesize=1000, max_attempts=3):
        self.client = client
        self.outdir = outdir
        self.search_timeout = search_timeout
        self.download_timeout = download_timeout
        self.ext = ext
        self.handled_albums = set()
        self.handled_playlists = set()
        self.min_filesize = min_filesize
        self.max_attempts = max_attempts

    async def download_file(self, query, dest):
        try:
            search = await self.client.searches.search(query)
            for _ in range(self.search_timeout):
                if search.results:
                    break
                await asyncio.sleep(1)
            for peer in sorted(search.results, key=lambda r: r.avg_speed or 0, reverse=True):
                for item in peer.shared_items:
                    if not item.filename.lower().endswith(f".{self.ext}"):
                        continue
                    try:
                        transfer = await self.client.transfers.download(peer.username, item.filename)
                        transfer.local_path = dest
                        # Wait for file to appear
                        for _ in range(10):
                            if os.path.exists(dest):
                                break
                            await asyncio.sleep(1)
                        # Wait for transfer to complete
                        total_wait = 0
                        while not transfer.is_transfered():
                            await asyncio.sleep(1)
                            total_wait += 1
                            if total_wait > self.download_timeout:
                                raise TimeoutError("Transfer stalled")
                        # Check file size to avoid corrupted/incomplete files
                        if os.path.getsize(dest) < self.min_filesize:
                            raise ValueError("File size too small")
                        return True
                    except Exception:
                        # On failure try next peer/item
                        continue
        except Exception:
            pass
        return False

    async def search_album_and_download_track(self, track: Track):
        # Search by album to find track from album peers
        query = sanitize(f"{track.album} {track.artist}")
        search = await self.client.searches.search(query)
        for _ in range(self.search_timeout):
            if search.results:
                break
            await asyncio.sleep(1)

        # Try up to max_attempts
        for attempt in range(self.max_attempts):
            for peer in sorted(search.results, key=lambda r: r.avg_speed or 0, reverse=True):
                for item in peer.shared_items:
                    if not item.filename.lower().endswith(f".{self.ext}"):
                        continue
                    # Looser match: check if sanitized track name is substring of sanitized filename
                    if sanitize(track.name).lower() in sanitize(item.filename).lower():
                        dest = os.path.join(self.outdir, f"{track.sid}.{self.ext}")
                        if os.path.exists(dest) and os.path.getsize(dest) >= self.min_filesize:
                            print(f"{track.label} ✅ Done (cached)")
                            return True
                        try:
                            transfer = await self.client.transfers.download(peer.username, item.filename)
                            transfer.local_path = dest
                            # Wait for file to appear
                            for _ in range(10):
                                if os.path.exists(dest):
                                    break
                                await asyncio.sleep(1)
                            # Wait for transfer to complete
                            total_wait = 0
                            while not transfer.is_transfered():
                                await asyncio.sleep(1)
                                total_wait += 1
                                if total_wait > self.download_timeout:
                                    raise TimeoutError("Transfer stalled")
                            # Check file size to avoid corrupted/incomplete files
                            if os.path.getsize(dest) < self.min_filesize:
                                raise ValueError("File size too small")
                            print(f"{track.label} ✅ Done")
                            return True
                        except Exception as e:
                            print(f"{track.label} ⚠️ Attempt {attempt+1} failed: {type(e).__name__}")
            # Wait a bit before next attempt
            await asyncio.sleep(1)
        # All attempts failed
        print(f"{track.label} ❌ Failed after {self.max_attempts} attempts")
        return False

    async def download_playlist(self, playlist: str, tracks: list[Track]):
        if playlist in self.handled_playlists:
            return
        self.handled_playlists.add(playlist)
        print(f"\n🎶 Playlist: {playlist}")
        for track in tracks:
            dest = os.path.join(self.outdir, f"{track.sid}.{self.ext}")
            if os.path.exists(dest) and os.path.getsize(dest) >= self.min_filesize:
                continue
            success = False
            # If track is part of album, try album search for track first
            if track.album:
                success = await self.search_album_and_download_track(track)
            if not success:
                # Fallback: individual track search
                query = sanitize(f"{track.name} {track.artist}")
                success = await self.download_file(query, dest)
                if success:
                    print(f"{track.label} ✅ Done")
                else:
                    print(f"{track.label} ❌ Failed")

    async def download_album(self, album: str, artist: str, tracks: list[Track]):
        if album in self.handled_albums:
            return
        self.handled_albums.add(album)
        print(f"\n📀 Album: {album} by {artist}")
        missing = [t for t in tracks if not os.path.exists(os.path.join(self.outdir, f"{t.sid}.{self.ext}")) or os.path.getsize(os.path.join(self.outdir, f"{t.sid}.{self.ext}")) < self.min_filesize]
        if not missing:
            return
        query = sanitize(f"{album} {artist}")
        search = await self.client.searches.search(query)
        for _ in range(self.search_timeout):
            if search.results:
                break
            await asyncio.sleep(1)
        for peer in sorted(search.results, key=lambda r: r.avg_speed or 0, reverse=True):
            remaining = list(missing)
            for item in peer.shared_items:
                if not item.filename.lower().endswith(f".{self.ext}"):
                    continue
                for track in remaining:
                    if sanitize(track.name).lower() in sanitize(item.filename).lower():
                        dest = os.path.join(self.outdir, f"{track.sid}.{self.ext}")
                        if os.path.exists(dest) and os.path.getsize(dest) >= self.min_filesize:
                            print(f"{track.name} ✅")
                            remaining.remove(track)
                            continue
                        try:
                            transfer = await self.client.transfers.download(peer.username, item.filename)
                            transfer.local_path = dest
                            for _ in range(10):
                                if os.path.exists(dest):
                                    break
                                await asyncio.sleep(1)
                            total_wait = 0
                            while not transfer.is_transfered():
                                await asyncio.sleep(1)
                                total_wait += 1
                                if total_wait > self.download_timeout:
                                    raise TimeoutError("Stalled")
                            if os.path.getsize(dest) < self.min_filesize:
                                raise ValueError("File size too small")
                            print(f"{track.name} ✅")
                            remaining.remove(track)
                        except Exception as e:
                            print(f"{track.name} ❌ {type(e).__name__}")
            if not remaining:
                return
        print("❌ Incomplete album. Filling individually.")
        for track in missing:
            await self.download_playlist("Incomplete Album Fallback", [track])

# Main
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_files", nargs="*", default=["track_index.json"])
    parser.add_argument("-o", "--output", default="output")
    parser.add_argument("--ext", default="mp3")
    parser.add_argument("--search-timeout", type=int, default=10)
    parser.add_argument("--download-timeout", type=int, default=60)
    args = parser.parse_args()

    load_dotenv()
    disable_aioslsk_logging()
    os.makedirs(args.output, exist_ok=True)

    creds = CredentialsSettings(username=getenv_safe("SOULSEEK_USERNAME"), password=getenv_safe("SOULSEEK_PASSWORD"))
    settings = Settings(credentials=creds)
    client = SoulSeekClient(settings)
    await client.start()
    try:
        await client.login()
    except ConnectionReadError:
        print("❌ Could not connect to SoulSeek")
        sys.exit(1)

    data = {}
    for path in args.json_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data.update(json.load(f))
        except Exception as e:
            print(f"Failed to load {path}: {e}")
            sys.exit(1)

    tracks = [Track(sid, info) for sid, info in data.items()]
    album_map = defaultdict(list)
    playlist_map = defaultdict(list)

    for track in tracks:
        if track.album_source:
            album_map[track.album].append(track)
        for pl in track.playlists:
            playlist_map[pl].append(track)

    dl = Downloader(client, args.output, args.search_timeout, args.download_timeout, args.ext)
    for album, album_tracks in album_map.items():
        await dl.download_album(album, album_tracks[0].artist, album_tracks)
    for pl, pl_tracks in playlist_map.items():
        await dl.download_playlist(pl, pl_tracks)

    await client.stop()

if __name__ == "__main__":
    asyncio.run(main())
