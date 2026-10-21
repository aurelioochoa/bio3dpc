"""Image-skinned canvas controls with native pointer and keyboard interaction."""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageTk

from . import ui_images

INK, GOLD, MUTED = '#f3e8cd', '#d9b879', '#b3c1b3'


class Artwork:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.photos = {}

    def path(self, name):
        return self.folder/f'{name}.png'

    def source(self, name):
        return ui_images.source(self.path(name), 'RGBA')

    def get(self, name, width, height):
        size = max(1, round(width)), max(1, round(height))
        key = name, size
        if key not in self.photos:
            # Resampled copies outlive clear(), so a reflow to a size seen
            # before only rebuilds the Tk photos.
            if name == 'logo':
                image=ui_images.letterboxed(self.source(name),size)
            else:
                image=ui_images.resized(self.source(name),size)
            self.photos[key] = ImageTk.PhotoImage(image)
        return self.photos[key]

    def clear(self):
        self.photos.clear()

    def hit_area(self,width,height):
        size=max(1,round(width)),max(1,round(height))
        key='transparent-hit-area',size
        if key not in self.photos:
            self.photos[key]=ImageTk.PhotoImage(Image.new('RGBA',size))
        return self.photos[key]


class Control:
    def __init__(self, view, rect, *, command=None):
        self.view, self.canvas, self.rect = view, view.canvas, rect
        self.command = command
        self.state, self.hover, self.pressed = 'normal', False, False
        self.tag = 'control-' + str(len(view.controls))
        self.focus_item = None
        view.controls.append(self)
        self.canvas.tag_bind(self.tag, '<Enter>', lambda e: self.enter(True))
        self.canvas.tag_bind(self.tag, '<Leave>', lambda e: self.enter(False))
        self.canvas.tag_bind(self.tag, '<ButtonPress-1>', self.press)

    def image(self, name, x, y, w, h):
        photo = self.view.art.get(name, w*self.view.scale, h*self.view.scale)
        return self.canvas.create_image(*self.view.xy(x,y), image=photo, anchor='nw', tags=self.tag)

    def text(self, x, y, text, **kwargs):
        return self.view.text(x,y,text,tags=self.tag,**kwargs)

    def outline(self):
        x,y,w,h = self.rect
        # Canvas rectangles with fill='' only receive events on their edges.
        # A transparent image makes the entire intended target interactive.
        hit=self.view.art.hit_area(w*self.view.scale,h*self.view.scale)
        self.canvas.create_image(*self.view.xy(x,y),image=hit,anchor='nw',tags=self.tag)
        radius=min(10,h/4)
        points=(x+radius,y,x+w-radius,y,x+w,y,x+w,y+radius,x+w,y+h-radius,
                x+w,y+h,x+w-radius,y+h,x+radius,y+h,x,y+h,x,y+h-radius,x,y+radius,x,y)
        self.focus_item = self.canvas.create_polygon(*self.view.xy(*points),
            outline='',fill='',smooth=True,width=1.5,tags=self.tag)
        self.paint_state()

    def paint_state(self):
        if self.focus_item:
            focused = self.view.focus_control is self
            self.canvas.itemconfigure(self.focus_item,
                outline=GOLD if self.state != 'disabled' and (focused or self.hover) else '',
                dash=(3,2) if focused else ())

    def enter(self, hover):
        self.hover = hover
        self.canvas.configure(cursor='hand2' if hover and self.state != 'disabled' else '')
        self.paint_state()

    def press(self, event):
        if self.state == 'disabled':
            return
        self.view.set_focus(self)
        self.view.pressed_control = self
        self.pressed = True
        self.canvas.move(self.tag,0,max(1,self.view.scale))

    def release(self, event):
        was_pressed, self.pressed = self.pressed, False
        self.draw()
        x,y,w,h=self.rect
        px,py=self.view.logical(event.x,self.canvas.canvasy(event.y))
        if was_pressed and x <= px <= x+w and y <= py <= y+h:
            self.invoke()

    def drag(self,event):
        pass

    def key(self, key):
        if key in ('Return','space'):
            self.invoke()

    def invoke(self):
        if self.state != 'disabled' and self.command:
            self.command()

    def configure(self, **kwargs):
        self.state = kwargs.get('state', self.state)
        self.draw()

    def cget(self, name):
        if name == 'state':
            return self.state
        raise KeyError(name)


