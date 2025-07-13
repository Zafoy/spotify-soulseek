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

# ensure valid json
def jsdict_get_safe(d: dict[Any, Any], key: str) -> Any:
    val = d.get(key)
    if val is None:
        print(f"Malformed JSON. Missing key: {key}")
        sys.exit(1)
    return val

# disable aioslsk logging. will clutter terminal
def disable_aioslsk_logging():
    for logger_name in list(logging.root.manager.loggerDict.keys()):
        if logger_name.startswith("aioslsk"):
            logging.getLogger(logger_name).disabled = True

# functions to handle overwriting terminal lines
def move_cursor_to_line(n: int):
    sys.stdout.write(f"\033[{n}F\r")

def restore_cursor(lines: int):
    sys.stdout.write(f"\033[{lines}E")
    sys.stdout.flush()

async def update_line(index: int, text: str):
    move_cursor_to_line(total_tracks - index)
    print(text.ljust(100), end="\r", flush=True)
    restore_cursor(total_tracks - index)

# represents a single track. handles its search and download process
class Track:
    # constructor
    def __init__(self, index: int, spotify_id: str, track_info: dict[str, Any]):
        self.index = index
        self.spotify_id = spotify_id
        self.name = jsdict_get_safe(track_info, "name")
        self.artist = jsdict_get_safe(track_info, "artist")
        self.label = f"{self.name} - {self.artist}"

    # process this track
    async def process(self, client: SoulSeekClient, output_path: str, search_timeout: int, download_timeout: int):
        max_attempts = 3

        # check if file already exists (common extensions)
        possible_exts = ["mp3", "flac", "m4a", "wav", "ogg"]
        for ext in possible_exts:
            existing_path = os.path.join(output_path, f"{self.spotify_id}.{ext}")
            if os.path.exists(existing_path):
                await update_line(self.index, f"{self.label} ✅ Already exists")
                return True

        for _ in range(max_attempts):
            await update_line(self.index, f"{self.label} 🔍 Searching")

            try:
                # sanitize query string to remove invalid characters
                query = f"{self.name} {self.artist}"
                query = re.sub(r'[\\/:*?"<>|()\[\]]', '', query)
                query = re.sub(r'\s+', ' ', query).strip()

                # perform search
                search_request = await client.searches.search(query)

                for _ in range(search_timeout):
                    if search_request.results:
                        break
                    await asyncio.sleep(1)

                results = search_request.results
                valid = [r for r in results if r.shared_items]
                if not valid:
                    await update_line(self.index, f"{self.label} ❌ No files")
                    return False

                # sort peers by descending avg speed
                sorted_peers = sorted(valid, key=lambda r: r.avg_speed or 0, reverse=True)

                # try each peer until one succeeds
                for peer in sorted_peers:
                    username = peer.username
                    remote_path = peer.shared_items[0].filename
                    ext = remote_path.split(".")[-1]
                    dest = os.path.join(output_path, f"{self.spotify_id}.{ext}")

                    # skip if exact file already exists
                    if os.path.exists(dest):
                        await update_line(self.index, f"{self.label} ✅ Already exists")
                        return True

                    await update_line(self.index, f"{self.label} ⬇️  Downloading from {username}")

                    try:
                        transfer: Transfer = await client.transfers.download(username, remote_path)
                        transfer.local_path = dest

                        # wait for file to appear
                        for _ in range(10):
                            if os.path.exists(dest):
                                break
                            await asyncio.sleep(1)
                        else:
                            raise TimeoutError("File did not appear")

                        # wait for transfer to complete
                        total_wait = 0
                        while not transfer.is_transfered():
                            await asyncio.sleep(1)
                            total_wait += 1
                            if total_wait > download_timeout:
                                raise TimeoutError("Transfer stalled")

                        await update_line(self.index, f"{self.label} ✅ Done")
                        return True

                    except Exception as e:
                        await update_line(self.index, f"{self.label} ⚠️  Failed ({type(e).__name__})")
                        continue

                await update_line(self.index, f"{self.label} ❌ All peers failed")
                await asyncio.sleep(2)

            except Exception:
                await update_line(self.index, f"{self.label} ❌ Search failed")
                await asyncio.sleep(2)

        await update_line(self.index, f"{self.label} ❌ Failed after {max_attempts} attempts")
        return False

# script arguments
def parse_args():
    parser = argparse.ArgumentParser(description="SoulSeek downloader from JSON")
    parser.add_argument("filenames", nargs="*", default=["track_index.json"], help="Input JSON files")
    parser.add_argument("-o", "--output_path", default="output/", help="Output directory")
    parser.add_argument("-c", "--concurrent", type=int, default=2, help="Max concurrent downloads")
    parser.add_argument("--search-timeout", type=int, default=10, help="Timeout for search (seconds)")
    parser.add_argument("--download-timeout", type=int, default=60, help="Timeout for download (seconds)")
    return parser.parse_args()

# main async entry
async def main():
    global total_tracks, tracks
    args = parse_args()
    load_dotenv()
    disable_aioslsk_logging()

    username = getenv_safe("SOULSEEK_USERNAME")
    password = getenv_safe("SOULSEEK_PASSWORD")

    # pull track data from track index json(s)
    track_data = {}
    for file in args.filenames:
        try:
            with open(file, "r", encoding="utf-8") as f:
                track_data.update(json.load(f))
        except Exception as e:
            print(f"Failed to read {file}: {e}")
            sys.exit(1)

    os.makedirs(args.output_path, exist_ok=True) # make output directory

    # initialize soulseek client
    settings = Settings(credentials=CredentialsSettings(username=username, password=password))
    client = SoulSeekClient(settings)

    # attempt soulseek signin
    await client.start()
    try:
        await client.login()
    except ConnectionReadError:
        print("❌ Could not connect to SoulSeek.")
        sys.exit(1)

    # assign each track an index
    tracks = [Track(i, sid, info) for i, (sid, info) in enumerate(track_data.items())]
    total_tracks = len(tracks)

    for t in tracks:
        print(t.label)

    failed = [] # will store failed downloads

    # limit concurrency using semaphore
    sem = asyncio.Semaphore(args.concurrent)

    # safely run each track
    async def safe_process(track: Track):
        async with sem:
            success = await track.process(client, args.output_path, args.search_timeout, args.download_timeout)
            if not success:
                failed.append(track.label)

    # run all tracks concurrently respecting concurrency limit
    await asyncio.gather(*(safe_process(t) for t in tracks))

    print("\n✅ All downloads attempted.")
    if failed:
        print("\n❌ Failed downloads:")
        for name in failed:
            print(" -", name)

    await client.stop()

# only run main if the file is executed directly
if __name__ == "__main__":
    total_tracks = 0
    tracks = []
    asyncio.run(main())