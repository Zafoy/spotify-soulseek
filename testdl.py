# IMPORTS
import os
import sys
import json
import asyncio
import logging
import argparse
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
        self.spotify_id = spotify_id # will be used to rename downloaded tracks
        self.name = jsdict_get_safe(track_info, "name")
        self.artist = jsdict_get_safe(track_info, "artist")
        self.label = f"{self.name} - {self.artist}"

    async def process(self, client: SoulSeekClient, output_path: str):
        max_attempts = 3 # TODO: set this as a parameter
        for attempt in range(1, max_attempts + 1): # retry loop
            await update_line(self.index, f"{self.label} 🔍︎ Searching... (attempt {attempt}/{max_attempts})")
            try:
                # ATTEMPT TO SEARCH THE TRACK
                search_request = await client.searches.search(f"{self.name} {self.artist}")

                for _ in range(5): # gives up to 5 seconds for results to arrive
                    if search_request.results:
                        break
                    await asyncio.sleep(1)

                results = search_request.results
                if not results:
                    await update_line(self.index, f"{self.label} ✖ Not found")
                    return

                valid = [r for r in results if r.shared_items] # bool whether search results are shared items (downloadable)
                if not valid:
                    await update_line(self.index, f"{self.label} ✖ No files")
                    return

                await update_line(self.index, f"{self.label} ⬇️ Downloading... (attempt {attempt})")

                # sort peers by descending speed
                sorted_peers = sorted(valid, key=lambda r: r.avg_speed, reverse=True)

                # select peer starting with fastest
                peer = sorted_peers[(attempt - 1) % len(sorted_peers)] # % len makes sure we wrap around if attempt > peers
                username = peer.username # TODO: may want to display this

                # handle name and path to write downloaded file
                remote_path = peer.shared_items[0].filename
                ext = remote_path.split(".")[-1] # pull ext so we know how to save
                dest = os.path.join(output_path, f"{self.spotify_id}.{ext}") # save with spotify id as name
                
                # ATTEMPT TO DOWNLOAD THE TRACK
                try:
                    transfer: Transfer = await asyncio.wait_for(
                        client.transfers.download(username, remote_path),
                        timeout=20 # TODO: set as param?
                    )
                    transfer.local_path = dest

                    total_wait = 0
                    while not transfer.is_transfered():
                        await asyncio.sleep(1)
                        total_wait += 1
                        if total_wait > 60:
                            raise TimeoutError("Download timeout")

                    await update_line(self.index, f"{self.label} ✓ Done")
                    return

                except asyncio.TimeoutError:
                    await update_line(self.index, f"{self.label} ✖ Timeout")
                except Exception as e:
                    await update_line(self.index, f"{self.label} ✖ {type(e).__name__}")

            except Exception:
                await update_line(self.index, f"{self.label} ✖ Search failed")

            await asyncio.sleep(3)  # short delay before retry

        await update_line(self.index, f"{self.label} ✖ Failed after {max_attempts} attempts")
        # TODO: probably want these failed files to display at end of script

# script arguments
def parse_args():
    parser = argparse.ArgumentParser(description="SoulSeek downloader from json data.")
    parser.add_argument("filenames", nargs="*", default=["track_index.json"], help="Input JSON files")
    parser.add_argument("-o", "--output_path", default="output/", help="Output directory")
    parser.add_argument("-d", "--delay", type=int, default=30, help="Delay between tracks (seconds)")
    return parser.parse_args()


async def main():
    global total_tracks
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
        print("✖ Could not connect to SoulSeek.")
        sys.exit(1)

    # assign each track an index
    tracks = [Track(i, sid, info) for i, (sid, info) in enumerate(track_data.items())]
    total_tracks = len(tracks)

    for t in tracks:
        print(t.label)

    # process tracks
    for t in tracks:
        try:
            await t.process(client, args.output_path)
        except Exception:
            await update_line(t.index, f"{t.label} ✖ Unexpected error") # in case of unhandled error
        await asyncio.sleep(args.delay)

    print("\n✓ All downloads complete.")
    await client.stop()

# only run main if the file is executed directly
if __name__ == "__main__":
    total_tracks = 0
    asyncio.run(main())