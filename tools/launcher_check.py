#!/usr/bin/env python3
"""Exercise the real responsive launcher and retain reviewable screenshots."""
from pathlib import Path
import argparse
import json
import os
import sys
import tempfile
import time
import subprocess
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record',action='store_true',help='Record a twelve-second live UI preview')
    parser.add_argument('--display',default=':96',help='Dedicated test display (default: :96)')
    args=parser.parse_args()
    os.environ['DISPLAY']=args.display
    import tkinter as tk
    from PIL import ImageGrab
    from bio3dpc.launcher import Launcher, Preferences
    with tempfile.TemporaryDirectory(prefix='bio3dpc-launcher-') as temporary:
        os.environ['XDG_CONFIG_HOME']=temporary+'/config'
        os.environ['XDG_STATE_HOME']=temporary+'/state'
        window=tk.Tk()
        app=Launcher(window,profile='vx8350')
        view=app.view
        errors=[]
        window.report_callback_exception=lambda *args:errors.append(str(args))
        reports={}
        def pump(seconds=.15):
            deadline=time.monotonic()+seconds
            while time.monotonic()<deadline:
                window.update()
                time.sleep(.002)
            assert not errors,errors
        def point(x,y):
            px,py=view.xy(x,y)
            return round(px),round(py-view.canvas.canvasy(0))
        def click(control,x=None,y=None):
            rx,ry,rw,rh=control.rect
            px,py=point(rx+rw/2 if x is None else x,ry+rh/2 if y is None else y)
            view.canvas.event_generate('<Motion>',x=px,y=py)
            view.canvas.event_generate('<ButtonPress-1>',x=px,y=py)
            view.canvas.event_generate('<ButtonRelease-1>',x=px,y=py)
            pump(.025)
        def key(name):
            view.canvas.focus_force()
            view.canvas.event_generate('<KeyPress>',keysym=name)
            window.update()
        def capture(name,target=None):
            target=target or window
            window.update()
            x,y=target.winfo_rootx(),target.winfo_rooty()
            destination=ROOT/'docs'/name
            try:
                ImageGrab.grab(bbox=(x,y,x+target.winfo_width(),y+target.winfo_height()),xdisplay=os.environ['DISPLAY']).save(destination)
            except OSError:
                # Rootless Xwayland cannot capture its root window; capture only
                # this Tk window through ImageMagick when available instead.
                subprocess.run(['magick','import','-display',args.display,'-window',
                                str(target.winfo_id()),str(destination)],check=True)
        try:
            pump(1.5)
            assert view.decoder.process is not None
            assert view.frames_drawn>15
            start=view.frames_drawn
            begin=time.monotonic()
            pump(1.5)
            fps=(view.frames_drawn-start)/(time.monotonic()-begin)
            assert fps>40,f'Playback too slow: {fps:.1f} fps'
            reports['displayed_fps']=round(fps,1)
            reports['decoded_queue_limit']=view.decoder.frames.maxsize
            click(view.editions[1]); assert app.selected.id=='n7705'
            click(view.editions[0]); assert app.selected.id=='vx8350'
            app.scale.set(3)
            click(view.scale_slider,x=44+190-7,y=560); assert app.scale.get()==4
            key('Home'); assert app.scale.get()==1
            key('End'); assert app.scale.get()==6
            key('Right'); assert app.scale.get()==6
            key('Left'); assert app.scale.get()==5
            px,py=point(120,560)
            view.canvas.event_generate('<ButtonPress-1>',x=px,y=py)
            view.canvas.event_generate('<B1-Motion>',x=0,y=py)
            view.canvas.event_generate('<ButtonRelease-1>',x=0,y=py)
            assert app.scale.get()==1
            app.scale.set(3)
            old=app.audio.get()
            click(view.audio_switch); assert app.audio.get()!=old
            key('Right'); assert app.audio.get()
            key('Left'); assert not app.audio.get()
            key('space'); assert app.audio.get()
            key('Tab'); assert view.focus_control is app.launch_button
            calls=[]
            command=app.launch_button.command
            app.launch_button.command=lambda:calls.append('play')
            app.launch_button.configure(state='disabled')
            click(app.launch_button); assert not calls
            app.launch_button.configure(state='normal')
            click(app.launch_button); assert calls==['play']
            app.launch_button.command=command
            click(view.settings_button); assert view.settings_window is not None
            pump(.1)
            dialog=view.settings_window
            dialog.geometry('620x460');pump(.1)
            assert dialog.winfo_width()==620
            view.settings_notebook.select(view.controls_tab)
            pump(.1)
            view.control_binding_buttons['forward'].invoke()
            pump(.1)
            view.binding_capture.event_generate('<KeyPress>',keysym='j')
            pump(.1)
            assert app.key_bindings['forward'].get()=='j'
            app.mouse_sensitivity.set('1.5')
            app.invert_y.set(True)
            dialog.geometry('760x740');pump(.15)
            view.controls_canvas.yview_moveto(0)
            pump(.1)
            capture('controls-preview.png',dialog)
            # Exercise the real Save and return command and settings persistence.
            close_command=dialog.protocol('WM_DELETE_WINDOW')
            dialog.tk.call(close_command)
            assert view.settings_window is None
            saved=Preferences.load()
            assert saved.scale==3 and saved.audio is True
            assert saved.controls.bindings['forward']=='j'
            assert saved.controls.sensitivity==1.5 and saved.controls.invert_y
            view.settings()
            view.reset_controls_button.invoke()
            view.settings_window.tk.call(view.settings_window.protocol('WM_DELETE_WINDOW'))
            from bio3dpc.input_settings import ControlSettings
            assert Preferences.load().controls==ControlSettings()
            reports['control_settings']='keyboard capture, sensitivity, invert Y, persistence, reset, 620x460 and 760x740'
            click(view.installation_button)
            pump(.1)
            installation=view.installation_window
            assert installation is not None
            assert 'not included or downloaded' in view.installation_guide.get('1.0','end')
            view.verify_button.invoke()
            assert 'Verified:' in app.installation_status.get()
            for width,height in ((780,700),(600,560)):
                installation.geometry(f'{width}x{height}')
                pump(.15)
                assert view.verify_button.winfo_ismapped()
                assert view.verify_button.winfo_rooty()+view.verify_button.winfo_height()<=installation.winfo_rooty()+installation.winfo_height()
                assert view.campaign_button.winfo_ismapped()
                assert view.campaign_button.winfo_rooty()+view.campaign_button.winfo_height()<=installation.winfo_rooty()+installation.winfo_height()
                assert view.installation_guide.winfo_height()>40
                if width==780:
                    capture('installation-preview.png',installation)
            installation.tk.call(installation.protocol('WM_DELETE_WINDOW'))
            assert view.installation_window is None
            reports['installation']='guide, verification, saved paths, 780x700 and 600x560'
            with patch('bio3dpc.bathysphere.subprocess.Popen') as opener:
                click(view.saves_button)
                assert opener.call_args.args[0][0]=='xdg-open'
                assert Path(opener.call_args.args[0][1]).is_dir()
            process=view.decoder.process
            view.animate.set(False);pump(.2)
            assert process.poll() is not None and view.decoder.process is None
            count=view.frames_drawn
            pump(.15);assert view.frames_drawn==count
            tall=min(900,window.winfo_screenheight()-100)
            wide=min(1440,window.winfo_screenwidth()-100)
            for width,height in ((wide,tall),(900,650),(640,tall),(760,540),(1152,768)):
                window.geometry(f'{width}x{height}')
                pump(.45)
                assert (window.winfo_width(),window.winfo_height())==(width,height), (width,height,window.geometry())
                assert view.stacked==(view.canvas.winfo_width()<820)
                assert view.decoder.process is None
                for control in view.controls:
                    x,y,w,h=control.rect
                    px,py=view.xy(x,y)
                    assert px>=-1 and px+w*view.scale<=view.canvas.winfo_width()+1
                    assert py>=0 and py+h*view.scale<=view.content_height+1
                if view.stacked:
                    assert view.scrollbar.winfo_width()>5
                    view.set_focus(view.saves_button)
                    top=view.canvas.canvasy(0)
                    px,py=view.xy(*view.saves_button.rect[:2])
                    assert top<=py and py+38*view.scale<=top+view.canvas.winfo_height()+2
                    if width==640:
                        capture('launcher-narrow-preview.png')
                reports[f'{width}x{height}']='stacked' if view.stacked else 'side by side'
            view.focus_control=None
            for control in view.controls:
                control.hover=False
                control.paint_state()
            view.canvas.event_generate('<Motion>',x=20,y=20)
            capture('launcher-preview.png')
            view.animate.set(True)
            pump(.8)
            assert view.decoder.process is not None and view.frames_drawn>count
            if args.record:
                x,y=window.winfo_rootx(),window.winfo_rooty()
                recording=subprocess.Popen(['ffmpeg','-hide_banner','-loglevel','error','-y',
                    '-f','x11grab','-framerate','60','-video_size','1152x768','-i',
                    f'{os.environ["DISPLAY"]}+{x},{y}','-t','12','-an','-c:v','libx264',
                    '-threads','2','-preset','fast','-crf','22','-movflags','+faststart',
                    str(ROOT/'docs/bathysphere-preview.mp4')])
                try:
                    deadline=time.monotonic()+20
                    while recording.poll() is None and time.monotonic()<deadline:
                        pump(.1)
                    assert recording.poll()==0,'UI preview recording failed'
                finally:
                    if recording.poll() is None:
                        recording.terminate()
                        recording.wait(timeout=3)
            # Playback can be stopped during a pending resize without restarting.
            window.geometry('1100x740');pump(.06)
            view.animate.set(False);pump(.4)
            assert view.decoder.process is None
            assert not errors
            reports['callback_errors']=len(errors)
            reports['controls']='editions, slider, arrows, audio, keyboard, installation, general and control settings, saves, disabled/pressed buttons'
            (ROOT/'docs/launcher-verification.json').write_text(json.dumps(reports,indent=2)+'\n')
            print(json.dumps(reports,indent=2))
        finally:
            app.close()
            assert view.decoder.process is None
            assert view.animation_job is None and view.resize_job is None and view.decoder_job is None
        print('LAUNCHER CHECK PASSED')


if __name__=='__main__':
    main()
