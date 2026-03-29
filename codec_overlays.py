"""CODEC Overlays — tkinter popup overlays for status, recording, toggle"""
import subprocess
import sys


def show_overlay(text, color="#E8711A", duration=2500):
    d = f"root.after({duration}, root.destroy)" if duration else ""
    s = f"""
import tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,84
x=(sw-w)//2
y=sh-130
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
c=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
c.pack()
c.create_rectangle(1,1,w-1,h-1,outline='{color}',width=1)
c.create_text(w//2,h//2,text='{text}',fill='{color}',font=('Helvetica',13))
{d}
root.mainloop()
"""
    subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_recording_overlay(key_label="F18"):
    s = """
import tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,84
x=(sw-w)//2
y=sh-130
root.geometry(f'{w}x{h}+{x}+{y}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline='#E8711A',width=1)
dot=cv.create_oval(14,29,27,42,fill='#ff3b3b',outline='')
cv.create_text(w//2+8,42,text='\U0001f3a4  Recording — release """ + key_label + """ to send',fill='#eeeeee',font=('Helvetica',13))
on=[True]
def pulse():
    on[0]=not on[0]
    cv.itemconfig(dot,fill='#ff3b3b' if on[0] else '#550000')
    root.after(400,pulse)
pulse()
root.mainloop()
"""
    return subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_processing_overlay(text="Transcribing...", duration=4000):
    s = f"""
import tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,84
x=(sw-w)//2
y=sh-130
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline='#00aaff',width=1)
cv.create_text(w//2,h//2,text='\u26a1 {text}',fill='#00aaff',font=('Helvetica',13))
root.after({duration},root.destroy)
root.mainloop()
"""
    subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def show_toggle_overlay(is_on, shortcuts=""):
    color = '#E8711A' if is_on else '#ff3333'
    label = 'C O D E C' if is_on else 'S I G N I N G   O U T'
    dur = 3000 if is_on else 1500
    import threading
    snd = '/System/Library/Sounds/Blow.aiff' if is_on else '/System/Library/Sounds/Funk.aiff'
    threading.Thread(
        target=lambda: subprocess.run(['afplay', snd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
        daemon=True
    ).start()
    s = f"""
import tkinter as tk
root=tk.Tk()
root.overrideredirect(True)
root.attributes('-topmost',True)
root.attributes('-alpha',0.95)
root.configure(bg='#0a0a0a')
sw=root.winfo_screenwidth()
sh=root.winfo_screenheight()
w,h=440,84
x=(sw-w)//2
y=sh-130
root.geometry(f'{{w}}x{{h}}+{{x}}+{{y}}')
cv=tk.Canvas(root,bg='#0a0a0a',highlightthickness=0,width=w,height=h)
cv.pack()
cv.create_rectangle(1,1,w-1,h-1,outline='{color}',width=1)
cv.create_text(w//2,39 if not '{shortcuts}' else 24,text='{label}',fill='{color}',font=('Helvetica',18,'bold'))
if '{shortcuts}': cv.create_text(w//2,55,text='{shortcuts}',fill='#aaaaaa',font=('Helvetica',13))
root.after({dur},root.destroy)
root.mainloop()
"""
    subprocess.Popen([sys.executable, "-c", s], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
