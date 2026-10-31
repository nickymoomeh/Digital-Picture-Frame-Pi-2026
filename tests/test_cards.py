"""Cards use the normal loader/renderer without consuming the photo playlist."""
import time
from unittest.mock import Mock

import pytest
from PIL import Image

from picframe3.app import PicFrame
from picframe3.config import Config
from picframe3.events import Action, Command
from picframe3.media.loader import SlideLoader
from picframe3.media.prepare import PrepareOptions


@pytest.fixture
async def frame(tmp_path, monkeypatch):
    cfg = Config()
    cfg.slideshow.card_directory = str(tmp_path)
    cfg.slideshow.card_every = 2
    f = PicFrame(cfg)
    f.renderer = Mock(in_transition=False)
    f.loader = SlideLoader((80, 50), PrepareOptions())
    f.playlist = Mock()
    f.library = Mock()
    f._publish = Mock()
    monkeypatch.setattr('picframe3.slideshow.Texture.from_image', lambda image, **kw: image)
    yield f
    f.loader.close()


def card(frame, name='weather', color='red', duration=30):
    from pathlib import Path
    path = Path(frame.config.slideshow.card_directory) / (name + '.png')
    Image.new('RGB', (80, 50), color).save(path)
    return {'path': str(path), 'duration': duration}


async def test_order_replacement_fairness_and_playlist(frame):
    s = frame.slideshow
    weather = card(frame)
    news = card(frame, 'news')
    s.queue_card(weather)
    s.queue_card(news)
    s.queue_card(dict(weather, duration=42))
    assert list(s.cards) == ['weather', 'news']
    assert not await s._try_card()  # two photos first
    s.photos_since_card = 2
    await s.advance()
    assert s.card['type'] == 'weather'
    assert s.card['duration'] == 42
    assert s.current == []
    assert frame.renderer.show.call_args.kwargs == {'transition': 'fade', 'duration': 2.5}
    assert 44 <= s.next_change_in(time.monotonic()) <= 44.5
    frame.playlist.next.assert_not_called()
    frame.playlist.jump_to.assert_not_called()
    frame.library.mark_played.assert_not_called()
    assert not await s._try_card()  # no adjacent cards
    frame.config.slideshow.card_consecutive = True
    assert await s._try_card()
    assert s.card['type'] == 'news'


async def test_resume_returns_to_normal_advance(frame):
    s = frame.slideshow
    s.queue_card(card(frame))
    s.photos_since_card = 2
    assert await s._try_card()
    from unittest.mock import AsyncMock
    s._advance_locked = AsyncMock()
    await s.advance()
    s._advance_locked.assert_awaited_once_with(initial=False, backwards=False)


async def test_latest_bytes_and_invalid_pending_files(frame):
    s = frame.slideshow
    payload = card(frame)
    s.queue_card(payload)
    card(frame, color='blue')
    s.photos_since_card = 2
    assert await s._try_card()
    slide = frame.renderer.show.call_args.args[0]
    assert slide.texture.getpixel((40, 25)) == (0, 0, 255)
    s.card = None
    s.photos_since_card = 2
    s.queue_card(card(frame, 'broken'))
    from pathlib import Path
    Path(s.cards['broken']['path']).write_bytes(b'not an image')
    s.queue_card(card(frame, 'missing'))
    Path(s.cards['missing']['path']).unlink()
    s.queue_card(card(frame, 'news'))
    assert await s._try_card()
    assert s.card['type'] == 'news'
    assert not s.cards


@pytest.mark.parametrize('duration', [0, -1, 3601, float('nan'), float('inf'), True, '30'])
def test_duration_validation(frame, duration):
    with pytest.raises(ValueError):
        frame.slideshow.queue_card(card(frame, duration=duration))


def test_path_and_configuration_validation(frame, tmp_path):
    s = frame.slideshow
    p = card(frame)
    with pytest.raises(ValueError):
        s.queue_card(dict(p, path='weather.png'))
    outside = tmp_path.parent / 'outside.png'
    Image.new('RGB', (2, 2)).save(outside)
    link = tmp_path / 'link.png'
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        s.queue_card(dict(p, path=str(link)))
    frame.config.slideshow.card_directory = ''
    with pytest.raises(ValueError):
        s.queue_card(p)
    from picframe3.uischema import check_path_setting
    assert check_path_setting('slideshow.card_directory', '/etc')


async def test_pause_and_display_hold_remaining_time(frame, monkeypatch):
    now = [100.0]
    monkeypatch.setattr('picframe3.slideshow.time.monotonic', lambda: now[0])
    s = frame.slideshow
    s.queue_card(card(frame))
    s.photos_since_card = 2
    frame.paused = True
    assert not await s._try_card()
    frame.paused = False
    frame.display_on = False
    assert not await s._try_card()
    frame.display_on = True
    assert await s._try_card()
    now[0] = 110
    await frame.handle(Command(Action.PAUSE))
    remaining = s.next_change_in(now[0])
    now[0] = 200
    assert s.next_change_in(now[0]) == remaining
    frame.set_display(False)
    await frame.handle(Command(Action.RESUME))
    now[0] = 300
    assert s.next_change_in(now[0]) == remaining
    frame.set_display(True)
    assert s.next_change_in(now[0]) == remaining
    now[0] += 5
    assert s.next_change_in(now[0]) == remaining - 5


def test_command_parsing():
    assert Command.parse({'action': 'queue_card', 'path': '/tmp/test.png', 'duration': 30}).action is Action.QUEUE_CARD


async def test_real_shuffle_progression_and_play_counts(frame, library):
    from picframe3.library.playlist import Playlist
    frame.library = library
    frame.playlist = Playlist(library, order='shuffle')
    frame.on_slide_shown = Mock()
    s = frame.slideshow
    await s.advance(initial=True)
    first = s.current[0].id
    await s.advance()
    assert s.current[0].id != first
    expected = [r.id for r in frame.playlist.peek()]
    position = frame.playlist.position
    order = list(frame.playlist._ids)
    counts = {i: library.get(i).play_count for i in order}
    s.queue_card(card(frame))
    await s.advance()
    assert s.card is not None
    assert frame.playlist.position == position
    assert frame.playlist._ids == order
    assert {i: library.get(i).play_count for i in order} == counts
    await s.advance()
    assert s.card is None
    assert [r.id for r in s.current] == expected
    assert library.get(expected[0]).play_count == counts[expected[0]] + 1


async def test_http_validation_and_additive_state(frame):
    from fastapi.testclient import TestClient

    from picframe3.control.http import HttpServer
    from picframe3.events import State

    frame.state = lambda: State(card=frame.slideshow.card_state(time.monotonic()))
    client = TestClient(HttpServer(frame, frame.config.http).api)
    payload = card(frame)
    assert client.post('/api/command', json={'action': 'queue_card', **payload}).status_code == 200
    command = frame.bus.drain()[0]
    await frame.handle(command)
    assert client.get('/api/state').json()['card']['pending'] == 1
    assert client.post('/api/command', json={'action': 'queue_card', **payload, 'duration': -1}).status_code == 400
    assert client.patch('/api/config', json={'slideshow.card_directory': '/etc'}).status_code == 400


async def test_pause_during_card_decode_defers_without_consuming_photo(frame):
    s = frame.slideshow
    s.queue_card(card(frame))
    s.photos_since_card = 2
    load = frame.loader.load

    async def pause_during_load(metas):
        result = await load(metas)
        frame.paused = True
        return result

    frame.loader.load = pause_during_load
    await s.advance()
    assert s.card is None
    assert list(s.cards) == ['weather']
    frame.playlist.next.assert_not_called()
