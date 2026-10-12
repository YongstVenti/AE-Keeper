"""Antialiased nine-slice native Tk controls with keyboard/state support."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw, ImageTk

from branding import BG, WHITE, ACCENT, HOVER, PRESSED, TINT, TINT_HOVER, TINT_PRESSED, BORDER, FOCUS


def install_rounded_theme(root, style):
    images = []
    def tile(fill, background=BG, outline=None, radius=12):
        scale, size = 4, 36
        im = Image.new('RGB', (size*scale, size*scale), background)
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((scale,scale,(size-1)*scale,(size-1)*scale),
                               radius=radius*scale,fill=fill,outline=outline,width=scale if outline else 0)
        photo = ImageTk.PhotoImage(im.resize((size,size),Image.Resampling.LANCZOS),master=root)
        images.append(photo)
        return photo
    variants = {
        'TButton': (WHITE,TINT,TINT_PRESSED),
        'Primary.TButton': (ACCENT,HOVER,PRESSED),
        'Accent.TButton': (ACCENT,HOVER,PRESSED),
        'Quiet.TButton': (BG,TINT,TINT_PRESSED),
        'Small.TButton': (WHITE,TINT,TINT_PRESSED),
        'Nav.TButton': (BG,WHITE,TINT),
        'SelectedNav.TButton': (TINT,TINT_HOVER,TINT_PRESSED),
        'Panel.Small.TButton': (BG,TINT,TINT_PRESSED),
        'Panel.Quiet.TButton': (WHITE,TINT,TINT_PRESSED),
    }
    for name, colors in variants.items():
        element = 'Round'+name.replace('.','')+'.border'
        background = '#ffffff' if name.startswith('Panel.') else BG
        normal,hover,pressed=(tile(c,background=background) for c in colors)
        disabled=tile('#f0f1f5',background=background)
        focus=tile(colors[0],background=background,outline=FOCUS)
        style.element_create(element,'image',normal,('disabled',disabled),('pressed',pressed),
                             ('active',hover),('focus',focus),border=(14,14),padding=0,sticky='nsew')
        style.layout(name,[(element,{'sticky':'nsew','children':[
            ('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})])
    panel = tile(WHITE,outline=BORDER,radius=16)
    style.element_create('RoundPanel.border','image',panel,border=(18,18),padding=0,sticky='nsew')
    style.layout('Rounded.TFrame',[('RoundPanel.border',{'sticky':'nsew','children':[
        ('Frame.padding',{'sticky':'nsew'})]})])
    style.configure('Rounded.TFrame',background='#ffffff')
    entry=tile(WHITE,outline=BORDER)
    entry_focus=tile(WHITE,outline=FOCUS)
    style.element_create('RoundEntry.field','image',entry,('focus',entry_focus),border=(14,14),padding=0,sticky='nsew')
    style.layout('TEntry',[('RoundEntry.field',{'sticky':'nsew','children':[
        ('Entry.padding',{'sticky':'nsew','children':[('Entry.textarea',{'sticky':'nsew'})]})]})])
    style.configure('TEntry',padding=(12,7),borderwidth=0,fieldbackground='#ffffff')
    # Keep image references alive for this interpreter; ttk stores image names.
    root.rounded_theme_images=images
