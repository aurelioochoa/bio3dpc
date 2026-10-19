#!/usr/bin/env python3
"""Slice imagegen masters into reusable, transparent native UI assets.

Pillow only crops, fits and packs generated artwork. Interactive labels and
focus/pressed states are composed live; they never replace generated textures.
"""
from pathlib import Path
import json
import math

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'bio3dpc/ui_assets'
OUT = ASSETS / 'controls'

# Crop boundaries are measured against the shipped, inspected 1254px atlas.
REGIONS = {
    'play': (0,140,535,340),
    'secondary': (535,140,970,340),
    'portrait-frame': (970,0,1254,450),
    'landscape-frame': (0,450,455,850),
    'slider-track': (455,600,1020,730),
    'slider-thumb': (1020,450,1254,850),
    'toggle-track': (0,880,590,1254),
    'toggle-knob': (590,860,910,1254),
    'radio-on': (930,880,1254,1254),
}


def build():
    OUT.mkdir(parents=True,exist_ok=True)
    manifest={}
    def save(name,im,origin):
        im.save(OUT/f'{name}.png')
        manifest[name]={'size':list(im.size),'source':origin}
    def isolate(image,box):
        tile=image.crop(box)
        # Generators can leave nearly invisible alpha specks far beyond the
        # object. Measure the actual silhouette, keeping a two-pixel fringe.
        bounds=tile.getchannel('A').point(lambda alpha:255 if alpha>32 else 0).getbbox()
        x1,y1,x2,y2=bounds
        return tile.crop((max(0,x1-2),max(0,y1-2),min(tile.width,x2+2),min(tile.height,y2+2)))
    atlas=Image.open(ASSETS/'bathysphere-controls-source.png').convert('RGBA')
    for name,box in REGIONS.items():
        save(name,isolate(atlas,box),'bathysphere-controls-source.png')
    save('radio-off',Image.open(OUT/'radio-on.png'),'bathysphere-controls-source.png; center covered by live unselected indicator')
    icons=Image.open(ASSETS/'bathysphere-icons-source.png').convert('RGBA')
    mid=icons.width//2
    for name,box in (
        ('icon-settings',(0,0,mid,mid)),('icon-folder',(mid,0,icons.width,mid)),
        ('arrow-left',(0,mid,mid,icons.height)),('icon-mouse',(mid,mid,icons.width,icons.height))):
        save(name,isolate(icons,box),'bathysphere-icons-source.png')
    save('arrow-right',ImageOps.mirror(Image.open(OUT/'arrow-left.png')),'bathysphere-icons-source.png; mirrored arrow')
    logo=Image.open(ASSETS/'bathysphere-logo-source.png').convert('RGBA')
    save('logo',logo.crop(logo.getbbox()),'bathysphere-logo-source.png')
    # These are the actual generated concept previews, not emulator captures.
    concept=Image.open(ROOT/'docs/design-concepts/01-bathysphere.png').convert('RGBA')
    assert concept.size==(1536,1024)
    save('preview-portrait',concept.crop((1034,326,1157,500)),'docs/design-concepts/01-bathysphere.png')
    save('preview-landscape',concept.crop((1210,325,1478,501)),'docs/design-concepts/01-bathysphere.png')
    # Alpha masks are motion effects, not replacement UI art. Precomputing them
    # avoids per-frame blur work while the generated lamp artwork stays intact.
    for level in range(16):
        strength=level/15
        im=Image.new('RGBA',(144,208))
        pixels=[]
        for y in range(im.height):
            for x in range(im.width):
                dx,dy=x/2-36,y/2-52
                if strength<.72:
                    alpha=int(210*(1-strength/.72)*math.exp(-.5*((dx/6)**2+(dy/15)**2)))
                    pixels.append((10,20,15,alpha))
                else:
                    alpha=int(82*(strength-.72)/.28*math.exp(-.5*((dx/17)**2+(dy/24)**2)))
                    pixels.append((255,173,56,alpha))
        im.putdata(pixels)
        save(f'lamp-{level:02}',im,'procedural alpha lighting mask')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('LAUNCHER ASSETS BUILT',len(manifest))


if __name__=='__main__':
    build()
