import base64
import copy
from io import BytesIO

from PIL import Image
import pytest

from app import create_app
from config import Config
from provider import features, genai
from tests.test_endurance_error_unit import Token, request_body
from tools.prompts import normalize_tool_history


def image_url(mode, color, format='WEBP', **metadata):
    with Image.new(mode, (12, 9), color) as image, BytesIO() as output:
        image.save(output, format=format, **({'lossless': True} if format == 'WEBP' else {}), **metadata)
        return 'data:image/' + format.lower() + ';base64,' + base64.b64encode(output.getvalue()).decode()


def pixels(url):
    with Image.open(BytesIO(base64.b64decode(url.partition(',')[2], validate=True))) as image:
        image.load()
        return image.size, image.mode, image.tobytes()


@pytest.mark.parametrize('mode,color', [('RGB', (200, 30, 40)), ('RGBA', (200, 30, 40, 0)), ('RGBA', (200, 30, 40, 71)), ('RGBA', (200, 30, 40, 255))])
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
    prepared, options = features.prepare_request(normalize_tool_history(history), 'Kimi-k3', Config(Token(), 1, None, False), {}, 'chat')
    historical = prepared[1]['content'][1]['image_url']['url']
    assert historical.startswith('data:image/png;base64,') and pixels(historical) == pixels(earlier)
    assert 'read-A' in prepared[1]['content'][0]['text']
    assert prepared[-1]['content'] == 'Continue without another image'
    assert options['imageUrls'] == []


@pytest.mark.parametrize('format,mode,color', [('PNG', 'RGBA', (1, 2, 3, 4)), ('JPEG', 'RGB', (1, 2, 3))])
def test_non_webp_is_not_reencoded(format, mode, color):
    original = image_url(mode, color, format=format)
    assert features._compatible_image_url(original) == original


@pytest.mark.parametrize('url', ['https://example.test/image.png', 'https://example.test/image.webp?sig=abc;part=2', 'https://example.test/signed?image=anything'])
def test_remote_image_url_is_not_rewritten(url):
    assert features._compatible_image_url(url) == url


def test_webp_must_be_valid_decodable_webp():
    for data in ['not-base64!', base64.b64encode(b'not an image').decode()]:
        with pytest.raises(ValueError):
            features._compatible_image_url('data:image/webp;base64,' + data)
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


def test_webp_icc_and_exif_orientation_survive_png_conversion():
    exif = Image.Exif()
    exif[274] = 6
    profile = b'controlled-icc-profile'
    original = image_url('RGBA', (20, 40, 60, 80), icc_profile=profile, exif=exif.tobytes())
    converted = features._compatible_image_url(original)
    with Image.open(BytesIO(base64.b64decode(converted.partition(',')[2]))) as image:
        assert image.info['icc_profile'] == profile
        assert image.getexif()[274] == 6
    assert pixels(converted) == pixels(original)


@pytest.mark.parametrize('mode,lossless,metadata', [('RGB', False, {}), ('RGB', True, {}), ('RGBA', True, {'icc_profile': b'controlled'})])
def test_webp_pixel_limit_accepts_boundary_and_rejects_above(monkeypatch, mode, lossless, metadata):
    with Image.new(mode, (12, 9), 'red') as image, BytesIO() as output:
        image.save(output, format='WEBP', lossless=lossless, **metadata)
        original = 'data:image/webp;base64,' + base64.b64encode(output.getvalue()).decode()
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 108)
    assert pixels(features._compatible_image_url(original)) == pixels(original)
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 107)
    with pytest.raises(ValueError):
        features._compatible_image_url(original)


@pytest.mark.parametrize('protocol', ['chat', 'messages', 'responses'])
@pytest.mark.parametrize('stream', [False, True])
def test_invalid_webp_returns_bad_request_before_sse(monkeypatch, protocol, stream):
    def unexpected_generation(*args, **kwargs):
        raise AssertionError('Invalid image reached generation')
    monkeypatch.setattr(genai.requests, 'post', unexpected_generation)
    monkeypatch.setattr(genai.model_registry, 'get_root_ai_type', lambda *args: 'xinference')
    path, body = request_body(protocol, stream)
    if protocol == 'chat':
        content = [{'type': 'image_url', 'image_url': {'url': 'data:image/webp;base64,not-base64!'}}]
        body['messages'] = [{'role': 'user', 'content': content}]
    elif protocol == 'messages':
        content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/webp', 'data': 'not-base64!'}}]
        body['messages'] = [{'role': 'user', 'content': content}]
    else:
        body['input'] = [{'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'data:image/webp;base64,not-base64!'}]}]
    response = create_app(Config(Token(), 1, None, False)).test_client().post(path, json=body)
    assert response.status_code == 400
    assert response.is_json


@pytest.mark.parametrize('protocol', ['chat', 'messages', 'responses'])
@pytest.mark.parametrize('stream', [False, True])
def test_current_multi_image_context_is_complete_and_not_duplicated_on_repair(monkeypatch, protocol, stream):
    from tests.test_tool_repair_unit import PARAMETERS, action, calls_from, generation, patch_generations

    earlier = image_url('RGB', (220, 20, 20))
    current = [image_url('RGB', (120, 0, 120)), image_url('RGB', (0, 0, 220))]
    def image_part(url):
        if protocol == 'messages':
            return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/webp', 'data': url.partition(',')[2]}}
        if protocol == 'responses':
            return {'type': 'input_image', 'image_url': url}
        return {'type': 'image_url', 'image_url': {'url': url}}
    text_type = 'input_text' if protocol == 'responses' else 'text'
    messages = [
        {'role': 'user', 'content': [{'type': text_type, 'text': 'Earlier picture'}, image_part(earlier)]},
        {'role': 'assistant', 'content': 'Earlier picture seen'},
        {'role': 'user', 'content': [{'type': text_type, 'text': 'Inspect the two new pictures, then write the file.'},
                                     *[image_part(url) for url in current]]},
    ]
    path, body = request_body(protocol, stream)
    body['input' if protocol == 'responses' else 'messages'] = messages
    requests_seen, _ = patch_generations(monkeypatch, [
        generation(content='Investigating. @@ACTION@@{"calls":invalid}@@END_ACTION@@'),
        generation(content=action(PARAMETERS)),
    ])
    response = create_app(Config(Token(), 1, None, False)).test_client().post(path, json=body)
    assert calls_from(response, protocol, stream) == [PARAMETERS]
    assert len(requests_seen) == 2
    expected = [pixels(url) for url in [earlier, *current]]
    for request in requests_seen:
        carried = [part['image_url']['url'] for message in request['messages']
                   if isinstance(message.get('content'), list) for part in message['content']
                   if part.get('type') == 'image_url']
        assert [pixels(url) for url in carried] == expected
    assert requests_seen[0]['messages'] == requests_seen[1]['messages']
