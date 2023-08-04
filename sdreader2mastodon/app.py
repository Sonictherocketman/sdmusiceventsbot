import argparse
from dataclasses import dataclass
from datetime import datetime
from hashlib import md5
import json
import logging
import os
import random
import urllib.parse

from bs4 import BeautifulSoup
import httpx


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
    categories: [str]
    start_date: str
    end_date: str
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
    today = datetime.now().strftime('%Y-%m-%d')
    return settings.reader_event_url_template.replace(
        '{start_date}',
        today,
    ).replace(
        '{end_date}',
        today,
    )


def get_soup(url, settings):
    response = httpx.get(url)
    response.raise_for_status()
    return BeautifulSoup(response.text, 'html.parser')


def get_events(soup, settings):
    event_elements = soup.find_all('div', attrs={'class': 'event-item'})
    events_block = soup.find('div', attrs={'class': 'events-date'})
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

        return Event(
            title=title_element.get_text().strip(),
            url=cleaned_url,
            location=place_element.get_text().strip(),
            date=date,
            time=time_element.get_text().strip(),
        )

    return [make_event(el, date) for el in event_elements]


def post(event, settings: Settings):
    status = f'{event.title}\n{event.date}, {event.time} @ {event.location}\n{settings.extra_hashtags}\n\n{event.url}'
    key = md5(status.encode('utf-8')).hexdigest()

    response = httpx.post(
        settings.mastodon_api_url,
        headers={
            'Authorization': f'Bearer {settings.mastodon_api_token}',
            'Idempotency-Key': key,
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


def get_settings(args):
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
    for event in random.shuffle(events):
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
