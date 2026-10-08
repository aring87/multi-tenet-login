"""Shared visual tokens for the Windows desktop interface."""
from tkinter import ttk

COLORS = dict(surface="#f2f5f8", card="#ffffff", ink="#172b43", muted="#56677b",
              line="#dce4ec", accent="#0f766e", accent_hover="#115e59", sidebar="#142638")


def configure_theme(root):
    c=COLORS
    root.configure(bg=c["surface"])
    root.option_add("*TCombobox*Listbox.font", "{Segoe UI} 10")
    style=ttk.Style(root);style.theme_use("clam")
    style.configure(".",font=("Segoe UI",10),foreground=c["ink"],background=c["surface"])
    style.configure("TFrame",background=c["surface"])
    style.configure("Card.TFrame",background=c["card"])
    style.configure("Card.TLabelframe",background=c["card"],bordercolor=c["line"],relief="solid",borderwidth=1)
    style.configure("Card.TLabelframe.Label",background=c["card"],foreground=c["ink"],font=("Segoe UI",10,"bold"))
    for name,size,weight,color,bg in (
        ("CardTitle",14,"bold",c["ink"],c["card"]),
        ("Field",10,"bold",c["ink"],c["card"]),
        ("Muted",10,"normal",c["muted"],c["card"]),
        ("Section",10,"bold",c["accent"],c["card"]),
        ("Context",10,"normal",c["muted"],c["surface"]),
        ("Badge",9,"bold",c["accent"],"#e2f1ee")):
        style.configure(name+".TLabel",font=("Segoe UI",size,weight),foreground=color,background=bg)
    for name in ("TEntry","TCombobox"):
        style.configure(name,fieldbackground=c["card"],background=c["card"],bordercolor=c["line"],
                        lightcolor=c["line"],darkcolor=c["line"],padding=8,arrowsize=14)
        style.map(name,bordercolor=[("focus",c["accent"])],
                  fieldbackground=[("disabled","#edf1f5"),("readonly",c["card"])],
                  foreground=[("disabled","#6f7e8e")],selectbackground=[("readonly","#dbeeea")],
                  selectforeground=[("readonly",c["ink"])])
    for name,bg,fg,hover in (("Primary",c["accent"],"white",c["accent_hover"]),
                             ("Secondary","#edf2f6",c["ink"],"#e0e8ef"),
                             ("Danger","#fff1f1","#a12b36","#ffe1e4"),
                             ("Disclosure",c["card"],c["accent"],"#edf7f4")):
        style.configure(name+".TButton",padding=(14,10),background=bg,foreground=fg,
                        borderwidth=1,bordercolor=bg,lightcolor=bg,darkcolor=bg,
                        focusthickness=2,focuscolor=fg,font=("Segoe UI",10,"bold"),anchor="w" if name=="Disclosure" else "center")
        style.map(name+".TButton",background=[("disabled","#e5ebef"),("pressed",hover),("active",hover)],
                  foreground=[("disabled","#748291")],bordercolor=[("focus",c["accent"]),("active",hover)])
    style.configure("TButton",padding=(12,8))
    style.configure("TCheckbutton",background=c["card"],padding=(0,5),focuscolor=c["accent"])
    style.map("TCheckbutton",background=[("active",c["card"])],foreground=[("disabled","#748291")])
    style.configure("Hidden.TNotebook",background=c["surface"],borderwidth=0,tabmargins=0,
                    bordercolor=c["surface"],lightcolor=c["surface"],darkcolor=c["surface"])
    style.layout("Hidden.TNotebook.Tab",[])
    style.configure("Horizontal.TProgressbar",background=c["accent"],troughcolor="#e3eaf0",borderwidth=0,thickness=3,
                    bordercolor=c["surface"],lightcolor=c["accent"],darkcolor=c["accent"])
    style.configure("Treeview",background=c["card"],fieldbackground=c["card"],rowheight=34,borderwidth=0)
    style.configure("Treeview.Heading",background="#eaf0f5",font=("Segoe UI",10,"bold"),padding=(10,9),relief="flat")
    style.map("Treeview",background=[("selected","#dbeeea")],foreground=[("selected",c["ink"])])
    style.configure("Vertical.TScrollbar",background="#c6d1dd",troughcolor=c["surface"],borderwidth=0,arrowsize=12)
    return style
