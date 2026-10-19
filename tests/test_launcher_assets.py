"""Generated assets are complete, transparent and retain source provenance."""
import json
from PIL import Image

from bio3dpc.bathysphere import ASSETS


def test_generated_control_skin_is_complete_and_transparent():
    folder=ASSETS/'controls'
    manifest=json.loads((folder/'manifest.json').read_text())
    required={'logo','play','secondary','portrait-frame','landscape-frame',
              'slider-track','slider-thumb','toggle-track','toggle-knob','radio-on',
              'radio-off','icon-settings','icon-folder','arrow-left','arrow-right',
              'icon-mouse','preview-portrait','preview-landscape'}
    assert required <= manifest.keys()
    for name in required:
        entry=manifest[name]
        image=Image.open(folder/f'{name}.png')
        assert list(image.size)==entry['size']
        assert min(image.size)>=40
        assert image.mode=='RGBA'
        if name not in ('preview-portrait','preview-landscape'):
            assert image.getchannel('A').getextrema()==(0,255)
        assert 'source' in entry
    assert 'logo-source' in manifest['logo']['source']
    assert 'controls-source' in manifest['play']['source']


def test_frame_holes_are_transparent_and_previews_have_native_aspect():
    for name in ('portrait-frame','landscape-frame'):
        image=Image.open(ASSETS/'controls'/f'{name}.png')
        assert image.getpixel((image.width//2,image.height//2))[3]==0
    portrait=Image.open(ASSETS/'controls/preview-portrait.png')
    landscape=Image.open(ASSETS/'controls/preview-landscape.png')
    assert portrait.width<portrait.height
    assert landscape.width>landscape.height
