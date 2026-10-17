"""Launcher profile form with a reversible local avatar draft."""
from pathlib import Path
import json
import sys
import tempfile
import tkinter as tk
from tkinter import ttk

LIBRARY = Path(__file__).resolve().parent / 'launcher_lib'
if LIBRARY.is_dir() and str(LIBRARY) not in sys.path:
    sys.path.insert(0, str(LIBRARY))

from avatar_tools import load_image, make_preview_photo, pick_cropped_avatar, resolve_avatar_path

PROFILE_KEYS = ('name', 'signature', 'face_id', 'qq_face_url')

def profile_text(value, label, minimum, maximum):
    try:
        length = len(value.encode('utf-16-le')) // 2
    except UnicodeError:
        raise ValueError('%s包含无效字符。' % label) from None
    if not minimum <= length <= maximum or any(ord(c) < 32 for c in value):
        raise ValueError('%s需为 %d—%d 字，且不能包含换行或控制字符。' % (label,minimum,maximum))
    return value

def save_profile_draft(run_gm, uin, request, baseline):
    """Use the existing optimistic, atomic editor endpoint for profile-only edits."""
    payload={'fields':{},'expected':{},'profile':request,'profile_expected':dict(baseline)}
    ok,out=run_gm([str(uin),'editor',json.dumps(payload,ensure_ascii=False)])
    if not ok:
        return False,out.strip() or '保存失败，请稍后重试。'
    try:
        result=json.loads(out)
        if not isinstance(result,dict) or any(key not in result for key in PROFILE_KEYS):
            raise ValueError('Missing profile fields')
    except (ValueError,TypeError):
        return False,'资料保存结果无法确认，请重新打开编辑资料页面核对。'
    return True,result

