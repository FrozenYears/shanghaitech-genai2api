import base64
import copy
from io import BytesIO

from PIL import Image
import pytest

from config import Config
from provider import features
from tests.test_endurance_error_unit import Token
from tools.prompts import normalize_tool_history


def image_url(mode, color, format='WEBP'):
    with Image.new(mode, (12, 9), color) as image, BytesIO() as output:
        image.save(output, format=format, **({'lossless': True} if format == 'WEBP' else {}))
        return 'data:image/' + format.lower() + ';base64,' + base64.b64encode(output.getvalue()).decode()


def pixels(url):
    with Image.open(BytesIO(base64.b64decode(url.partition(',')[2], validate=True))) as image:
        image.load()
        return image.size, image.mode, image.tobytes()


@pytest.mark.parametrize('mode,color', [('RGB', (200, 30, 40)), ('RGBA', (200, 30, 40, 71))])
def test_inline_webp_preserves_pixels_alpha_and_turns_for_current_and_historical_images(mode, color):
    earlier = image_url(mode, color)
    current = image_url('RGB', (20, 40, 210))
    messages = [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'First image'}, {'type': 'image_url', 'image_url': {'url': earlier, 'detail': 'low'}}]},
        {'role': 'assistant', 'content': 'First image seen'},
        {'role': 'user', 'content': [{'type': 'text', 'text': 'Second image'}, {'type': 'image_url', 'image_url': {'url': current}}]},
    ]
    original = copy.deepcopy(messages)
    prepared, options = features.prepare_request(messages, 'Kimi-k3', Config(Token(), 1, None, False), {}, 'chat')
    historical = prepared[0]['content'][1]['image_url']
    assert historical['url'].startswith('data:image/png;base64,')
    assert pixels(historical['url']) == pixels(earlier)
    assert historical['detail'] == 'low'
    assert prepared[0]['content'][0] == {'type': 'text', 'text': 'First image'}
    assert [m['role'] for m in prepared] == ['user', 'assistant', 'user']
    assert len(options['imageUrls']) == 1 and options['imageUrl'] == options['imageUrls'][0]
    assert options['imageUrl'].startswith('data:image/png;base64,')
    assert pixels(options['imageUrl']) == pixels(current)
    assert pixels(historical['url']) != pixels(current)
    assert messages == original


def test_tool_result_webp_history_survives_text_only_continue():
    earlier = image_url('RGBA', (80, 90, 100, 120))
    history = [
        {'role': 'assistant', 'tool_calls': [{'id': 'read-A', 'type': 'function', 'function': {'name': 'read', 'arguments': '{"path":"a.webp"}'}}]},
        {'role': 'tool', 'tool_call_id': 'read-A', 'content': [{'type': 'image_url', 'image_url': {'url': earlier}}]},
        {'role': 'user', 'content': 'Continue without another image'},
    ]
    normalized = normalize_tool_history(history)
    prepared, options = features.prepare_request(normalized, 'Kimi-k3', Config(Token(), 1, None, False), {}, 'chat')
    historical = prepared[1]['content'][1]['image_url']['url']
    assert historical.startswith('data:image/png;base64,') and pixels(historical) == pixels(earlier)
    assert 'read-A' in prepared[1]['content'][0]['text']
    assert prepared[-1]['content'] == 'Continue without another image'
    assert options['imageUrls'] == []


def test_non_webp_history_is_not_reencoded():
    original = image_url('RGBA', (1, 2, 3, 4), format='PNG')
    assert features._compatible_image_url(original) == original
    assert features._compatible_image_url('https://example.test/image.png') == 'https://example.test/image.png'


def test_historical_webp_must_be_valid_decodable_webp():
    with pytest.raises(ValueError):
        features._compatible_image_url('data:image/webp;base64,not-base64!')
    with pytest.raises(ValueError):
        features._compatible_image_url('data:image/webp;base64,' + base64.b64encode(b'not an image').decode())
    png = image_url('RGB', (1, 2, 3), format='PNG')
    with pytest.raises(ValueError):
        features._compatible_image_url(png.replace('image/png', 'image/webp'))


def test_animated_webp_is_not_silently_replaced_with_one_frame():
    with Image.new('RGB', (12, 9), 'red') as first, Image.new('RGB', (12, 9), 'blue') as second, BytesIO() as output:
        first.save(output, format='WEBP', save_all=True, append_images=[second], duration=50, loop=0, lossless=True)
        url = 'data:image/webp;base64,' + base64.b64encode(output.getvalue()).decode()
    with pytest.raises(ValueError):
        features._compatible_image_url(url)


def test_converted_png_obeys_the_same_strict_size_limit(monkeypatch):
    original = image_url('RGB', (1, 2, 3))
    converted = features._compatible_image_url(original)
    png_size = len(base64.b64decode(converted.partition(',')[2]))
    assert len(base64.b64decode(original.partition(',')[2])) < png_size
    monkeypatch.setattr(features, 'IMAGE_LIMIT', png_size)
    with pytest.raises(ValueError):
        features._compatible_image_url(original)
