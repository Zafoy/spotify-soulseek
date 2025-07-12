from argparse import Namespace


import argparse
from aioslsk.transfer.model import Transfer
from fileinput import filename
from typing import Any
import asyncio
from io import TextIOWrapper
import logging
import os
import sys
import json
from aioslsk.client import SoulSeekClient
from aioslsk.events import SearchRequestRemovedEvent, SearchResult, SearchResultEvent
from aioslsk.exceptions import ConnectionReadError, PeerConnectionError
from aioslsk.log_utils import MessageFilter
from aioslsk.network.network import ConnectToPeer
from aioslsk.settings import Settings, CredentialsSettings
from dotenv import load_dotenv


def getenv_safe(key: str) -> str:
    var = os.getenv(key)

    if var is None:
        print(f"Unable to find {key} in the environment")
        exit()

    return var


# Initialize argument parser
parser = argparse.ArgumentParser()

parser.add_argument("filenames", nargs="+")
parser.add_argument("--request_timeout", nargs="?", default=5)
parser.add_argument("-ot", "--track_output_path", nargs="?", default="output/tracks/")
parser.add_argument(
    "-op", "--playlist_output_path", nargs="?", default="output/playlists/"
)
parser.add_argument("-oa", "--album_output_path", nargs="?", default="output/albums/")
namespace = parser.parse_args(sys.argv[1:])

files: list[TextIOWrapper] = []

for f in namespace.filenames:
    try:
        files.append(open(f))
    except FileNotFoundError:
        print(f'"{f}" is not a valid file path.')
        exit()

# Get list titles and artists from json data
albums: list[dict[str, str]] = []

for file in files:
    try:
        data: list[dict[str, str]] = json.load(file)  # pyright: ignore[reportAny]
    except json.JSONDecodeError:
        print(f'"{file.name}" is not a valid json file.')
        exit()
    albums += data

# Try to get soulseek credentials from the environment
load_dotenv()
# TODO: Allow user to specify these as arguments
soulseek_username = getenv_safe("SOULSEEK_USERNAME")
soulseek_password = getenv_safe("SOULSEEK_PASSWORD")

# Create default soulseek client settings and configure soulseek credentials
settings: Settings = Settings(
    credentials=CredentialsSettings(
        username=soulseek_username, password=soulseek_password
    )
)

# Create soulseek client
client: SoulSeekClient = SoulSeekClient(settings)

transfer_requests: list[tuple[str, str]] = []


# async block for aioslsk
async def main(
    namespace: Namespace, client: SoulSeekClient, albums: list[dict[str, str]]
):
    await client.start()
    try:
        await client.login()
    except ConnectionReadError:
        print("Unable to connect to SoulSeek.")
        exit()

    client.settings.searches.send.request_timeout = namespace.request_timeout

    # Make search requests for each album
    for album in albums:
        query = f"{album.get('artist')}#{album.get('title')}"  # Insert a pound symbol to be able to split the artist and song title later in an event. SoulSeek discards the pound sign when performing a search afaik. -Helinos
        print(
            f'Performing search request "{album.get("artist")} {album.get("title")}"...'
        )
        _search_request = await client.searches.search(query)
        break  # Do only once for testing TODO: Remove

    # Wait for transfer requests and start transfers
    while True:
        await asyncio.sleep(1)
        if len(transfer_requests) != 0:
            (username, remote_path) = transfer_requests.pop()
            print(f"Beginning transfer of {remote_path} from {username}")
            transfer: Transfer = await client.transfers.download(username, remote_path)
            filename = remote_path.split("\\")[-1]
            transfer.local_path = namespace.track_output_path + filename


# Soulseek events
async def search_result_listener(event: SearchResultEvent):
    None


async def search_request_removed_listener(event: SearchRequestRemovedEvent):
    query = event.query.query
    (artist, song_title) = query.split("#")
    query_tuple = (artist, song_title)
    query_string = f"{artist} {song_title}"

    results = event.query.results

    print(f"{query_string} has finished collecting {len(results)} results.")

    # Sort results by peer's average speed and get the fastest one
    sorted(results, key=lambda result: result.avg_speed)
    result = results[0]

    # Start the download in the background
    for shared_item in result.shared_items:
        transfer_requests.append(
            (result.username, shared_item.filename)
        )  # "filename" is actually the full path to the remote file


# Register events
client.events.register(SearchResultEvent, search_result_listener)
client.events.register(SearchRequestRemovedEvent, search_request_removed_listener)

asyncio.run(main(namespace, client, albums))