class ProfileEditorPanel(tk.Frame):
    def __init__(self,master,player,game_root,theme,choose_file,center_window,on_save=None):
        self.theme=theme
        self.game_root=Path(game_root)
        self.player=dict(player)
        self.baseline={key:player.get(key) for key in PROFILE_KEYS}
        self.choose_file=choose_file
        self.center_window=center_window
        self.on_save=on_save
        self._temporary=None
        self._source_path=None
        self._custom_label=''
        self._closed=False
        self._preview_error=''
        super().__init__(master,bg=theme['PANEL'])
        self.name_var=tk.StringVar(master=self,value=player.get('name') or '')
        self.signature_var=tk.StringVar(master=self,value=player.get('signature') or '')
        self.avatar_var=tk.StringVar(master=self,value='保持当前头像')
        self.avatar_path=tk.StringVar(master=self,value='')
        from profile_editor import avatar_choices
        self.choices={'保持当前头像':'keep',**{label:face for face,label in avatar_choices(self.game_root)}}
        self._build()
        self.bind('<Destroy>',self._on_destroy,add='+')
        self.refresh_avatar()

    def _button(self,parent,text,command,primary=False):
        t=self.theme
        return tk.Button(parent,text=text,command=command,
            bg=t['ACCENT'] if primary else t['LOGBG'],fg='#12220a' if primary else t['FG'],
            activebackground=t['ACCENT_HOVER'] if primary else t['PANEL'],
            activeforeground='#12220a' if primary else t['FG'],
            relief='flat',cursor='hand2',font=('Segoe UI',10,'bold') if primary else ('Segoe UI',9),
            padx=10,pady=7,bd=0)

    def _build(self):
        t=self.theme
        for title,var in (('名称（2—7 字）',self.name_var),('签名（最多 30 字）',self.signature_var)):
            tk.Label(self,text=title,bg=t['PANEL'],fg=t['MUTED'],font=('Segoe UI',9)).pack(anchor='w',padx=14,pady=(10,3))
            tk.Entry(self,textvariable=var,bg=t['LOGBG'],fg=t['FG'],insertbackground=t['FG'],
                relief='flat',font=('Segoe UI',11)).pack(fill='x',padx=14,ipady=5)
        avatars=tk.Frame(self,bg=t['PANEL'])
        avatars.pack(fill='x',padx=14,pady=(16,8))
        self.current_preview=self._preview_box(avatars,'当前头像')
        self.selected_preview=self._preview_box(avatars,'保存后头像')
        controls=tk.Frame(self,bg=t['PANEL'])
        controls.pack(fill='x',padx=14,pady=4)
        self.avatar_box=ttk.Combobox(controls,textvariable=self.avatar_var,values=list(self.choices),
            state='readonly',style='Dark.TCombobox',font=('Segoe UI',10))
        self.avatar_box.pack(fill='x',pady=(0,8))
        self.avatar_box.bind('<<ComboboxSelected>>',self.refresh_avatar)
        buttons=tk.Frame(controls,bg=t['PANEL'])
        buttons.pack(fill='x')
        self.choose_button=self._button(buttons,'选择图片并裁切',self.choose_local)
        self.choose_button.pack(side='left',fill='x',expand=True,padx=(0,4))
        self.crop_button=self._button(buttons,'调整裁切',self.recrop)
        self.crop_button.pack(side='left',fill='x',expand=True,padx=(4,0))
        self.avatar_note=tk.Label(self,text='PNG / JPG · 最多 5 MiB',bg=t['PANEL'],fg=t['MUTED'],
            font=('Segoe UI',9),wraplength=440,justify='left')
        self.avatar_note.pack(anchor='w',padx=14,pady=(5,2))
        self.message=tk.Label(self,text='',bg=t['PANEL'],fg=t['MUTED'],font=('Segoe UI',9),
            wraplength=440,justify='left')
        self.message.pack(anchor='w',padx=14,pady=(0,3))
        self.save_button=self._button(self,'保存个人资料',self.save,True)
        self.save_button.pack(fill='x',padx=14,pady=(8,14))

    def _preview_box(self,parent,title):
        t=self.theme
        box=tk.Frame(parent,bg=t['PANEL'])
        box.pack(side='left',expand=True)
        tk.Label(box,text=title,bg=t['PANEL'],fg=t['MUTED'],font=('Segoe UI',9)).pack(pady=(0,5))
        border=tk.Frame(box,width=112,height=112,bg=t['LOGBG'],highlightthickness=1,
            highlightbackground=t['MUTED'])
        border.pack()
        border.pack_propagate(False)
        label=tk.Label(border,bg=t['LOGBG'],fg=t['MUTED'],text='暂无头像')
        label.pack(fill='both',expand=True)
        return label

    def _set_photo(self,label,path):
        if path:
            try:
                photo=make_preview_photo(load_image(path),master=self.winfo_toplevel(),size=104,checkerboard=True)
                label.configure(image=photo,text='')
                label.image=photo
                return
            except (OSError,ValueError,ImportError,RuntimeError,tk.TclError) as exc:
                self._preview_error='头像预览失败：%s' % exc
        label.configure(image='',text='暂无头像')
        label.image=None

    def refresh_avatar(self,_event=None):
        self._preview_error=''
        try:
            current=resolve_avatar_path(self.player,self.game_root)
        except (OSError,ValueError):
            current=None
        self._set_photo(self.current_preview,current)
        mode=self.choices.get(self.avatar_var.get(),'keep')
        if mode=='custom':
            selected=Path(self.avatar_path.get()) if self.avatar_path.get() else None
        elif mode=='keep':
            selected=current
        else:
            selected=resolve_avatar_path({'face_id':mode,'qq_face_url':''},self.game_root)
        self._set_photo(self.selected_preview,selected)
        self.crop_button.configure(state='normal' if mode=='custom' and self._source_path else 'disabled')
        self.avatar_note.configure(text=('已裁切：%s · 256 × 256' % self._custom_label if mode=='custom'
            else 'PNG / JPG · 最多 5 MiB · 选择后可拖动、缩放和旋转裁切'))
        if self._preview_error:
            self.message.configure(text=self._preview_error,fg=self.theme['STOP'])

    def _temp_folder(self):
        if self._temporary is None:
            self._temporary=tempfile.TemporaryDirectory(prefix='pvz-profile-avatar-')
        return Path(self._temporary.name)

    def choose_local(self):
        path=self.choose_file(self.winfo_toplevel())
        if not path:
            return
        pending=None
        try:
            # Stage the selected source privately so later re-cropping still works
            # if the user moves the original file. Cancellation preserves the draft.
            with open(path,'rb') as stream:
                data=stream.read(5*1024*1024+1)
            if not 0 < len(data) <= 5*1024*1024:
                raise ValueError('请选择不超过 5 MiB 的 PNG 或 JPG 图片。')
            with tempfile.NamedTemporaryFile(dir=self._temp_folder(),suffix=Path(path).suffix,delete=False) as stream:
                pending=Path(stream.name)
                stream.write(data)
            image=pick_cropped_avatar(self.winfo_toplevel(),pending,self.theme,self.center_window)
            if image is None:
                pending.unlink(missing_ok=True)
                return
            self._accept_crop(image,pending,Path(path).name)
        except (OSError,ValueError,ImportError,RuntimeError,tk.TclError) as exc:
            if pending: pending.unlink(missing_ok=True)
            self.message.configure(text=str(exc),fg=self.theme['STOP'])

    def _accept_crop(self,image,source_path,name):
        target=self._temp_folder()/'avatar.png'
        pending=self._temp_folder()/'avatar.new.png'
        try:
            image.save(pending,format='PNG')
            pending.replace(target)
        finally:
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                pass
        old=self._source_path
        self._source_path=Path(source_path)
        self._custom_label=name
        self.avatar_path.set(str(target))
        self.choices['自定义图片']='custom'
        self.avatar_box.configure(values=list(self.choices))
        self.avatar_var.set('自定义图片')
        if old and old!=self._source_path:
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
        self.message.configure(text='裁切已应用到预览，点击保存后生效。',fg=self.theme['MUTED'])
        self.refresh_avatar()

    def recrop(self):
        if not self._source_path:
            return
        try:
            image=pick_cropped_avatar(self.winfo_toplevel(),self._source_path,self.theme,self.center_window)
            if image is not None:
                self._accept_crop(image,self._source_path,self._custom_label)
        except (OSError,ValueError,ImportError,RuntimeError,tk.TclError) as exc:
            self.message.configure(text=str(exc),fg=self.theme['STOP'])

    def request(self,only_changed=True):
        name=self.name_var.get().strip()
        signature=self.signature_var.get()
        if name!=(self.baseline.get('name') or ''):
            profile_text(name,'名称',2,7)
        profile_text(signature,'签名',0,30)
        request={}
        for key,value in (('name',name),('signature',signature)):
            if not only_changed or value!=(self.baseline.get(key) or ''):
                request[key]=value
        mode=self.choices.get(self.avatar_var.get(),'keep')
        if mode!='keep' or not only_changed:
            request['avatar']=mode
            if mode=='custom':
                path=self.avatar_path.get()
                if not path or not Path(path).is_file():
                    raise ValueError('请先选择图片并完成裁切。')
                request['image_path']=path
        return request

    def save(self):
        if not self.on_save:
            return False
        try:
            request=self.request()
        except ValueError as exc:
            self.message.configure(text=str(exc),fg=self.theme['STOP'])
            return False
        if not request:
            self.message.configure(text='资料没有变更。',fg=self.theme['MUTED'])
            return True
        self.save_button.configure(state='disabled')
        try:
            ok,result=self.on_save(request,dict(self.baseline))
        except Exception as exc:
            ok,result=False,str(exc)
        finally:
            self.save_button.configure(state='normal')
        if not ok:
            self.message.configure(text='保存失败：%s' % result,fg=self.theme['STOP'])
            return False
        self.reset_from_player(result)
        self.message.configure(text='个人资料已保存，下次进入游戏时生效。',fg=self.theme['ACCENT'])
        return True

    def reset_from_player(self,player):
        self.player.update(player)
        self.baseline.update({key:player.get(key) for key in PROFILE_KEYS})
        self.name_var.set(player.get('name') or '')
        self.signature_var.set(player.get('signature') or '')
        self.avatar_var.set('保持当前头像')
        self.avatar_path.set('')
        self._source_path=None
        self._custom_label=''
        self.choices.pop('自定义图片',None)
        self.avatar_box.configure(values=list(self.choices))
        if self._temporary:
            self._temporary.cleanup()
            self._temporary=None
        self.refresh_avatar()

    def _on_destroy(self,event):
        if event.widget is self and not self._closed:
            self._closed=True
            if self._temporary:
                self._temporary.cleanup()
                self._temporary=None

