import os.path
import time
import argparse
import urllib.parse
import re

import requests
import schedule
import yt_dlp

parser = argparse.ArgumentParser(prog='vhx-downloader', description='Download videos from Vimeo OTT')
parser.add_argument("--client-id", dest='client_id', help="OAuth2 client ID", required=True)
parser.add_argument("--client-secret", dest='client_secret', help="OAuth2 client secret", required=True)
parser.add_argument("--username", dest='username', help="", required=True)
parser.add_argument("--password", dest='password', help="", required=True)
parser.add_argument("--site-id", dest='site_id', help="Site ID", required=True)
parser.add_argument("--series-id", dest='series', help="Series ID to download", action="append", required=False)
parser.add_argument("--series-slug", dest='slugs', help="Series slug to download (eg. 'game-changer' in 'dropout.tv/game-changer')", action="append", required=False)
parser.add_argument("--dest-dir", dest='dest_dir', help="Destination directory", required=True)
parser.add_argument("--watch", dest='watch', help="Watch for new episodes", action='store_true')
parser.add_argument("--watch-at", dest='watch_at', help="Time when to check for new episodes", default="00:00")
parser.add_argument("--watch-timezone", dest='watch_timezone', help="Timezone the watch at time is in.", default="Etc/GMT")

def fetch_paginated(session, url, key):
    items = []
    params = {'page': '1', 'per_page': '100'}
    while True:
        r = session.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        items.extend(data[key])

        if len(items) >= data['pagination']['count']:
            return items
        
        params['page'] = str(int(params['page']) + 1)

class VhxAuth(requests.auth.AuthBase):
    TOKEN_URL = "https://auth.vhx.com/v1/oauth/token"

    def __init__(self, session: requests.Session, client_id: str, client_secret: str, username: str, password: str) -> None:
        self.session = session
        self.client_id = client_id
        self.client_secret = client_secret
        self.username = username
        self.password = password

        self.token = ""
        self.token_expires = 0

    def __call__(self, request: requests.PreparedRequest) -> requests.PreparedRequest:
        if request.url == self.TOKEN_URL:
            return request
        
        if self.token_expires < time.time():
            r = self.session.post(self.TOKEN_URL, data={
                "username": self.username,
                "password": self.password,
                "grant_type": "password",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            })
            r.raise_for_status()

            token = r.json()

            self.token = token['access_token']
            self.token_expires = time.time() + token['expires_in']

        request.headers['authorization'] = f'Bearer {self.token}'

        return request


def main(args):
    with requests.Session() as session:
        session.auth = VhxAuth(session, args.client_id, args.client_secret, args.username, args.password)

        series = args.series or []

        if args.slugs:
            for slug in args.slugs:
                # Unfortunately using the slug instead of the collection ID doesn't seem to work for collection lookup
                # but for some reason it does work for collections items lookup. Truly a Vimeo OTT moment.
                res = session.get(f'https://api.vhx.tv/collections/{slug}/items', params={'site_id': args.site_id})
                res.raise_for_status()
                self_url = urllib.parse.urlparse(res.json()['_links']['self']['href'])
                if match := re.match(r'^/collections/(\d+)/items$', self_url.path):
                    series.append(match[1])
                else:
                    raise RuntimeError('failed to extract the series ID from {self_url.path!r} for series {slug!r}')

        if not series:
            print("No series selected, stopping.")
            return

        for series_id in series:
            seasons = fetch_paginated(session, f"https://api.vhx.com/v2/sites/{args.site_id}/collections/{series_id}/items", 'items')
            for season in seasons:
                if season['entity_type'] == 'collection':
                    episodes = fetch_paginated(session, f"https://api.vhx.com/v2/sites/{args.site_id}/collections/{season['entity_id']}/items", 'items')
                    for episode in episodes:
                        if episode['entity_type'] == 'video':
                            meta = episode['entity']['metadata']
                            output_file_root = f'S{meta["season"]["number"] or 0:02}E{meta["season"]["episode_number"] or 0:02} - {episode["entity"]["title"]}'.replace('/', '_')
                            path = os.path.join(args.dest_dir, meta["series"]["name"], f'{output_file_root}.mkv')
                            
                            print("Downloading", path)

                            if os.path.exists(path):
                                print(f'{path} already exists, skipping...')
                                continue

                            r = session.get(f"https://api.vhx.com/v2/sites/{args.site_id}/videos/{episode['entity_id']}/delivery", params={"offline_license": "1"})
                            r.raise_for_status()
                            streams = r.json()
                            for stream in streams['streams']:
                                if stream['method'] == 'dash':
                                    options = {
                                        'outtmpl': {
                                            'default': f'{output_file_root}.%(ext)s',
                                        },
                                        'paths': {
                                            'home': os.path.join(args.dest_dir, meta["series"]["name"]),
                                            'temp': '/tmp',
                                        },
                                        'merge_output_format': 'mkv',
                                        'format': 'bestvideo+bestaudio',
                                        'writesubtitles': True,
                                        'postprocessors': [
                                            # --embed-subs
                                            {"key": "FFmpegEmbedSubtitle"},
                                        ],
                                    }
                                    with yt_dlp.YoutubeDL(options) as ytdl:
                                        ytdl.download([stream['url']])
                                    break
                            else:
                                raise RuntimeError("failed to find a DASH stream")

if __name__ == '__main__':
    args = parser.parse_args()

    main(args)

    if args.watch:
        schedule.every().day.at(args.watch_at, args.watch_timezone).do(main, args)

        while True:
            print('Waiting until', schedule.next_run())
            time.sleep(schedule.idle_seconds())
            schedule.run_pending()
