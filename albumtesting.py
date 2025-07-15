# IMPORTS
import os
import sys
import json
import asyncio
import logging
import argparse
import re
from typing import Any
from dotenv import load_dotenv
from collections import defaultdict
from aioslsk.client import SoulSeekClient
from aioslsk.settings import Settings, CredentialsSettings
from aioslsk.transfer.model import Transfer
from aioslsk.exceptions import ConnectionReadError

# ensure environment variable is set
def getenv_safe(key: str) -> str:
    val = os.getenv(key)
    if val is None:
        print(f"Missing environment variable: {key}")
        sys.exit(1)
    return val

# disable aioslsk logging
def disable_aioslsk_logging():
    for logger_name in list(logging.root.manager.loggerDict.keys()):
        if logger_name.startswith("aioslsk"):
            logging.getLogger(logger_name).disabled = True

# sanitize filenames for compatibility
def sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '', name)

album_locks = {}

class Track:
    handled_albums = set()
    album_to_tracks = defaultdict(list)
    track_data = {}

    def __init__(self, index: int, spotify_id: str, track_info: dict[str, Any]):
        self.index = index
        self.spotify_id = spotify_id
        self.name = track_info["name"]
        self.artist = track_info["artist"]
        self.album = track_info.get("album")
        self.sources = track_info["sources"]
        self.label = f"{self.name} - {self.artist}"
        self.is_album = any(s["type"] == "album" for s in self.sources)

    async def process(self, client: SoulSeekClient, output_path: str, search_timeout: int, download_timeout: int, preferred_ext: str):
        max_attempts = 3

        def file_exists():
            return os.path.exists(os.path.join(output_path, f"{self.spotify_id}.{preferred_ext}"))

        if file_exists():
            return True

        if self.is_album and self.album:
            if self.album not in album_locks:
                album_locks[self.album] = asyncio.Lock()

            async with album_locks[self.album]:
                if file_exists():
                    return True

                if self.album not in Track.handled_albums:
                    Track.handled_albums.add(self.album)
                    album_tracks = Track.album_to_tracks[self.album]
                    found_tracks = set()

                    print(f"\n📀 Album: {self.album} by {self.artist}")

                    query = re.sub(r'[^\w\s]', '', f"{self.album} {self.artist}").strip()

                    try:
                        search_request = await client.searches.search(query)
                        for _ in range(search_timeout):
                            if search_request.results:
                                break
                            await asyncio.sleep(1)

                        valid_peers = [r for r in search_request.results if r.shared_items]
                        sorted_peers = sorted(valid_peers, key=lambda r: r.avg_speed or 0, reverse=True)

                        for peer in sorted_peers:
                            peer_found = set()

                            for item in peer.shared_items:
                                filename = os.path.basename(item.filename)
                                if not filename.lower().endswith(f".{preferred_ext}"):
                                    continue

                                match_sid = next((sid for sid in album_tracks
                                                  if sanitize_filename(Track.track_data[sid]["name"]).lower() in sanitize_filename(filename.lower())
                                                  and sid not in found_tracks), None)
                                if not match_sid:
                                    continue

                                dest = os.path.join(output_path, f"{match_sid}.{preferred_ext}")
                                if os.path.exists(dest):
                                    found_tracks.add(match_sid)
                                    peer_found.add(match_sid)
                                    print(f"{Track.track_data[match_sid]['name']} ✅")
                                    continue

                                try:
                                    transfer = await client.transfers.download(peer.username, item.filename)
                                    transfer.local_path = dest

                                    for _ in range(10):
                                        if os.path.exists(dest):
                                            break
                                        await asyncio.sleep(1)
                                    else:
                                        continue

                                    total_wait = 0
                                    while not transfer.is_transfered():
                                        await asyncio.sleep(1)
                                        total_wait += 1
                                        if total_wait > download_timeout:
                                            raise TimeoutError("Transfer stalled")

                                    found_tracks.add(match_sid)
                                    peer_found.add(match_sid)
                                    print(f"{Track.track_data[match_sid]['name']} ✅")

                                except Exception:
                                    continue

                            if len(found_tracks) >= len(album_tracks):
                                return True

                        if len(found_tracks) < len(album_tracks):
                            print(f"❌ Incomplete album. Attempting individual tracks.")
                    except Exception:
                        print(f"❌ Album search failed. Attempting individual tracks.")

        for _ in range(max_attempts):
            print(f"{self.label} 🔍 Searching")
            try:
                query = re.sub(r'[^\w\s]', '', f"{self.name} {self.artist}").strip()
                search_request = await client.searches.search(query)

                for _ in range(search_timeout):
                    if search_request.results:
                        break
                    await asyncio.sleep(1)

                results = [r for r in search_request.results if r.shared_items]
                sorted_peers = sorted(results, key=lambda r: r.avg_speed or 0, reverse=True)

                for peer in sorted_peers:
                    item = next((i for i in peer.shared_items if i.filename.lower().endswith(f".{preferred_ext}")), None)
                    if not item:
                        continue

                    dest = os.path.join(output_path, f"{self.spotify_id}.{preferred_ext}")
                    print(f"{self.label} ⬇️  Downloading from {peer.username}")
                    try:
                        transfer = await client.transfers.download(peer.username, item.filename)
                        transfer.local_path = dest

                        for _ in range(10):
                            if os.path.exists(dest):
                                break
                            await asyncio.sleep(1)

                        total_wait = 0
                        while not transfer.is_transfered():
                            await asyncio.sleep(1)
                            total_wait += 1
                            if total_wait > download_timeout:
                                raise TimeoutError("Transfer stalled")

                        print(f"{self.label} ✅ Done")
                        return True

                    except Exception as e:
                        print(f"{self.label} ⚠️  Failed ({type(e).__name__})")
                        continue

            except Exception:
                print(f"{self.label} ❌ Search failed")
                await asyncio.sleep(2)

        print(f"{self.label} ❌ Failed after {max_attempts} attempts")
        return False

