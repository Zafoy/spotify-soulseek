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


def getenv_safe(key: str) -> str:
    val = os.getenv(key)
    if val is None:
        print(f"Missing environment variable: {key}")
        sys.exit(1)
    return val


def sanitize(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', '', text)


def disable_aioslsk_logging():
    for name in list(logging.root.manager.loggerDict.keys()):
        if name.startswith("aioslsk"):
            logging.getLogger(name).disabled = True


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


class Downloader:
    def __init__(self, client, outdir, search_timeout, download_timeout, ext, min_filesize=1000, max_attempts=3, verbose=False):
        self.client = client
        self.outdir = outdir
        self.search_timeout = search_timeout
        self.download_timeout = download_timeout
        self.ext = ext
        self.handled_albums = set()
        self.handled_playlists = set()
        self.min_filesize = min_filesize
        self.max_attempts = max_attempts
        self.verbose = verbose

    def log(self, message):
        if self.verbose:
            print(message)

    def print_result(self, track: Track, success: bool, cached=False):
        symbol = "📁" if cached else ("✅" if success else "❌")
        print(f"{symbol} {track.label}")

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
                        for _ in range(10):
                            if os.path.exists(dest):
                                break
                            await asyncio.sleep(1)
                        total_wait = 0
                        while not transfer.is_transfered():
                            await asyncio.sleep(1)
                            total_wait += 1
                            if total_wait > self.download_timeout:
                                raise TimeoutError("Transfer stalled")
                        if os.path.getsize(dest) < self.min_filesize:
                            raise ValueError("File size too small")
                        return True
                    except Exception:
                        continue
        except Exception:
            pass
        return False

    async def search_album_and_download_track(self, track: Track):
        dest = os.path.join(self.outdir, f"{track.sid}.{self.ext}")
        if os.path.exists(dest) and os.path.getsize(dest) >= self.min_filesize:
            self.print_result(track, True, cached=True)
            return True

        first_artist = track.artist.split(',')[0].split('&')[0].strip()
        query = sanitize(f"{track.album} {first_artist}")
        search = await self.client.searches.search(query)
        for _ in range(self.search_timeout):
            if search.results:
                break
            await asyncio.sleep(1)

        for attempt in range(self.max_attempts):
            found_peer = False
            for peer in sorted(search.results, key=lambda r: r.avg_speed or 0, reverse=True):
                for item in peer.shared_items:
                    if not item.filename.lower().endswith(f".{self.ext}"):
                        continue
                    if sanitize(track.name).lower() in sanitize(item.filename).lower():
                        found_peer = True
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
                                    raise TimeoutError("Transfer stalled")
                            if os.path.getsize(dest) < self.min_filesize:
                                raise ValueError("File size too small")
                            self.print_result(track, True)
                            return True
                        except Exception as e:
                            self.log(f"{track.label} ⚠️ Attempt {attempt+1} failed: {type(e).__name__}")
                            break
            if not found_peer:
                self.log(f"{track.label} ⚠️ Attempt {attempt+1} failed: No match")
            await asyncio.sleep(1)
        self.print_result(track, False)
        return False

    async def download_playlist(self, playlist: str, tracks: list[Track]):
        if playlist in self.handled_playlists:
            return
        self.handled_playlists.add(playlist)
        print(f"\n🎶 Playlist: {playlist}")  # <-- Always print the playlist name
        for track in tracks:
            dest = os.path.join(self.outdir, f"{track.sid}.{self.ext}")
            if os.path.exists(dest) and os.path.getsize(dest) >= self.min_filesize:
                self.print_result(track, True, cached=True)
                continue
            success = False
            if track.album:
                success = await self.search_album_and_download_track(track)
            if not success:
                self.log(f"{track.label} 🔁 Falling back to individual track search")
                query = sanitize(f"{track.name} {track.artist}")
                success = await self.download_file(query, dest)
                self.print_result(track, success)

    async def download_album(self, album: str, artist: str, tracks: list[Track]):
        if album in self.handled_albums:
            return
        self.handled_albums.add(album)
        print(f"\n📀 Album: {album} by {artist}")
        seen = set()
        for track in tracks:
            dest = os.path.join(self.outdir, f"{track.sid}.{self.ext}")
            if os.path.exists(dest) and os.path.getsize(dest) >= self.min_filesize:
                self.print_result(track, True, cached=True)
                seen.add(track.sid)
        missing = [t for t in tracks if t.sid not in seen]
        if not missing:
            return
        query = sanitize(f"{album} {artist}")
        search = await self.client.searches.search(query)
        for _ in range(self.search_timeout):
            if search.results:
                break
            await asyncio.sleep(1)
        for peer in sorted(search.results, key=lambda r: r.avg_speed or 0, reverse=True):
            remaining = [t for t in missing if t.sid not in seen]
            for item in peer.shared_items:
                if not item.filename.lower().endswith(f".{self.ext}"):
                    continue
                for track in remaining:
                    if sanitize(track.name).lower() in sanitize(item.filename).lower():
                        dest = os.path.join(self.outdir, f"{track.sid}.{self.ext}")
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
                            self.print_result(track, True)
                            seen.add(track.sid)
                        except Exception:
                            self.print_result(track, False)
            if len(seen) == len(tracks):
                return
        self.log("❌ Incomplete album. Filling individually.")
        for track in tracks:
            if track.sid not in seen:
                await self.download_playlist("Incomplete Album Fallback", [track])


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_files", nargs="*", default=["track_index.json"])
    parser.add_argument("-o", "--output", default="output")
    parser.add_argument("--ext", default="mp3")
    parser.add_argument("--search-timeout", type=int, default=10)
    parser.add_argument("--download-timeout", type=int, default=60)
    parser.add_argument("--verbose", action="store_true", help="enable verbose output")
    args = parser.parse_args()

    load_dotenv()
    disable_aioslsk_logging()
    os.makedirs(args.output, exist_ok=True)

    creds = CredentialsSettings(
        username=getenv_safe("SOULSEEK_USERNAME"),
        password=getenv_safe("SOULSEEK_PASSWORD")
    )
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

    dl = Downloader(client, args.output, args.search_timeout, args.download_timeout, args.ext, verbose=args.verbose)
    for album, album_tracks in album_map.items():
        await dl.download_album(album, album_tracks[0].artist, album_tracks)
    for pl, pl_tracks in playlist_map.items():
        await dl.download_playlist(pl, pl_tracks)

    await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
