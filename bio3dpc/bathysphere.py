"""Responsive Bathysphere artwork with image-skinned, live launcher controls."""
from __future__ import annotations

from pathlib import Path
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageTk, ImageOps

from .animation import LoopVideo, LampFlicker
from .launcher_controls import Artwork, ImageButton, ScaleSlider, AudioSwitch, EditionChoice

ASSETS = Path(__file__).resolve().parent / 'ui_assets'
GOLD, INK, MUTED, DARK = '#d9b879', '#f3e8cd', '#b3c1b3', '#0a201d'


class BathysphereView:
    def __init__(self, app):
        self.app, self.window = app, app.window
        self.closed, self.paused = False, False
        self.animation_job = self.resize_job = self.decoder_job = None
        self.frames_drawn = 0
        self.settings_window = None
        self.installation_window = None
        self.animate = tk.BooleanVar(value=app.preferences.animation)
        self.controls = []
        self.focus_control = self.pressed_control = None
        self.photo = None
        self.scale, self.origin = 1., (736., 0.)
        self.art = Artwork(ASSETS/'controls')
        self.plate = Image.open(ASSETS/'bathysphere-source.png').convert('RGB').resize((1152,768),Image.Resampling.LANCZOS)
        self.left_plate, self.right_plate = self.plate.crop((0,0,736,768)), self.plate.crop((736,0,1152,768))
        self.lamps = [LampFlicker() for _ in range(4)]
        self.lamp_items, self.lamp_levels = [], [-1]*4
        self.window.title('BioShock 3D · BREW PC PORT')
        width=min(1152,self.window.winfo_screenwidth()-80)
        height=min(768,self.window.winfo_screenheight()-100)
        self.window.geometry(f'{max(640,width)}x{max(520,height)}')
        self.window.minsize(640,520)
        self.window.resizable(True,True)
        self.window.configure(bg=DARK)
        if (ASSETS/'icon.png').is_file():
            self.icon=ImageTk.PhotoImage(Image.open(ASSETS/'icon.png').resize((64,64),Image.Resampling.LANCZOS))
            self.window.iconphoto(True,self.icon)
        self.canvas=tk.Canvas(self.window,bg=DARK,highlightthickness=0,takefocus=True)
        self.scrollbar=ttk.Scrollbar(self.window,orient='vertical',command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(fill='both',expand=True)
        self.background=self.canvas.create_image(0,0,anchor='nw')
        self.panel=self.canvas.create_image(0,0,anchor='nw')
        self.decoder=LoopVideo(ASSETS/'bathysphere-loop.mp4')
        self._create_controls()
        self.canvas.bind('<Configure>',self._configure)
        self.canvas.bind('<ButtonRelease-1>',self._release)
        self.canvas.bind('<B1-Motion>',self._drag)
        self.canvas.bind('<KeyPress>',self._key)
        self.canvas.bind('<MouseWheel>',self._wheel)
        self.canvas.bind('<Button-4>',lambda e: self._scroll(-3))
        self.canvas.bind('<Button-5>',lambda e: self._scroll(3))
        for variable in (app.detail,app.status,app.scale,app.audio):
            variable.trace_add('write',self.refresh)
        self.animate.trace_add('write',lambda *args:self.resume() if self.animate.get() else self.pause())
        self.reflow(max(640,width),max(520,height))
        self.resume()

    def xy(self,*values):
        return [value*self.scale+self.origin[index%2] for index,value in enumerate(values)]

    def logical(self,x,y):
        return (x-self.origin[0])/self.scale,(y-self.origin[1])/self.scale

    def font(self,family,size,style='normal'):
        return family,max(8,round(size*self.scale)),style

    def text(self,x,y,value,*,size=12,color=INK,family='FreeSans',**kwargs):
        return self.canvas.create_text(*self.xy(x,y),text=value,fill=color,font=self.font(family,size),**kwargs)

    def _create_controls(self):
        app=self.app
        self.editions=[]
        positions=((40,240,125,230),(180,240,190,230))
        for index,profile in enumerate(app.options[:2]):
            self.editions.append(EditionChoice(self,positions[index],profile,index))
        self.scale_slider=ScaleSlider(self,(44,543,190,36),app.scale)
        self.audio_switch=AudioSwitch(self,(258,545,122,32),app.audio)
        app.launch_button=ImageButton(self,(40,590,336,60),skin='play',text='P L A Y',primary=True,command=app.launch)
        self.settings_button=ImageButton(self,(40,662,160,38),text='Settings',icon='icon-settings',command=self.settings)
        self.saves_button=ImageButton(self,(208,662,168,38),text='Saved games',icon='icon-folder',command=self.saved_games)
        self.controls_button=ImageButton(self,(40,705,160,30),text='Controls',icon='icon-settings',
                                        command=lambda:self.settings('Controls'))
        self.installation_button=ImageButton(self,(208,705,168,30),text='Installation',icon='icon-folder',command=self.installation)

    def _configure(self,event):
        if self.closed:
            return
        if self.resize_job is not None:
            self.window.after_cancel(self.resize_job)
        self.resize_job=self.window.after(45,lambda:self.reflow(event.width,event.height))

    def reflow(self,width,height):
        self.resize_job=None
        if self.closed:
            return
        self.art.clear()
        self.stacked=width<820
        if self.stacked:
            if not self.scrollbar.winfo_manager():
                self.canvas.pack_forget()
                self.scrollbar.pack(side='right',fill='y')
                self.canvas.pack(side='left',fill='both',expand=True)
            art_height=max(180,min(300,round(width*.38)))
            self.scale=min(1.25,width/440,max(.85,(height-art_height)/768))
            self.origin=((width-416*self.scale)/2,art_height)
            panel_x,panel_y,panel_w,panel_h=0,art_height,width,round(768*self.scale)
            content_height=art_height+panel_h
            art_w,art_h=width,art_height
        else:
            self.scrollbar.pack_forget()
            self.canvas.yview_moveto(0)
            self.scale=min(height/768,width/800)
            panel_w=round(416*self.scale)
            panel_x,panel_y,panel_h=width-panel_w,0,height
            self.origin=(panel_x,(height-768*self.scale)/2)
            content_height=height
            art_w,art_h=panel_x,height
        self.content_height=content_height
        self.canvas.configure(scrollregion=(0,0,width,content_height))
        self.art_size=max(1,art_w),max(1,art_h)
        self.photo=ImageTk.PhotoImage(ImageOps.fit(self.left_plate,self.art_size,Image.Resampling.LANCZOS))
        self.canvas.itemconfigure(self.background,image=self.photo)
        self.panel_photo=ImageTk.PhotoImage(self.right_plate.resize((max(1,panel_w),max(1,panel_h)),Image.Resampling.LANCZOS))
        self.canvas.coords(self.panel,panel_x,panel_y)
        self.canvas.itemconfigure(self.panel,image=self.panel_photo)
        self._draw_labels()
        for control in self.controls:
            control.hover=False
            control.draw()
        self._place_lamps()
        self._hints()
        self.refresh()
        # Debounce decoding independently, so dragging a window never starts a
        # decoder for every intermediate size. The still plate stays visible.
        if self.decoder_job is not None:
            self.window.after_cancel(self.decoder_job)
        self.decoder_job=self.window.after(180,self._resize_decoder)

    def _draw_labels(self):
        c=self.canvas
        c.delete('labels')
        logo=self.art.get('logo',348*self.scale,152*self.scale)
        c.create_image(*self.xy(34,38),image=logo,anchor='nw',tags='labels')
        for y in (198,503):
            c.create_line(*self.xy(40,y,376,y),fill='#887347',tags='labels')
        self.text(44,218,'Choose your edition',size=14,family='FreeSerif',anchor='w',tags='labels')
        self.caveat=self.text(208,484,'',size=9,color=MUTED,width=round(332*self.scale),justify='center',tags='labels')
        self.text(44,526,'Window scale',size=12,family='FreeSerif',anchor='w',tags='labels')
        self.scale_value=self.text(229,526,'',size=13,color=GOLD,anchor='e',tags='labels')
        self.text(258,526,'Audio',size=12,family='FreeSerif',anchor='w',tags='labels')
        self.status_text=self.text(208,750,'',size=8,color=MUTED,width=round(330*self.scale),justify='center',tags='labels')

    def _hints(self):
        c=self.canvas
        c.delete('hints')
        w,h=self.art_size
        if self.stacked:
            return
        c.create_text(w/2,h-24*self.scale,
                      text='Settings → Controls    ·    Tab: release mouse    ·    Enter: confirm',
                      fill=INK,font=('FreeSans',max(8,round(10*self.scale))),tags='hints')

    def _place_lamps(self):
        c=self.canvas
        for item in self.lamp_items:
            c.delete(item)
        self.lamp_items=[]
        self.lamp_levels=[-1]*4
        w,h=self.art_size
        factor=max(w/736,h/768)
        ox,oy=(w-736*factor)/2,(h-768*factor)/2
        # A small cache of pre-rendered alpha masks keeps every frame cheap.
        self.lamp_photos=[]
        for index,(x,y,size) in enumerate(((58,438,1),(674,503,1),(548,574,.48),(585,685,.48))):
            photos=[]
            for level in range(16):
                image=self.art.source(f'lamp-{level:02}')
                dimensions=max(1,round(72*factor*size)),max(1,round(104*factor*size))
                photos.append(ImageTk.PhotoImage(image.resize(dimensions,Image.Resampling.LANCZOS)))
            self.lamp_photos.append(photos)
            self.lamp_items.append(c.create_image(ox+x*factor,oy+y*factor,image=photos[12]))
        # Lamp overlays must never extend beyond the observation panel.
        c.tag_raise(self.panel)
        c.tag_raise('labels')
        for control in self.controls:
            c.tag_raise(control.tag)
        c.tag_raise('hints')

    def _resize_decoder(self):
        self.decoder_job=None
        if self.closed:
            return
        self.decoder.stop()
        self.decoder=LoopVideo(ASSETS/'bathysphere-loop.mp4',*self.art_size,crop='736:768:0:0')
        if not self.paused:
            self.resume()

    def set_focus(self,control):
        previous=self.focus_control
        self.focus_control=control
        self.canvas.focus_set()
        if previous:
            previous.paint_state()
        control.paint_state()
        if self.stacked:
            x,y,w,h=control.rect
            top=self.xy(x,y)[1]
            bottom=top+h*self.scale
            visible_top=self.canvas.canvasy(0)
            if top<visible_top:
                self.canvas.yview_moveto(max(0,top-12)/self.content_height)
            elif bottom>visible_top+self.canvas.winfo_height():
                self.canvas.yview_moveto((bottom-self.canvas.winfo_height()+12)/self.content_height)

    def _key(self,event):
        if event.keysym in ('Tab','ISO_Left_Tab'):
            eligible=[control for control in self.controls if control.state!='disabled']
            index=eligible.index(self.focus_control) if self.focus_control in eligible else -1
            backwards=event.keysym=='ISO_Left_Tab' or bool(event.state & 1)
            self.set_focus(eligible[(index+(-1 if backwards else 1))%len(eligible)])
        elif self.focus_control:
            self.focus_control.key(event.keysym)
        elif event.keysym in ('Left','Right') and self.app.options:
            self.choose(self.app.options[0 if event.keysym=='Left' else -1])
        elif event.keysym=='Return':
            self.app.launch()
        else:
            return
        return 'break'

    def _release(self,event):
        control,self.pressed_control=self.pressed_control,None
        if control:
            control.release(event)

    def _drag(self,event):
        if self.pressed_control:
            self.pressed_control.drag(event)

    def _scroll(self,units):
        if self.stacked:
            self.canvas.yview_scroll(units,'units')
        return 'break'

    def _wheel(self,event):
        return self._scroll(-int(event.delta/120) if abs(event.delta)>=120 else (-1 if event.delta>0 else 1))

    def choose(self,profile):
        self.app.build.set(profile.title)
        self.app.select_build()

    def refresh(self,*args):
        if self.closed or not hasattr(self,'caveat'):
            return
        app=self.app
        for choice in self.editions:
            choice.draw()
        self.scale_slider.draw()
        self.audio_switch.draw()
        caveat='Later campaign assets are missing from this landscape collection.' if app.selected and app.selected.id=='n7705' else 'Select an original phone edition.'
        self.canvas.itemconfigure(self.caveat,text=caveat)
        self.canvas.itemconfigure(self.scale_value,text=f'{app.scale.get()}×')
        status=app.status.get()
        if 'Ready to launch' in status:
            status='Original game files detected · Saves kept separately'
        self.canvas.itemconfigure(self.status_text,text=status)

    def resume(self):
        if self.closed or not self.animate.get() or self.app.child is not None:
            return
        if self.paused:
            self.lamps=[LampFlicker() for _ in range(4)]
        self.paused=False
        self.decoder.start()
        if self.animation_job is None:
            self.frame_due=time.monotonic()
            self.animation_job=self.window.after(16,self._frame)

    def _frame(self):
        self.animation_job=None
        if self.closed or self.paused:
            return
        frame=self.decoder.latest()
        if frame is not None:
            self.photo=tk.PhotoImage(data=frame,format='PPM')
            self.canvas.itemconfigure(self.background,image=self.photo)
            self.frames_drawn+=1
        now=time.monotonic()
        for index,lamp in enumerate(self.lamps):
            level=max(0,min(15,round(lamp.sample(now)*15)))
            if level!=self.lamp_levels[index]:
                self.canvas.itemconfigure(self.lamp_items[index],image=self.lamp_photos[index][level])
                self.lamp_levels[index]=level
        self.frame_due+=1/60
        if self.frame_due<now:
            self.frame_due=now
        self.animation_job=self.window.after(max(1,round((self.frame_due-time.monotonic())*1000)),self._frame)

    def pause(self):
        self.paused=True
        if self.animation_job is not None:
            self.window.after_cancel(self.animation_job)
            self.animation_job=None
        self.decoder.stop()

    def close(self):
        self.closed=True
        self.pause()
        for name in ('resize_job','decoder_job'):
            job=getattr(self,name)
            if job is not None:
                self.window.after_cancel(job)
                setattr(self,name,None)
        if self.settings_window is not None:
            self.settings_window.destroy()
            self.settings_window=None
        if self.installation_window is not None:
            self.installation_window.destroy()
            self.installation_window=None

    def installation(self):
        if self.installation_window is not None:
            self.installation_window.lift()
            return
        from tkinter.scrolledtext import ScrolledText
        app=self.app
        dialog=self.installation_window=tk.Toplevel(self.window)
        dialog.title('BioShock 3D · Installation')
        dialog.geometry('780x700')
        dialog.minsize(600,560)
        dialog.transient(self.window)
        dialog.configure(bg=DARK)
        panel=tk.Frame(dialog,bg=DARK,padx=20,pady=16)
        panel.pack(fill='both',expand=True)
        panel.columnconfigure(0,weight=1)
        panel.rowconfigure(1,weight=1)
        tk.Label(panel,text='Bring your own original BREW game files',bg=DARK,fg=GOLD,
                 font=('FreeSerif',17)).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,10))
        guide=ScrolledText(panel,height=10,wrap='word',bg='#102b24',fg=INK,
                           font=('FreeSans',11),relief='flat',padx=10,pady=8)
        guide.grid(row=1,column=0,columnspan=2,sticky='nsew',pady=(0,12))
        guide.insert('1.0',
            'The game is not included or downloaded. Supply your own extracted BioShock 3D '
            'BREW phone build: bio3d.mod (the executable module), bio3d.idx (the asset index), '
            'and its matching bio*.dat game data packs.\n\n'
            '1. Select your edition in the main window: LG VX8350 v1.4.1.1 (portrait), '
            'or Nokia 7705 v1.5.2.1 (landscape).\n'
            '2. Browse to your bioshock3d_game_files collection below, or select the module and '
            'matching data folder individually. Keep original filenames.\n'
            '3. Choose Verify files, then Save and return. Use Play in the main window.\n\n'
            'Portrait needs bio1.dat through bio7.dat and bio3d.idx from the same build. '
            'Its complete data folder is usually data/vx8350. Landscape uses its own '
            '278583 folder with bio1.dat, bio2.dat and bio3d.idx; later campaign packs '
            'are missing from the known collection. Do not mix handset builds.\n\n'
            'Default collection locations: bioshock3d_game_files beside this checkout, '
            'then under ~/Downloads. Legacy bio3d_versions collections are also supported. '
            'Handset releases live in release_builds; older flat layouts also work. '
            'You may also set BIO3DPC_LIBRARY_DIR or use '
            '--library. A custom folder name works with individual file selection.\n\n'
            'These are BREW modules and game data, commonly called game files. '
            'Android APKs, iPhone IPAs, Java JARs and console ROMs are unsupported. '
            'Archives must be extracted first.\n\n'
            'Files are used in place, without copying or modifying originals. Keep the '
            'source folder or drive available. Saves are stored separately under '
            '~/.local/state/bio3dpc/saves (or XDG_STATE_HOME).')
        guide.configure(state='disabled')
        self.installation_guide=guide
        self._path_controls(panel,dialog,start=2)
        tk.Label(panel,textvariable=app.build,bg=DARK,fg=GOLD,font=('FreeSans',11)).grid(
            row=8,column=0,columnspan=2,sticky='w')
        result=ScrolledText(panel,height=3,wrap='word',bg=DARK,fg=INK,
                            font=('FreeSans',10),relief='flat')
        result.grid(row=9,column=0,columnspan=2,sticky='ew',pady=8)
        def update_result(*args):
            result.configure(state='normal')
            result.delete('1.0','end')
            result.insert('1.0',app.installation_status.get())
            result.configure(state='disabled')
        trace=app.installation_status.trace_add('write',update_result)
        def done():
            from tkinter import messagebox
            try:
                app.refresh_status()
                app.save()
            except (OSError,ValueError,tk.TclError) as error:
                messagebox.showerror('Cannot save installation',str(error),parent=dialog)
                return
            app.installation_status.trace_remove('write',trace)
            dialog.destroy()
            self.installation_window=None
        self.verify_button=self._dialog_button(panel,'Verify files',app.refresh_status,'secondary',150,38)
        self.verify_button.grid(row=10,column=0,sticky='w',pady=8)
        self._dialog_button(panel,'Save and return',done,'secondary',190,38).grid(row=10,column=1,pady=8)
        dialog.protocol('WM_DELETE_WINDOW',done)
        app.refresh_status()

    def settings(self,tab=None):
        if self.settings_window is not None:
            self.settings_window.lift()
            if tab:
                self.settings_notebook.select(self.controls_tab if tab=='Controls' else 0)
            return
        app=self.app
        dialog=self.settings_window=tk.Toplevel(self.window)
        dialog.title('BREW PC PORT · Settings')
        dialog.geometry('760x560')
        dialog.minsize(600,430)
        dialog.transient(self.window)
        dialog.configure(bg=DARK)
        style=ttk.Style(dialog)
        # Scoped names avoid changing the game's widgets or the desktop theme.
        style.configure('Bathysphere.TFrame',background=DARK)
        style.configure('Bathysphere.TLabel',background=DARK,foreground=INK,font=('FreeSans',11))
        style.configure('Bathysphere.TCheckbutton',background=DARK,foreground=INK)
        style.map('Bathysphere.TCheckbutton',background=[('active','#1b332a')],foreground=[('active',GOLD)])
        outer=ttk.Frame(dialog,padding=12,style='Bathysphere.TFrame')
        outer.pack(fill='both',expand=True)
        self.settings_notebook=ttk.Notebook(outer)
        self.settings_notebook.pack(fill='both',expand=True)
        panel=ttk.Frame(self.settings_notebook,padding=12,style='Bathysphere.TFrame')
        self.settings_notebook.add(panel,text='General')
        self.controls_tab=ttk.Frame(self.settings_notebook,style='Bathysphere.TFrame')
        self.settings_notebook.add(self.controls_tab,text='Controls')
        self._control_settings(self.controls_tab,dialog)
        if tab=='Controls':
            self.settings_notebook.select(self.controls_tab)
        panel.columnconfigure(0,weight=1)
        self._path_controls(panel,dialog)
        options=ttk.Frame(panel,style='Bathysphere.TFrame')
        options.grid(row=6,column=0,columnspan=2,sticky='w',pady=8)
        ttk.Label(options,text='Window scale',style='Bathysphere.TLabel').pack(side='left')
        ttk.Combobox(options,textvariable=app.scale,state='readonly',values=(1,2,3,4,5,6),width=3).pack(side='left',padx=10)
        ttk.Checkbutton(options,text='Audio',variable=app.audio,style='Bathysphere.TCheckbutton').pack(side='left',padx=10)
        ttk.Checkbutton(options,text='Animated background',variable=self.animate,style='Bathysphere.TCheckbutton').pack(side='left',padx=10)
        status=ttk.Label(panel,textvariable=app.status,wraplength=680,style='Bathysphere.TLabel')
        status.grid(row=7,column=0,columnspan=2,sticky='ew',pady=8)
        panel.bind('<Configure>',lambda event:status.configure(wraplength=max(300,event.width-40)))
        def done():
            from tkinter import messagebox
            try:
                app.refresh_status()
                app.save()
            except (OSError,ValueError,tk.TclError) as error:
                self.settings_notebook.select(self.controls_tab)
                messagebox.showerror('Cannot save settings',str(error),parent=dialog)
                return
            dialog.destroy()
            self.settings_window=None
        footer=ttk.Frame(outer,style='Bathysphere.TFrame')
        footer.pack(fill='x',pady=(10,0))
        self._dialog_button(footer,'Save and return',done,'secondary',190,38).pack(side='right')
        self._dialog_button(footer,'View game log',app.show_log,'secondary',130,38).pack(side='left')
        dialog.protocol('WM_DELETE_WINDOW',done)

    def _control_settings(self,parent,dialog):
        from .input_settings import BINDINGS, ControlSettings, key_label, normalize_key
        app=self.app
        canvas=tk.Canvas(parent,bg=DARK,highlightthickness=0)
        self.controls_canvas=canvas
        scrollbar=ttk.Scrollbar(parent,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right',fill='y')
        canvas.pack(side='left',fill='both',expand=True)
        form=tk.Frame(canvas,bg=DARK,padx=12,pady=10)
        item=canvas.create_window(0,0,window=form,anchor='nw')
        canvas.bind('<Configure>',lambda event:canvas.itemconfigure(item,width=event.width))
        form.columnconfigure((0,1),weight=1)
        def label(parent,text,**kwargs):
            kwargs.setdefault('fg',INK)
            return tk.Label(parent,text=text,bg=DARK,font=('FreeSans',11),**kwargs)
        label(form,'Changes apply the next time you launch the game.',anchor='w').grid(
            row=0,column=0,columnspan=2,sticky='ew',pady=(0,10))
        mouse=tk.Frame(form,bg=DARK)
        mouse.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(0,10))
        ttk.Checkbutton(mouse,text='Mouse look',variable=app.mouse_look,
                        style='Bathysphere.TCheckbutton').grid(row=0,column=0,sticky='w')
        ttk.Checkbutton(mouse,text='Invert mouse Y',variable=app.invert_y,
                        style='Bathysphere.TCheckbutton').grid(row=0,column=1,sticky='w',padx=14)
        label(mouse,'Mouse sensitivity (0.1–5.0×)').grid(row=1,column=0,sticky='w',pady=8)
        self.sensitivity_entry=ttk.Spinbox(mouse,from_=0.1,to=5.0,increment=0.1,
                                          textvariable=app.mouse_sensitivity,width=7)
        self.sensitivity_entry.grid(row=1,column=1,sticky='w',padx=14)
        sensitivity_position=tk.DoubleVar(value=float(app.mouse_sensitivity.get()))
        self.sensitivity_slider=ttk.Scale(mouse,from_=0.1,to=5.0,variable=sensitivity_position,
            command=lambda value:app.mouse_sensitivity.set(f'{float(value):.1f}'))
        self.sensitivity_slider.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(0,4))
        label(mouse,'Lower values slow the camera. Default: 1.0×.',anchor='w').grid(
            row=3,column=0,columnspan=2,sticky='w')
        self.control_binding_buttons={}
        action_labels=[]
        traces=[]
        def sync_sensitivity(*args):
            try:
                value=float(app.mouse_sensitivity.get())
            except ValueError:
                return
            if 0.1<=value<=5.0:
                sensitivity_position.set(value)
        traces.append((app.mouse_sensitivity,app.mouse_sensitivity.trace_add('write',sync_sensitivity)))
        sync_sensitivity()
        def capture(action):
            prompt=tk.Toplevel(dialog)
            prompt.title('Assign control')
            prompt.transient(dialog)
            prompt.resizable(False,False)
            prompt.configure(bg=DARK)
            self.binding_capture=prompt
            title=next(title for name,title,default,canonical in BINDINGS if name==action)
            label(prompt,'Press a key for '+title).pack(padx=20,pady=(20,8))
            hint=tk.StringVar(value='Escape cancels. Backspace clears this binding.')
            tk.Label(prompt,textvariable=hint,bg=DARK,fg=GOLD,wraplength=450).pack(padx=20,pady=(0,20))
            def pressed(event):
                if event.keysym=='Escape':
                    prompt.destroy()
                    return 'break'
                if event.keysym in ('Shift_L','Shift_R','Control_L','Control_R','Alt_L','Alt_R','Super_L','Super_R'):
                    return 'break'
                key='' if event.keysym=='BackSpace' else normalize_key(event.keysym)
                try:
                    if event.state & 0xC:
                        raise ValueError('Choose a single key without Ctrl or Alt.')
                    values={name:var.get() for name,var in app.key_bindings.items()}
                    values[action]=key
                    ControlSettings.from_dict({'bindings':values})
                except ValueError as error:
                    hint.set(str(error))
                    return 'break'
                app.key_bindings[action].set(key)
                prompt.destroy()
                return 'break'
            prompt.bind('<KeyPress>',pressed)
            prompt.grab_set()
            prompt.focus_force()
        for column,bindings in enumerate((BINDINGS[:10],BINDINGS[10:])):
            group=tk.Frame(form,bg=DARK)
            group.grid(row=2,column=column,sticky='new',padx=(0,12) if column==0 else (12,0))
            group.columnconfigure(0,weight=1)
            label(group,'Movement and actions' if column==0 else 'Equipment',fg=GOLD).grid(
                row=0,column=0,columnspan=2,sticky='w',pady=(0,8))
            for row,(action,title,default,canonical) in enumerate(bindings,1):
                action_label=label(group,title,anchor='w',wraplength=205)
                action_label.grid(row=row,column=0,sticky='w',pady=5)
                action_labels.append(action_label)
                button=tk.Button(group,text=key_label(app.key_bindings[action].get()),width=8,
                                 command=lambda action=action:capture(action),bg='#102b24',fg=INK,
                                 activebackground='#1b332a',activeforeground=GOLD,relief='flat',
                                 highlightthickness=1,highlightcolor=GOLD,highlightbackground='#70603d')
                button.grid(row=row,column=1,sticky='e',padx=(6,0),pady=4)
                self.control_binding_buttons[action]=button
                variable=app.key_bindings[action]
                trace=variable.trace_add('write',lambda *args,var=variable,b=button:b.configure(text=key_label(var.get())))
                traces.append((variable,trace))
        help_label=label(form,'Click a key to change it. Backspace clears a binding; each key has one action.\n'
              'Fixed: Enter/Space confirm · Esc/Backspace pause · Tab frees the pointer.\n'
              'Mouse: left fires, right switches weapon/plasmid, middle changes ammo; wheel selects equipment.\n'
              'Phone keypad and F4–F7/F10/F11 retain their original functions.',
              justify='left',wraplength=640)
        help_label.grid(row=3,column=0,columnspan=2,sticky='ew',pady=12)
        def reflow(event):
            help_label.configure(wraplength=max(250,event.width-24))
            for action_label in action_labels:
                action_label.configure(wraplength=max(100,event.width//2-112))
            canvas.configure(scrollregion=canvas.bbox('all'))
        form.bind('<Configure>',reflow)
        def reset():
            defaults=ControlSettings()
            app.mouse_look.set(defaults.mouse_look)
            app.mouse_sensitivity.set(str(defaults.sensitivity))
            app.invert_y.set(defaults.invert_y)
            for action,key in defaults.bindings.items():
                app.key_bindings[action].set(key)
        self.reset_controls_button=self._dialog_button(form,'Reset controls',reset,'secondary',160,34)
        self.reset_controls_button.grid(row=4,column=0,sticky='w',pady=(0,10))
        def cleanup(event):
            if event.widget is dialog:
                for variable,trace in traces:
                    variable.trace_remove('write',trace)
        dialog.bind('<Destroy>',cleanup,add='+')
        def wheel(event):
            units=-1 if getattr(event,'num',0)==4 or getattr(event,'delta',0)>0 else 1
            canvas.yview_scroll(units*3,'units')
            return 'break'
        def focus(event):
            top=event.widget.winfo_rooty()-form.winfo_rooty()
            bottom=top+event.widget.winfo_height()
            visible=canvas.canvasy(0)
            total=max(1,form.winfo_height())
            if top<visible:
                canvas.yview_moveto(max(0,top-8)/total)
            elif bottom>visible+canvas.winfo_height():
                canvas.yview_moveto((bottom-canvas.winfo_height()+8)/total)
        def bind_scroll(widget):
            for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):
                widget.bind(sequence,wheel)
            widget.bind('<FocusIn>',focus,add='+')
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(form)
        canvas.bind('<MouseWheel>',wheel)
        canvas.bind('<Button-4>',wheel)
        canvas.bind('<Button-5>',wheel)

    def _path_controls(self,panel,dialog,*,start=0):
        app=self.app
        collection=[app.library.get()]
        def apply_collection(event=None):
            if app.library.get()!=collection[0]:
                collection[0]=app.library.get()
                app.select_build(reset=True)
        self.browse_buttons=[]
        for row,title,var,directory in ((start,'Game files (bioshock3d_game_files)',app.library,True),
                                        (start+2,'Original BREW module (.mod)',app.mod,False),
                                        (start+4,'Matching game data folder (.dat + .idx)',app.data,True)):
            tk.Label(panel,text=title,bg=DARK,fg=INK,font=('FreeSans',11)).grid(row=row,column=0,sticky='w')
            entry=tk.Entry(panel,textvariable=var,bg='#102b24',fg=INK,insertbackground=GOLD,relief='flat',highlightthickness=1,highlightbackground='#70603d',highlightcolor=GOLD)
            entry.grid(row=row+1,column=0,sticky='ew',pady=(5,13),padx=(0,10),ipady=5)
            def browse(variable=var,is_directory=directory):
                choice=filedialog.askdirectory(parent=dialog) if is_directory else filedialog.askopenfilename(parent=dialog,filetypes=[('BREW module','*.mod')])
                if choice:
                    variable.set(choice)
                    if variable is app.library:
                        apply_collection()
                    app.refresh_status()
            button=self._dialog_button(panel,'Browse…',browse,'secondary',90,32)
            button.grid(row=row+1,column=1)
            self.browse_buttons.append(button)
            if var is app.library:
                entry.bind('<FocusOut>',apply_collection)

    def _dialog_button(self,parent,text,command,skin,width,height):
        photo=self.art.get(skin,width,height)
        button=tk.Button(parent,image=photo,text=text,compound='center',command=command,bg=DARK,activebackground=DARK,fg=INK,activeforeground=GOLD,font=('FreeSerif',12),relief='flat',bd=0,highlightthickness=1,highlightbackground=DARK,highlightcolor=GOLD,cursor='hand2')
        button.photo=photo
        return button

    def saved_games(self):
        from .profiles import default_save_dir
        from .launcher import xdg_path
        mod=Path(self.app.mod.get()).expanduser()
        path=default_save_dir(mod) if mod.is_file() else xdg_path('XDG_STATE_HOME','.local/state')/'bio3dpc/saves'
        path.mkdir(parents=True,exist_ok=True)
        try:
            subprocess.Popen(['xdg-open',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except OSError as error:
            self.app.status.set(str(path)+': '+str(error))