class ImageButton(Control):
    def __init__(self, view, rect, *, skin='secondary', text='', icon=None, command=None, primary=False):
        super().__init__(view,rect,command=command)
        self.skin, self.label, self.icon, self.primary = skin,text,icon,primary

    def draw(self):
        self.canvas.delete(self.tag)
        x,y,w,h=self.rect
        self.image(self.skin,x,y,w,h)
        if self.label:
            self.text(x+w/2+(7 if self.icon else 0),y+h/2,self.label,
                      size=23 if self.primary else 12,
                      family='FreeSerif', color=('#273024' if self.primary else INK) if self.state != 'disabled' else '#6a735f')
        if self.icon:
            self.image(self.icon,x+13,y+(h-18)/2,18,18)
        self.outline()
        if self.state == 'disabled':
            self.canvas.create_rectangle(*self.view.xy(x+4,y+4,x+w-4,y+h-4),
                fill='#0c211c', stipple='gray50',outline='',tags=self.tag)


class ScaleSlider(Control):
    def __init__(self,view,rect,variable):
        super().__init__(view,rect)
        self.variable=variable

    def draw(self):
        self.canvas.delete(self.tag)
        x,y,w,h=self.rect
        self.image('slider-track',x+24,y+(h-12)/2,w-48,12)
        self.image('arrow-left',x+3,y+(h-12)/2,12,12)
        self.image('arrow-right',x+w-15,y+(h-12)/2,12,12)
        for i in range(6):
            tx=x+30+(w-60)*i/5
            self.canvas.create_line(*self.view.xy(tx,y+h-3,tx,y+h),fill='#97804d',tags=self.tag)
        tx=x+30+(w-60)*(self.variable.get()-1)/5
        self.image('slider-thumb',tx-9,y+(h-25)/2,18,25)
        # An invisible tag image covers the whole trough, including its gaps.
        self.canvas.create_rectangle(*self.view.xy(x,y,x+w,y+h),outline='',fill='',tags=self.tag)
        self.outline()

    def press(self,event):
        if self.state == 'disabled':
            return
        self.view.set_focus(self)
        self.view.pressed_control=self
        self.pressed=True
        px,_=self.view.logical(event.x,self.canvas.canvasy(event.y))
        x,y,w,h=self.rect
        if px<x+22:
            self.variable.set(max(1,self.variable.get()-1))
        elif px>x+w-22:
            self.variable.set(min(6,self.variable.get()+1))
        else:
            self.drag(event)

    def drag(self,event):
        if not self.pressed:
            return
        px,_=self.view.logical(event.x,self.canvas.canvasy(event.y))
        x,y,w,h=self.rect
        self.variable.set(max(1,min(6,round(1+5*(px-x-30)/(w-60)))))

    def release(self,event):
        self.pressed=False

    def key(self,key):
        value=self.variable.get()
        if key in ('Left','Down'): value-=1
        elif key in ('Right','Up'): value+=1
        elif key=='Home': value=1
        elif key=='End': value=6
        self.variable.set(max(1,min(6,value)))


class AudioSwitch(Control):
    def __init__(self,view,rect,variable):
        super().__init__(view,rect,command=lambda: variable.set(not variable.get()))
        self.variable=variable

    def draw(self):
        self.canvas.delete(self.tag)
        x,y,w,h=self.rect
        on=self.variable.get()
        self.image('toggle-track',x,y,w,h)
        self.image('toggle-knob',x+w-29 if on else x+3,y+3,26,26)
        self.text(x+(w*.38 if on else w*.65),y+h/2,'On' if on else 'Off',size=13)
        self.outline()

    def key(self,key):
        if key in ('Left','Right'):
            self.variable.set(key=='Right')
        else:
            super().key(key)


class EditionChoice(Control):
    def __init__(self,view,rect,profile,index):
        super().__init__(view,rect,command=lambda: view.choose(profile))
        self.profile,self.index=profile,index

    def draw(self):
        self.canvas.delete(self.tag)
        x,y,w,h=self.rect
        portrait=self.profile.width<self.profile.height
        pw,ph=(100,128) if portrait else (174,132)
        px=x+(w-pw)/2
        py=y+132-ph
        self.image('preview-portrait' if portrait else 'preview-landscape',px+5,py+5,pw-10,ph-10)
        self.image('portrait-frame' if portrait else 'landscape-frame',px,py,pw,ph)
        self.text(x+w/2,y+149,'Portrait' if portrait else 'Landscape',size=14,family='FreeSerif')
        self.text(x+w/2,y+172,'LG VX8350' if portrait else 'Nokia 7705',size=9,color=MUTED)
        self.text(x+w/2,y+190,f'{self.profile.width} × {self.profile.height}',size=9,color=MUTED)
        selected=self.view.app.selected is not None and self.view.app.selected.id==self.profile.id
        self.image('radio-on' if selected else 'radio-off',x+w/2-10,y+204,20,20)
        if not selected:
            self.canvas.create_oval(*self.view.xy(x+w/2-5.5,y+208.5,x+w/2+5.5,y+219.5),
                                    fill='#0a201d',outline='#5e4926',tags=self.tag)
        self.outline()

    def key(self,key):
        if key in ('Left','Right'):
            self.view.choose(self.view.app.options[0 if key=='Left' else -1])
        else:
            super().key(key)
