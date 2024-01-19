import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import md5
import json
import logging
import os
import random
import urllib.parse

from bs4 import BeautifulSoup
import httpx


USER_AGENT = 'sdmusiceventsbot@mastodon.social (Music Events Bot) v1.0'


logging.basicConfig(
    format='[%(levelname)s] %(message)s',
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


@dataclass
class Cache:
    posts: [str]

@dataclass
class Settings:
    mastodon_api_token: str
    mastodon_api_url: str
    reader_base_url: str
    reader_event_url_template: str
    n: int = 1
    cache_filename: str = 'cache.json'
    extra_hashtags: str = '#sandiegolivemusic #sandiego #livemusic'


@dataclass
class Event:
    title: str
    url: str
    image_url: str = None
    location: str = None
    date: str = None
    time: str = None
    tags: [str] = list


def get_events_url(settings: Settings):
    today = datetime.now()
    start_date = (today + timedelta(days=1)).strftime('%Y-%m-%d')
    end_date = (today + timedelta(days=5)).strftime('%Y-%m-%d')
    return settings.reader_event_url_template.replace(
        '{start_date}',
        start_date,
    ).replace(
        '{end_date}',
        end_date,
    )


def get_soup(url, settings):
    response = httpx.get(
        url,
        headers={'User-Agent': USER_AGENT}
    )
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def get_events(soup, settings):
    events_blocks = soup.find_all('div', attrs={'class': 'events-date'})
    # There could be multiple events-date blocks (for multiple day searches).
    # We just select a random one for use here.
    events_block = random.choice(events_blocks)

    event_elements = events_block.find_all('div', attrs={'class': 'event-item'})
    date_element = events_block.find('h2')
    date = date_element.get_text().strip()

    def make_event(el, date):
        title_element = el.find('a', attrs={'class': 'event-title'})
        place_element = el.find('a', attrs={'class': 'event-place'})
        time_element = el.find('div', attrs={'class': 'event-time'})

        full_url = urllib.parse.urljoin(
            settings.reader_base_url,
            title_element['href'],
        )
        # Remove unneeded query params
        cleaned_url = urllib.parse.urljoin(
            full_url,
            urllib.parse.urlparse(full_url).path,
        )

        tags = [
            filter_element.get_text().strip().lower()
            for filter_element in el.find_all('a', attrs={'class': 'event-type'})
        ]

        return Event(
            title=title_element.get_text().strip(),
            url=cleaned_url,
            location=place_element.get_text().strip(),
            date=date,
            time=time_element.get_text().strip(),
            tags=[tag for tag in tags if tag != 'music'],
        )

    return [make_event(el, date) for el in event_elements]


def post(event, settings: Settings):
    hashtags = ' '.join([f'#{tag}' for tag in event.tags])
    status = (
        f'{event.title}\n{event.date}, {event.time} @ {event.location}\n'
        f'{hashtags} {settings.extra_hashtags}\n\n{event.url}'
    )

    key = md5(status.encode('utf-8')).hexdigest()

    response = httpx.post(
        settings.mastodon_api_url,
        headers={
            'Authorization': f'Bearer {settings.mastodon_api_token}',
            'Idempotency-Key': key,
            'User-Agent': USER_AGENT,
        },
        data={
            'status': status,
            'language': 'en',
        }
    )
    response.raise_for_status()

def get_cache(settings: Settings):
    try:
        with open(settings.cache_filename) as f:
            return Cache(**json.load(f))
    except FileNotFoundError:
        return Cache(posts=[])


def set_cache(cache: Cache, settings: Settings):
    with open(settings.cache_filename, 'w') as f:
        return json.dump(cache.__dict__, f)


def get_settings():
    return Settings(
        mastodon_api_token=os.environ['BOT_MASTODON_API_TOKEN'],
        mastodon_api_url=os.environ['BOT_MASTODON_API_URL'],
        reader_base_url=os.environ['BOT_READER_BASE_URL'],
        reader_event_url_template=os.environ['BOT_READER_EVENT_URL_TEMPLATE'],
    )


def main():
    logger.debug('Starting up...')
    settings = get_settings()
    cache = get_cache(settings)

    logger.debug('Finding events...')
    url = get_events_url(settings)
    soup = get_soup(url, settings)
    events = get_events(soup, settings)

    logger.info(f'Found {len(events)} total events. Posting: {settings.n}')
    i = 0

    random.shuffle(events)
    for event in events:
        if i >= settings.n:
            break
        elif event.url in cache.posts:
            continue
        else:
            logger.debug(f'Posting {event.title}...')
            i += 1
            post(event, settings)
            cache.posts.append(event.url)
    set_cache(cache, settings)
    logger.info('Done.')