def open_profile_window(parent,player,game_root,theme,choose_file,center_window,on_save):
    win=tk.Toplevel(parent)
    win.withdraw()
    win.title('编辑资料 — 账号 %s' % player['uin'])
    win._account_uin=int(player['uin'])
    win.configure(bg=theme['BG'])
    win.transient(parent)
    win.minsize(440,380)
    tk.Label(win,text='编辑资料',bg=theme['BG'],fg=theme['FG'],font=('Segoe UI',15,'bold')).pack(anchor='w',padx=20,pady=(16,3))
    tk.Label(win,text='保存名称、签名和头像，下次进入游戏时生效。',bg=theme['BG'],fg=theme['MUTED'],font=('Segoe UI',9)).pack(anchor='w',padx=20,pady=(0,12))
    tk.Button(win,text='关闭',command=win.destroy,bg=theme['PANEL'],fg=theme['FG'],
        activebackground=theme['LOGBG'],relief='flat',cursor='hand2',font=('Segoe UI',9),padx=20,pady=7).pack(side='bottom',anchor='e',padx=20,pady=(0,16))
    viewport=tk.Frame(win,bg=theme['BG'])
    viewport.pack(fill='both',expand=True,padx=8,pady=(0,12))
    canvas=tk.Canvas(viewport,bg=theme['BG'],highlightthickness=0)
    scrollbar=ttk.Scrollbar(viewport,orient='vertical',command=canvas.yview)
    scrollbar.pack(side='right',fill='y')
    canvas.pack(side='left',fill='both',expand=True)
    canvas.configure(yscrollcommand=scrollbar.set)
    body=tk.Frame(canvas,bg=theme['BG'])
    content=canvas.create_window(0,0,window=body,anchor='nw')
    body.bind('<Configure>',lambda event:canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.bind('<Configure>',lambda event:canvas.itemconfigure(content,width=event.width))
    win.bind('<MouseWheel>',lambda event:canvas.yview_scroll(-int(event.delta/120),'units'))
    win.bind('<Button-4>',lambda event:canvas.yview_scroll(-1,'units'))
    win.bind('<Button-5>',lambda event:canvas.yview_scroll(1,'units'))
    panel=ProfileEditorPanel(body,player,game_root,theme,choose_file,center_window,on_save)
    panel.pack(fill='both',expand=True,padx=12,pady=(0,4))
    win.profile_panel=panel
    win.profile_canvas=canvas
    win.update_idletasks()
    # Use a wide enough form while retaining accessibility on small displays.
    width=520
    height=min(panel.winfo_reqheight()+152,max(380,win.winfo_screenheight()-120))
    center_window(win,parent,(width,height))
    win.deiconify()
    return win
