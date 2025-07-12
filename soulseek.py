from argparse import Namespace


import argparse
import logging
from typing import Any
from aioslsk.transfer.model import Transfer
import asyncio
from io import TextIOWrapper
import os
import sys
import json
from aioslsk.client import SoulSeekClient
from aioslsk.events import SearchRequest
from aioslsk.exceptions import ConnectionReadError
from aioslsk.settings import Settings, CredentialsSettings
from dotenv import load_dotenv


def getenv_safe(key: str) -> str:
    var = os.getenv(key)

    if var is None:
        print(f"Unable to find {key} in the environment")
        exit()

    return var


def jsdict_get_safe(dict: dict[Any, Any], key: str) -> Any:
    value = dict.get(key)

    if value is None:
        print(f'Input json file is malformed. Unrecognized key: "{key}"')
        exit()

    return value


class Track:
    def __init__(self, spotify_id, track_info) -> None:
        self.spotify_id: str = spotify_id
        self.name: str = jsdict_get_safe(track_info, "name")
        self.artist: str = jsdict_get_safe(track_info, "artist")
        self.album_name: str = jsdict_get_safe(track_info, "album")
        self.sources: TrackSources = TrackSources(track_info)


class TrackHandler:
    def __init__(self, track: Track, search_request: SearchRequest):
        self.track = track
        self.search_request = search_request

    @classmethod
    async def create(cls, spotify_id, track_info):
        track = Track(spotify_id, track_info)

        query = f"{track.name} {track.album_name} {track.artist}"
        print(f'Performing search request "{query}"...')
        search_request = await client.searches.search(query)

        return cls(track, search_request)

    async def download(self, client: SoulSeekClient) -> None:
        if self.search_request is None:
            return

        sorted(self.search_request.results, key=lambda result: result.avg_speed)
        result = self.search_request.results[0]
        username = result.username
        remote_path = result.shared_items[0].filename

        filename = remote_path.split("\\")[-1]
        extension = filename.split(".")[-1]
        print(f"Beginning transfer of {filename} from {username}")
        transfer: Transfer = await client.transfers.download(username, remote_path)
        transfer.local_path = (
            f"{namespace.output_path}{self.track.spotify_id}.{extension}"
        )

        while not transfer.is_transfered():
            await asyncio.sleep(1)

        # Remove search request from client
        client.searches.remove_request(self.search_request)
        self.search_request = None


class TrackSources:
    def __init__(self, track) -> None:
        sources: list[dict[str, str]] = jsdict_get_safe(track, "sources")
        self.album_title: str | None = None
        self.playlist_name: str | None = None

        for source in sources:
            if source["type"] == "album":
                self.album_title = jsdict_get_safe(source, "album_title")
            if source["type"] == "playlist":
                self.playlist_name = jsdict_get_safe(source, "playlist_name")

    def is_from_album(self) -> bool:
        return self.album_title is not None

    def is_from_playlist(self) -> bool:
        return self.playlist_name is not None


# Initialize argument parser
parser = argparse.ArgumentParser()

parser.add_argument("filenames", nargs="+")
parser.add_argument("-t", "--request_timeout", nargs="?", default=5)
parser.add_argument("-o", "--output_path", nargs="?", default="output/")
namespace = parser.parse_args(sys.argv[1:])

files: list[TextIOWrapper] = []

for f in namespace.filenames:
    try:
        files.append(open(f))
    except FileNotFoundError:
        print(f'"{f}" is not a valid file path.')
        exit()

# Get list titles and artists from json data
tracks = dict()

for file in files:
    try:
        data: dict = json.load(file)
    except json.JSONDecodeError:
        print(f'"{file.name}" is not a valid json file.')
        exit()
    for track_id, track_info in data.items():
        tracks[track_id] = track_info

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


# async block because aioslsk is async
async def main(
    namespace: Namespace,
    client: SoulSeekClient,
    tracks: dict[str, Any],
):
    await client.start()
    try:
        await client.login()
    except ConnectionReadError:
        print("Unable to connect to SoulSeek.")
        exit()

    # client.settings.searches.send.request_timeout = namespace.request_timeout

    def filter(logger, startswith: str):
        class SpamFilter(logging.Filter):
            def filter(self, record):
                return not record.getMessage().startswith(startswith)

        logger = logging.getLogger(logger)
        logger.addFilter(SpamFilter())

    filter("aioslsk.network.network", "failed to fulfill ConnectToPeer request")
    filter(
        "aioslsk.distributed",
        "connection was not registered with the distributed network",
    )

    track_handlers: list[TrackHandler] = []

    async def search():
        for spotify_id, track_info in tracks.items():
            track_handler = await TrackHandler.create(spotify_id, track_info)
            await asyncio.sleep(namespace.request_timeout)
            track_handlers.append(track_handler)

    asyncio.create_task(search())

    # Prevent app from closing before downloads are finished.
    while True:
        for track_handler in track_handlers:
            await track_handler.download(client)

        # TODO: Test if all downloads are finished then quit
        await asyncio.sleep(1)


asyncio.run(main(namespace, client, tracks))
