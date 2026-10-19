#!/usr/bin/env python3
"""Render a seamless twelve-second Bathysphere artwork loop using FFmpeg.

The generated illustration is kept intact. Bicubic sampling adds water
refraction; video layers add drifting particles and instrument needles. Lamp
flicker runs independently in the launcher. No runtime network is needed.
"""
from pathlib import Path
import math
import random
import subprocess

from PIL import Image, ImageEnhance

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'bio3dpc/ui_assets'
WIDTH, HEIGHT, FPS, SECONDS = 1152, 768, 60, 12


def clock(seconds):
    total = round(seconds * 100)
    return f'{total // 360000}:{total // 6000 % 60:02}:{total // 100 % 60:02}.{total % 100:02}'


def circle(x, y, radius):
    # ASS vector ellipse, independent of raster artwork.
    r = radius
    return (f'm {x-r:.2f} {y:.2f} '
            f'b {x-r:.2f} {y-1.33*r:.2f} {x+r:.2f} {y-1.33*r:.2f} {x+r:.2f} {y:.2f} '
            f'b {x+r:.2f} {y+1.33*r:.2f} {x-r:.2f} {y+1.33*r:.2f} {x-r:.2f} {y:.2f}')


def render():
    ASSETS.mkdir(parents=True, exist_ok=True)
    source = ASSETS / 'bathysphere-source.png'
    output = ASSETS / 'bathysphere-loop.mp4'
    rendering = ASSETS / 'bathysphere-loop.render.mp4'
    overlay = ASSETS / 'bathysphere-motion.ass'
    lines = [f'''[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Motion,DejaVu Sans,12,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text''']
    rng = random.Random(83507705)
    particles = [(rng.uniform(70, 670), rng.random(), rng.uniform(.7, 1.7), rng.uniform(0, 2*math.pi))
                 for _ in range(24)]

    def event(frame, vector, color, alpha=0, blur=0, layer=0):
        tags = f'\\an7\\pos(0,0)\\bord0\\shad0\\p1\\1c&H{color}&\\alpha&H{alpha:02X}&\\blur{blur}'
        lines.append(f'Dialogue: {layer},{clock(frame/FPS)},{clock((frame+1)/FPS)},Motion,,0,0,0,,{{{tags}}}{vector}')

    for frame in range(FPS * SECONDS):
        phase = 2*math.pi*frame/(FPS*SECONDS)
        for x, start, radius, drift in particles:
            progress = (start + frame/(FPS*SECONDS)) % 1
            y = 480 - 435*progress
            opacity = math.sin(math.pi*progress)**2
            event(frame, circle(x+5*math.sin(phase+drift), y, radius), 'C9E9CB',
                  int(255-65*opacity), .6)
        for x, y, radius, angle, sweep in ((103, 608, 45, 2.65, .07),
                                           (622, 612, 43, -1.6, .18)):
            theta = angle + sweep*math.sin(phase)
            tipx, tipy = x+radius*math.cos(theta), y+radius*math.sin(theta)
            event(frame, f'm {x-1:.2f} {y:.2f} l {tipx:.2f} {tipy:.2f} l {x+1:.2f} {y+1:.2f}',
                  '77B6D7', 30, .3, 2)
            event(frame, circle(x, y, 2.2), '6C9BB4', 0, .2, 2)
    overlay.write_text('\n'.join(lines)+'\n', encoding='utf-8')
    # Sample every frame from an immutable source. This keeps interpolation
    # independent of encoder/filter buffering and makes the seam deterministic.
    base=Image.open(source).convert('RGB').resize((WIDTH,HEIGHT),Image.Resampling.LANCZOS)
    water_source=base.crop((36,30,696,455))
    mask=Image.new('L',water_source.size)
    mask.putdata([round(255*max(0,min(1,x/25,(659-x)/25,y/25,(424-y)/25)))
                  for y in range(425) for x in range(660)])
    cells=[(x,y,min(x+66,660),min(y+53,425)) for y in range(0,425,53) for x in range(0,660,66)]
    encoder=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y',
        '-f','rawvideo','-pixel_format','rgb24','-video_size',f'{WIDTH}x{HEIGHT}',
        '-framerate',str(FPS),'-i','pipe:0','-vf',f"ass=filename='{overlay}',format=yuv420p",
        '-an','-c:v','libx264','-threads','2','-preset','medium','-crf','20',
        '-movflags','+faststart',str(rendering)],stdin=subprocess.PIPE)
    try:
        for frame in range(FPS*SECONDS):
            phase=2*math.pi*frame/(FPS*SECONDS)
            def point(x,y):
                return x+1.6*math.sin(phase+y/95),y+1.2*math.sin(phase+x/130)
            mesh=[(box,(*point(box[0],box[1]),*point(box[0],box[3]),
                        *point(box[2],box[3]),*point(box[2],box[1]))) for box in cells]
            water=water_source.transform(water_source.size,Image.Transform.MESH,mesh,
                                         resample=Image.Resampling.BICUBIC)
            water=ImageEnhance.Brightness(water).enhance(1+.008*math.sin(phase))
            image=base.copy()
            image.paste(water,(36,30),mask)
            encoder.stdin.write(image.tobytes())
            if (frame+1)%(FPS*3)==0:
                print(f'Water motion rendered: {frame+1}/{FPS*SECONDS}',flush=True)
        encoder.stdin.close()
        if encoder.wait()!=0:
            raise RuntimeError('Launcher video encoder failed')
    finally:
        if not encoder.stdin.closed:
            encoder.stdin.close()
        if encoder.poll() is None:
            encoder.terminate()
            encoder.wait(timeout=5)
    rendering.replace(output)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(output),
                    '-frames:v', '1', str(ASSETS/'bathysphere-still.png')], check=True)
    print('BATHYSPHERE VIDEO RENDERED', output)


if __name__ == '__main__':
    render()
