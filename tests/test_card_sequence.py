"""Display order is independent of generator timer phase and replacement rate."""
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from picframe3.app import PicFrame
from picframe3.cardsequence import CardSequence, STATE_KEY
from picframe3.config import Config
from picframe3.library.db import Library
from picframe3.media.loader import SlideLoader
from picframe3.media.prepare import PrepareOptions


@pytest.fixture
async def ordered(tmp_path, monkeypatch):
    cfg = Config()
    cfg.slideshow.card_directory = str(tmp_path)
    cfg.slideshow.card_sequence = ['weather', 'recipe']
    f = PicFrame(cfg)
    f.library = Library(str(tmp_path / 'cards.db3'))
    f.renderer = Mock(in_transition=False)
    f.loader = SlideLoader((80, 50), PrepareOptions())
    f.playlist = Mock()
    f._publish = Mock()
    monkeypatch.setattr('picframe3.slideshow.Texture.from_image', lambda image, **kw: image)
    yield f
    f.loader.close()
    f.library.close()


def request(f, name, duration=None):
    p = Path(f.config.slideshow.card_directory) / f'{name}.png'
    Image.new('RGB', (80, 50), 'green').save(p)
    return dict(path=str(p), type=name, duration=duration or (30 if name == 'weather' else 45))


async def show_boundary(f, photos=8):
    s = f.slideshow
    s.card = None  # End the preceding card, as a normal photo would.
    s.photos_since_card = photos
    return await s._try_card()


async def test_out_of_order_submission_and_replacement_alternate(ordered):
    f, s = ordered, ordered.slideshow
    s.queue_card(request(f, 'recipe'))  # Recipe arrives first.
    s.queue_card(request(f, 'weather'))
    assert await show_boundary(f)
    assert s.card['type'] == 'weather' and s.card['duration'] == 30
    assert s.card_state(time.monotonic())['next_type'] == 'recipe'
    for _ in range(20):
        s.queue_card(request(f, 'weather'))
    assert list(s.cards) == ['recipe', 'weather']
    assert not await show_boundary(f, 7)
    assert await show_boundary(f, 8)
    assert s.card['type'] == 'recipe' and s.card['duration'] == 45
    assert await show_boundary(f)
    assert s.card['type'] == 'weather'
    assert not await show_boundary(f)  # Missing recipe cannot repeat weather.
    f.playlist.next.assert_not_called()
    assert f.library.get_state(STATE_KEY)['next_type'] == 'recipe'


async def test_missing_and_invalid_expected_card_preserve_turn(ordered):
    f, s = ordered, ordered.slideshow
    s.queue_card(request(f, 'weather'))
    assert await show_boundary(f)
    s.queue_card(request(f, 'weather'))
    assert not await show_boundary(f)
    bad = request(f, 'recipe')
    s.queue_card(bad)
    Path(bad['path']).write_bytes(b'broken')
    assert not await show_boundary(f)
    assert s.card_state(time.monotonic())['next_type'] == 'recipe'
    s.queue_card(request(f, 'recipe'))
    assert await show_boundary(f)
    assert s.card['type'] == 'recipe'


async def test_restart_retains_turn_and_receipt(ordered):
    f, s = ordered, ordered.slideshow
    weather = request(f, 'weather')
    s.queue_card(weather)
    assert await show_boundary(f)
    from picframe3.slideshow import SlideshowController
    restarted = SlideshowController(f)
    assert restarted.card_state(time.monotonic())['next_type'] == 'recipe'
    assert restarted.card_state(time.monotonic())['last_shown']['weather'] == weather['path']
    assert restarted.card_state(time.monotonic())['session'] != s.card_state(time.monotonic())['session']
    restarted.queue_card(request(f, 'weather'))
    restarted.photos_since_card = 8
    assert not await restarted._try_card()


async def test_pause_during_decode_does_not_advance(ordered):
    f, s = ordered, ordered.slideshow
    s.queue_card(request(f, 'weather'))
    load = f.loader.load
    async def pause(metas):
        result = await load(metas)
        f.paused = True
        return result
    f.loader.load = pause
    assert await show_boundary(f)
    assert s.card is None
    assert s.card_state(time.monotonic())['next_type'] == 'weather'
    assert list(s.cards) == ['weather']
    assert f.library.get_state(STATE_KEY) is None


async def test_no_adjacent_cards_even_if_config_conflicts(ordered):
    f, s = ordered, ordered.slideshow
    f.config.slideshow.card_consecutive = True
    with pytest.raises(ValueError, match='card_consecutive'):
        s.queue_card(request(f, 'weather'))
    assert s.card_state(time.monotonic())['sequence_error']


async def test_only_configured_types_accepted(ordered):
    with pytest.raises(ValueError, match='not in'):
        ordered.slideshow.queue_card(request(ordered, 'news'))


@pytest.mark.parametrize('sequence', [['weather'], ['weather', 'weather'], ['weather', ''], 'weather', ['weather', 3]])
def test_invalid_sequences(sequence):
    with pytest.raises(ValueError):
        CardSequence(sequence, Mock())


async def test_persistence_failure_stops_cards_not_photos(ordered):
    f, s = ordered, ordered.slideshow
    f.library.set_state = Mock(side_effect=OSError('disk full'))
    s.queue_card(request(f, 'weather'))
    assert await show_boundary(f)
    assert s.card['type'] == 'weather'
    assert s.card_state(time.monotonic())['sequence_error']
    s._advance_locked = AsyncMock()
    await s.advance()
    s._advance_locked.assert_awaited_once()


async def test_corrupt_state_fails_closed(ordered):
    f = ordered
    f.library.set_state(STATE_KEY, {'broken': True})
    assert f.slideshow.card_state(time.monotonic())['sequence_error']
    with pytest.raises(ValueError, match='persisted'):
        f.slideshow.queue_card(request(f, 'weather'))


async def test_cursor_advances_only_after_successful_show(ordered):
    f, s = ordered, ordered.slideshow
    s.queue_card(request(f, 'weather'))
    f.renderer.show.side_effect = ValueError('renderer failed')
    assert not await show_boundary(f)
    assert f.library.get_state(STATE_KEY) is None
    assert s.card_state(time.monotonic())['next_type'] == 'weather'


def test_local_yaml_only():
    from picframe3.uischema import check_path_setting
    assert check_path_setting('slideshow.card_sequence', ['weather', 'recipe'])