# MAIN FUNCTION
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("filenames", nargs="*", default=["track_index.json"])
    parser.add_argument("-o", "--output_path", default="output")
    parser.add_argument("--search-timeout", type=int, default=10)
    parser.add_argument("--download-timeout", type=int, default=60)
    parser.add_argument("--concurrent", type=int, default=2)
    parser.add_argument("--preferred-ext", default="mp3")
    return parser.parse_args()

async def main():
    args = parse_args()
    load_dotenv()
    disable_aioslsk_logging()

    username = getenv_safe("SOULSEEK_USERNAME")
    password = getenv_safe("SOULSEEK_PASSWORD")

    track_data = {}
    for file in args.filenames:
        try:
            with open(file, "r", encoding="utf-8") as f:
                track_data.update(json.load(f))
        except Exception as e:
            print(f"Failed to read {file}: {e}")
            sys.exit(1)

    os.makedirs(args.output_path, exist_ok=True)
    settings = Settings(credentials=CredentialsSettings(username=username, password=password))
    client = SoulSeekClient(settings)

    await client.start()
    try:
        await client.login()
    except ConnectionReadError:
        print("❌ Could not connect to SoulSeek.")
        sys.exit(1)

    Track.track_data = track_data
    tracks = [Track(i, sid, info) for i, (sid, info) in enumerate(track_data.items())]
    for t in tracks:
        if t.album:
            Track.album_to_tracks[t.album].append(t.spotify_id)

    failed = []
    sem = asyncio.Semaphore(args.concurrent)

    async def safe_process(track):
        async with sem:
            success = await track.process(client, args.output_path, args.search_timeout, args.download_timeout, args.preferred_ext)
            if not success:
                failed.append(track.label)

    await asyncio.gather(*(safe_process(t) for t in tracks))

    print("\n✅ All downloads attempted.")
    if failed:
        print("\n❌ Failed downloads:")
        for name in failed:
            print(" -", name)

    await client.stop()

if __name__ == "__main__":
    asyncio.run(main())