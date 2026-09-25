"""Sentinel Client Onboarding: local Windows desktop interface."""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import ttk, messagebox, filedialog, simpledialog
from desktop_backend import (BASE, DATA, Session, Stop, require, guid, fingerprint,
                             config_from_workspace, permission_run, full_run, validate_tenant_hint, CYBERQP_PORTALS)
from full_onboarding import validate_config

class App:
    def __init__(self, root):
        self.root = root
        root.title("Azure Workspace Discovery")
        root.geometry("1280x880")
        root.minsize(1120,740)
        root.configure(bg="#edf2f7")
        DATA.mkdir(exist_ok=True)
        self.events = queue.Queue()
        self.busy = False
        self.session = None
        self.auth_hint = None
        self.subscriptions = []
        self.workspaces = []
        self.workspace = None
        self.plan = None
        self.extras = {}
        self.clientfile = DATA / "clients.json"
        self.clients = self.read_clients()
        self.vars = {}
        self.controls = []
        self.build()
        self.refresh_clients()
        self.root.after(100,self.poll)
        self.root.protocol("WM_DELETE_WINDOW",self.close)

    def read_clients(self):
        if self.clientfile.exists():
            try:
                clients=json.loads(self.clientfile.read_text(encoding="utf-8"))
                require(isinstance(clients,list), "Invalid saved client inventory.")
                for c in clients:
                    require(set(c)=={"name","slug","tenant"} and all(isinstance(v,str) for v in c.values()),
                            "Invalid saved client entry.")
                return clients
            except Exception as error:
                messagebox.showerror("Client inventory",str(error))
                return []
        return []

    def var(self,name,default=""):
        self.vars[name]=tk.StringVar(value=default)
        return self.vars[name]

    def button(self, parent, text, command, style="Secondary.TButton"):
        w=ttk.Button(parent,text=text,command=command,style=style)
        self.controls.append((w,"normal"))
        return w

    def form(self, parent, label, name, default="", values=None):
        box=ttk.Frame(parent,style="Card.TFrame")
        box.pack(fill="x",pady=(0,14))
        ttk.Label(box,text=label,style="Field.TLabel").pack(anchor="w",pady=(0,6))
        v=self.var(name,default)
        if values is None:
            w=ttk.Entry(box,textvariable=v)
        else:
            w=ttk.Combobox(box,textvariable=v,values=values,state="readonly")
        w.pack(fill="x")
        self.controls.append((w,"readonly" if values is not None else "normal"))
        return w

    def card(self, parent, title, subtitle=""):
        outer=ttk.Frame(parent,style="Card.TFrame",padding=22)
        ttk.Label(outer,text=title,style="CardTitle.TLabel").pack(anchor="w")
        if subtitle:
            ttk.Label(outer,text=subtitle,style="Muted.TLabel",wraplength=330).pack(anchor="w",pady=(5,18))
        else:
            ttk.Frame(outer,style="Card.TFrame",height=16).pack()
        body=ttk.Frame(outer,style="Card.TFrame")
        body.pack(fill="both",expand=True)
        return outer,body

    def build(self):
        self.root.configure(bg="#f3f5f9")
        style=ttk.Style()
        style.theme_use("clam")
        style.configure(".",font=("Segoe UI",10),foreground="#233248")
        style.configure("TFrame",background="#f3f5f9")
        style.configure("Card.TFrame",background="#ffffff")
        style.configure("TLabel",background="#f3f5f9")
        style.configure("CardTitle.TLabel",font=("Segoe UI",13,"bold"),background="white",foreground="#13243b")
        style.configure("Field.TLabel",font=("Segoe UI",10,"bold"),background="white",foreground="#35455d")
        style.configure("Muted.TLabel",background="white",foreground="#66758a",font=("Segoe UI",10))
        style.configure("TEntry",fieldbackground="white",bordercolor="#d7dee8",lightcolor="#d7dee8",
                        darkcolor="#d7dee8",padding=9)
        style.configure("TCombobox",fieldbackground="white",background="white",bordercolor="#d7dee8",
                        lightcolor="#d7dee8",darkcolor="#d7dee8",padding=8,arrowsize=14)
        style.map("TCombobox",fieldbackground=[("readonly","white")],selectbackground=[("readonly","#e9f0ff")],
                  selectforeground=[("readonly","#233248")])
        style.configure("Secondary.TButton",padding=(13,9),background="#f1f4f8",foreground="#33465f",
                        borderwidth=0,font=("Segoe UI",10,"bold"))
        style.map("Secondary.TButton",background=[("active","#e4eaf3"),("disabled","#f0f2f5")],
                  foreground=[("disabled","#9ca7b7")])
        style.configure("Primary.TButton",padding=(16,10),background="#245de9",foreground="white",
                        borderwidth=0,font=("Segoe UI",10,"bold"))
        style.map("Primary.TButton",background=[("active","#174acb"),("disabled","#c4d0ee")],
                  foreground=[("disabled","#f5f7fc")])
        style.configure("Danger.TButton",padding=(12,9),background="#fff0f0",foreground="#b83741",borderwidth=0)
        style.map("Danger.TButton",background=[("active","#ffe0e2")])
        style.configure("TCheckbutton",background="white")
        style.configure("Hidden.TNotebook",background="#f3f5f9",borderwidth=0,tabmargins=0)
        style.layout("Hidden.TNotebook.Tab",[])
        style.configure("Horizontal.TProgressbar",background="#245de9",troughcolor="#e8edf5",borderwidth=0)
        sidebar=tk.Frame(self.root,bg="#12213a",width=205)
        sidebar.pack(side="left",fill="y");sidebar.pack_propagate(False)
        tk.Label(sidebar,text="SENTINEL",bg="#12213a",fg="#ffffff",font=("Segoe UI",18,"bold")).pack(anchor="w",padx=24,pady=(34,4))
        tk.Label(sidebar,text="WORKSPACE DISCOVERY",bg="#12213a",fg="#8ca5c5",font=("Segoe UI",9,"bold")).pack(anchor="w",padx=24,pady=(0,38))
        self.nav=[]
        for i,(title,desc) in enumerate([("Discover","Sign in & copy details"),("Configure","Permissions & identities"),("Review","Preview & apply")]):
            b=tk.Button(sidebar,text=f"0{i+1}   {title}\n       {desc}",justify="left",anchor="w",
                        command=lambda n=i:self.tabs.select(n),font=("Segoe UI",10),relief="flat",bd=0,
                        bg="#12213a",fg="#a9bad1",activebackground="#203b60",activeforeground="white",padx=18,pady=15)
            b.pack(fill="x",padx=12,pady=4);self.nav.append(b)
        bottom=tk.Frame(sidebar,bg="#12213a");bottom.pack(side="bottom",fill="x",padx=24,pady=24)
        tk.Label(bottom,text="LOCAL WORKSPACE",bg="#12213a",fg="#738daa",font=("Segoe UI",8,"bold")).pack(anchor="w")
        tk.Label(bottom,text="Your approved Azure access",bg="#12213a",fg="#b9c8da",font=("Segoe UI",9)).pack(anchor="w",pady=(6,16))
        tk.Button(bottom,text="Help & prerequisites",command=lambda:os.startfile(str(BASE/"DESKTOP-START-HERE.md")),
                  bg="#12213a",fg="#a9c5ff",activebackground="#203b60",relief="flat",anchor="w",bd=0).pack(anchor="w")
        tk.Button(bottom,text="Open app data",command=lambda:os.startfile(str(DATA)),
                  bg="#12213a",fg="#a9c5ff",activebackground="#203b60",relief="flat",anchor="w",bd=0).pack(anchor="w",pady=(8,0))
        main=ttk.Frame(self.root);main.pack(side="left",fill="both",expand=True)
        head=ttk.Frame(main,padding=(30,26,30,18));head.pack(fill="x")
        self.page_title=tk.StringVar(value="Connect your client")
        self.page_subtitle=tk.StringVar(value="Discover the right workspace without navigating the Azure portal.")
        ttk.Label(head,textvariable=self.page_title,font=("Segoe UI",23,"bold"),foreground="#14263e").pack(anchor="w")
        ttk.Label(head,textvariable=self.page_subtitle,foreground="#6a7890").pack(anchor="w",pady=(7,0))
        self.status=tk.StringVar(value="Ready. Use an authorized Azure account to connect a client.")
        statusframe=ttk.Frame(main,padding=(30,10));statusframe.pack(side="bottom",fill="x")
        ttk.Label(statusframe,textvariable=self.status,foreground="#61728a",wraplength=920).pack(anchor="w")
        self.progress=ttk.Progressbar(statusframe,mode="indeterminate",maximum=100)
        self.progress.pack(fill="x",pady=(8,0))
        self.tabs=ttk.Notebook(main,style="Hidden.TNotebook");self.tabs.pack(fill="both",expand=True,padx=26,pady=(0,12))
        self.page_contents=[]
        self.page_canvases=[]
        for name in ("Connect","Configure","Review"):
            page=ttk.Frame(self.tabs);self.tabs.add(page,text=name)
            canvas=tk.Canvas(page,bg="#f3f5f9",highlightthickness=0,bd=0)
            scroll=ttk.Scrollbar(page,orient="vertical",command=canvas.yview)
            scroll.pack(side="right",fill="y");canvas.pack(side="left",fill="both",expand=True)
            canvas.configure(yscrollcommand=scroll.set)
            self.page_canvases.append(canvas)
            content=ttk.Frame(canvas);window=canvas.create_window((0,0),window=content,anchor="nw")
            content.bind("<Configure>",lambda e,c=canvas:c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>",lambda e,c=canvas,w=window:c.itemconfigure(w,width=e.width))
            self.page_contents.append(content)
        connect,settings,review=self.page_contents
        connect.columnconfigure(0,weight=1,uniform="discovery");connect.columnconfigure(1,weight=1,uniform="discovery")
        clientcard,body=self.card(connect,"Client profile (optional)","Sign in without creating a profile. Save a client later for quicker access.")
        clientcard.grid(row=0,column=0,sticky="nsew",padx=(0,16),pady=(0,16))
        self.clientbox=self.form(body,"Saved client","client_name",values=[])
        self.clientbox.bind("<<ComboboxSelected>>",self.client_changed)
        actions=ttk.Frame(body,style="Card.TFrame");actions.pack(fill="x",pady=(0,18))
        self.button(actions,"+ Add client",self.add_client).pack(side="left",padx=(0,8))
        self.button(actions,"Delete client",self.delete_client,"Danger.TButton").pack(side="left")
        self.form(body,"Tenant ID or domain (optional)","tenant")
        self.form(body,"Client slug","client_slug")
        ttk.Label(body,text="Leave the tenant field blank to sign in without an ID. A saved tenant restricts sign-in to that directory. Clear it when switching to a new client.",
                  style="Muted.TLabel",wraplength=300).pack(anchor="w",pady=(0,20))
        self.button(body,"Save client",self.save_client).pack(fill="x",pady=(0,10))
        self.button(body,"Import configuration",self.import_config).pack(fill="x")
        details_body=body
        discovery,body=self.card(connect,"Azure workspace","Connect with the client's authorized Azure account.")
        discovery.grid(row=0,column=1,sticky="nsew",pady=(0,16))
        self.form(body,"CyberQP region - optional","cyberqp_region","US",list(CYBERQP_PORTALS))
        self.button(body,"Sign in to CyberQP",self.open_cyberqp).pack(anchor="w",pady=(0,10))
        ttk.Label(body,text="Opens your browser. Activate JIT access there, then return here.",
                  style="Muted.TLabel",wraplength=330).pack(anchor="w",pady=(0,16))
        self.button(body,"Open Azure portal",self.open_azure_portal).pack(anchor="w",pady=(0,10))
        ttk.Label(body,text="Portal access is separate. Connect the app below to discover workspaces.",
                  style="Muted.TLabel",wraplength=330).pack(anchor="w",pady=(0,16))
        self.form(body,"App sign-in method","login_method","Browser",["Browser","Windows account window"])
        self.button(body,"Sign in & discover",self.login,"Primary.TButton").pack(anchor="w",pady=(0,18))
        self.subbox=self.form(body,"Subscription","subscription",values=[])
        self.subbox.bind("<<ComboboxSelected>>",self.subscription_changed)
        self.button(body,"Refresh subscriptions",self.refresh_subscriptions).pack(anchor="w",pady=(0,8))
        self.button(body,"Check missing subscription",self.check_subscription).pack(anchor="w",pady=(0,12))
        self.button(body,"Refresh workspaces",self.discover).pack(anchor="w",pady=(0,18))
        self.wsbox=self.form(body,"Log Analytics workspace","workspace",values=[])
        self.wsbox.bind("<<ComboboxSelected>>",self.workspace_changed)
        self.identity=tk.StringVar(value="Workspace details will appear here after you sign in and discover.")
        body=details_body
        ttk.Label(body,text="Workspace details",style="Field.TLabel").pack(anchor="w",pady=(2,6))
        self.details=tk.Text(body,width=1,height=10,wrap="word",font=("Consolas",10),
                             bg="#f6f8fc",fg="#233248",relief="flat",padx=12,pady=12)
        self.details.pack(fill="x",pady=(0,12))
        self.identity.trace_add("write",lambda *args:self.render_details())
        self.render_details()
        self.button(body,"Copy workspace details",self.copy_workspace).pack(anchor="w")
        self.button(connect,"Advanced: permissions & onboarding  >",lambda:self.tabs.select(1),"Primary.TButton").grid(
            row=1,column=1,sticky="e",pady=(0,16))
        top,body=self.card(settings,"Setup scope","Choose what you want to configure for this workspace.")
        top.pack(fill="x",pady=(0,16))
        modebox=self.form(body,"Operation","mode","Permissions only",["Permissions only","Full Azure + GitHub onboarding"])
        modebox.bind("<<ComboboxSelected>>",lambda e:self.update_mode())
        self.form(body,"Workspace identity label","label","primary")
        ttk.Label(body,text="The primary label uses workspace.yml. Additional labels keep separate workspace files and targets.",
                  style="Muted.TLabel",wraplength=810).pack(anchor="w")
        self.permissioncard,body=self.card(settings,"Existing application permissions","Use the enterprise application's Object ID for each identity.")
        self.permissioncard.pack(fill="x",pady=(0,16))
        self.form(body,"Preview service principal Object ID","preview_principal")
        self.form(body,"Deployment service principal Object ID","deploy_principal")
        self.fullcard,body=self.card(settings,"Azure & GitHub onboarding","Configure separate identities, environments and a client pull request.")
        self.form(body,"GitHub organization","github_owner","")
        self.form(body,"GitHub repository","github_repo","")
        self.form(body,"App owner user Object IDs - comma separated","app_owners")
        self.form(body,"Production reviewer type","reviewer_type","User",["User","Team"])
        self.form(body,"Reviewer names - comma separated","reviewers")
        self.form(body,"OIDC subject format","oidc_subject_format","",["immutable","legacy"])
        self.form(body,"Initial rule path - optional","rule_path")
        self.vars["allow_missing_mitre"]=tk.BooleanVar(value=False)
        cb=ttk.Checkbutton(body,text="Allow missing MITRE metadata (approved exception)",variable=self.vars["allow_missing_mitre"])
        cb.pack(anchor="w",pady=(0,16));self.controls.append((cb,"normal"))
        row=ttk.Frame(body,style="Card.TFrame");row.pack(fill="x")
        self.button(row,"Sign in to GitHub",self.github_login).pack(side="left",padx=(0,10))
        self.button(row,"Export configuration",self.export_config).pack(side="left")
        for key in ("existing_preview_app_client_id","existing_deploy_app_client_id"):self.var(key)
        self.configfooter=ttk.Frame(settings)
        self.configfooter.pack(fill="x",pady=(0,16))
        self.button(self.configfooter,"Continue to review  >",lambda:self.tabs.select(2),"Primary.TButton").pack(side="right")
        reviewcard,body=self.card(review,"Deployment review","Preview the selected configuration before applying changes.")
        reviewcard.pack(fill="x",pady=(0,16))
        self.summary=tk.StringVar(value="Select a workspace and configure the setup to create a preview.")
        ttk.Label(body,textvariable=self.summary,style="Muted.TLabel",wraplength=820,justify="left").pack(anchor="w",pady=(0,20))
        actions=ttk.Frame(body,style="Card.TFrame");actions.pack(fill="x")
        self.button(actions,"Preview setup",lambda:self.onboard(False),"Primary.TButton").pack(side="left",padx=(0,10))
        self.button(actions,"Apply reviewed setup",lambda:self.onboard(True)).pack(side="left",padx=(0,10))
        self.button(actions,"Sign out",self.logout).pack(side="right")
        logcard,body=self.card(review,"Activity","Checks, deployment results and next steps appear here.")
        logcard.pack(fill="both",expand=True,pady=(0,16))
        logframe=ttk.Frame(body,style="Card.TFrame");logframe.pack(fill="both",expand=True)
        self.logbox=tk.Text(logframe,wrap="word",font=("Consolas",10),bg="#f6f8fc",fg="#354863",
                            height=16,state="disabled",bd=0,padx=14,pady=14,highlightthickness=0)
        logscroll=ttk.Scrollbar(logframe,orient="vertical",command=self.logbox.yview)
        logscroll.pack(side="right",fill="y");self.logbox.pack(fill="both",expand=True)
        self.logbox.configure(yscrollcommand=logscroll.set)
        self.tabs.bind("<<NotebookTabChanged>>",self.page_changed)
        self.page_changed()
        self.root.bind("<MouseWheel>",self.scroll_page)

    def scroll_page(self,event):
        if event.widget.winfo_class() in ("Text","TCombobox"):
            return
        self.page_canvases[self.tabs.index("current")].yview_scroll(int(-event.delta/120),"units")

    def page_changed(self,event=None):
        index=self.tabs.index("current")
        titles=["Connect your client","Configure workspace setup","Review & apply"]
        subtitles=["Discover the right workspace without navigating the Azure portal.",
                   "Set up permissions for existing identities, or onboard a new client.",
                   "Confirm the destination, preview the setup, then apply."]
        self.page_title.set(titles[index]);self.page_subtitle.set(subtitles[index])
        for i,button in enumerate(self.nav):
            button.configure(bg="#223f67" if i==index else "#12213a",fg="white" if i==index else "#a9bad1")

    def update_mode(self):
        if self.vars["mode"].get()=="Permissions only":
            self.fullcard.pack_forget()
            self.permissioncard.pack(fill="x",pady=(0,16),before=self.configfooter)
        else:
            self.permissioncard.pack_forget()
            self.fullcard.pack(fill="x",pady=(0,16),before=self.configfooter)

    def delete_client(self):
        if self.busy:
            return
        name=self.vars["client_name"].get()
        match=next((c for c in self.clients if c["name"]==name),None)
        if match is None:
            messagebox.showinfo("Delete client","Choose a saved client first.",parent=self.root)
            return
        if not messagebox.askyesno("Delete saved client",
                f"Remove {name} from this app's saved clients?\n\n"
                "Azure, GitHub and CyberQP resources will not be deleted. Local onboarding state is retained.",
                parent=self.root):
            return
        remaining=[c for c in self.clients if c["name"]!=name]
        try:
            temp=self.clientfile.with_suffix(".tmp")
            temp.write_text(json.dumps(remaining,indent=2),encoding="utf-8")
            temp.replace(self.clientfile)
        except OSError as error:
            messagebox.showerror("Could not delete client",str(error),parent=self.root)
            return
        self.clients=remaining
        self.auth_hint=None
        self.clear_selection()
        for key in ("client_name","tenant","client_slug","preview_principal","deploy_principal","app_owners",
                    "existing_preview_app_client_id","existing_deploy_app_client_id"):
            self.vars[key].set("")
        self.extras={}
        self.refresh_clients()
        self.summary.set("Select a workspace and create a new preview.")
        self.status.set(f"Removed {name} from saved clients. Cloud resources and onboarding state were retained.")

    def log(self,message):
        self.events.put(("log",str(message)))

    def poll(self):
        try:
            while True:
                kind,data=self.events.get_nowait()
                if kind=="log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end",data+"\n"); self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                elif kind=="done":
                    callback,result,error=data
                    self.set_busy(False)
                    if error:
                        self.plan=None
                        self.status.set("Stopped - see the details in Review & apply.")
                        self.log(error); self.tabs.select(2)
                        messagebox.showerror("Operation stopped",error)
                    else:
                        self.status.set("Ready")
                        if callback: callback(result)
        except queue.Empty: pass
        self.root.after(100,self.poll)

    def set_busy(self,busy):
        self.busy=busy
        self.progress.start(12) if busy else self.progress.stop()
        for widget,state in self.controls:
            widget.configure(state="disabled" if busy else state)

    def work(self,title,task,done=None):
        if self.busy: return
        self.set_busy(True); self.status.set(title); self.log(title)
        def run():
            try: result,error=task(),None
            except Exception as ex: result,error=None,str(ex)
            self.events.put(("done",(done,result,error)))
        threading.Thread(target=run,daemon=True).start()

    def values(self):
        return {k:v.get().strip() if isinstance(v.get(),str) else v.get() for k,v in self.vars.items()}

    def refresh_clients(self):
        self.clientbox.configure(values=[c["name"] for c in self.clients])
        if self.clients:
            self.vars["client_name"].set(self.clients[0]["name"])
            self.client_changed()

    def client_changed(self,event=None):
        self.clear_selection()
        for c in self.clients:
            if c["name"]==self.vars["client_name"].get():
                self.vars["tenant"].set(c["tenant"]); self.vars["client_slug"].set(c["slug"])
                break

    def clear_selection(self):
        self.subscriptions=[]; self.workspaces=[]; self.workspace=None; self.plan=None
        self.subbox.configure(values=[]); self.wsbox.configure(values=[])
        self.vars["subscription"].set(""); self.vars["workspace"].set("")
        self.identity.set("No authenticated workspace selected.")
        # The old session is not reused for another client's login.

    def add_client(self):
        name=simpledialog.askstring("Add client","Client display name:",parent=self.root)
        if not name: return
        tenant=simpledialog.askstring("Add client","Tenant ID or domain (optional; leave blank to discover after sign-in):",parent=self.root)
        if tenant is None: return
        slug=simpledialog.askstring("Add client","Lowercase client slug:",initialvalue=re.sub(r"[^a-z0-9]+","-",name.lower()).strip("-"),parent=self.root)
        if not slug: return
        self.vars["client_name"].set(name); self.vars["tenant"].set(tenant); self.vars["client_slug"].set(slug)
        self.clear_selection(); self.save_client()

    def save_client(self):
        v=self.values()
        try:
            v["tenant"] = validate_tenant_hint(v["tenant"]) if v["tenant"] else ""
        except Stop as error:
            messagebox.showerror("Tenant details",str(error),parent=self.root)
            return
        self.vars["tenant"].set(v["tenant"])
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,47}",v["client_slug"]) or not v["client_name"] :
            messagebox.showerror("Client details","Supply a display name and lowercase client slug. Tenant is optional."); return
        entry=dict(name=v["client_name"],slug=v["client_slug"],tenant=v["tenant"])
        self.clients=[c for c in self.clients if c["name"]!=entry["name"]]+[entry]
        self.clientfile.write_text(json.dumps(self.clients,indent=2),encoding="utf-8")
        self.clientbox.configure(values=[c["name"] for c in self.clients])
        self.status.set("Client mapping saved. Credentials are not part of the client inventory.")

    def open_azure_portal(self):
        try:
            if not webbrowser.open("https://portal.azure.com/",new=2):
                raise RuntimeError("Could not open the browser. Open https://portal.azure.com/ manually.")
            self.status.set("Azure portal opened. Use Sign in & discover to load workspace details into this app.")
        except Exception as error:
            messagebox.showerror("Open Azure portal",str(error),parent=self.root)

    def open_cyberqp(self):
        region=self.vars["cyberqp_region"].get()
        url=CYBERQP_PORTALS.get(region)
        if not url:
            messagebox.showerror("CyberQP region","Choose US, EU, or Canada.",parent=self.root)
            return
        try:
            if not webbrowser.open(url,new=2):
                raise RuntimeError("Could not open the browser. Open " + url + " manually.")
            self.status.set("CyberQP portal opened. Activate access there, then return to Sign in & discover.")
        except Exception as error:
            messagebox.showerror("Open CyberQP",str(error),parent=self.root)

    def login(self):
        try:
            value=self.vars["tenant"].get().strip()
            hint=validate_tenant_hint(value) if value else ""
        except Stop as error:
            messagebox.showerror("Tenant details",str(error),parent=self.root)
            return
        self.vars["tenant"].set(hint)
        self.clear_selection()
        self.auth_hint=hint
        old=self.session
        self.session=Session(logger=self.log)
        self.session.env["AZURE_CORE_ENABLE_BROKER_ON_WINDOWS"] = (
            "true" if self.vars["login_method"].get()=="Windows account window" else "false")
        def task():
            if old:
                old.logout()
            return self.session.login(hint)
        def done(rows):
            self.subscriptions=rows
            self.subbox.configure(values=[r["name"]+" | "+r["id"]+" | Tenant: "+r["tenantId"] for r in rows])
            self.subbox.current(next((i for i,r in enumerate(rows) if r.get("isDefault")),0))
            self.subscription_changed()
        self.work("Waiting for Microsoft sign-in...",task,done)

    def refresh_subscriptions(self):
        if not self.session or not self.subscriptions:
            messagebox.showinfo("Sign in first","Complete Sign in & discover first."); return
        current=self.subscriptions[self.subbox.current()]["id"]
        def done(rows):
            self.clear_selection()
            self.subscriptions=rows
            self.subbox.configure(values=[r["name"]+" | "+r["id"]+" | "+r.get("state","Unknown")+
                                          " | Tenant: "+r["tenantId"] for r in rows])
            if rows:
                self.subbox.current(next((i for i,r in enumerate(rows) if r["id"]==current),0))
                self.subscription_changed()
            else: self.status.set("Azure returned no subscriptions after refresh.")
        self.work("Refreshing subscriptions from Azure...",self.session.refresh_subscriptions,done)

    def check_subscription(self):
        if not self.session or not self.subscriptions:
            messagebox.showinfo("Sign in first","Complete Sign in & discover first."); return
        value=simpledialog.askstring("Check missing subscription","Paste the subscription ID shown in Azure portal:",parent=self.root)
        if not value: return
        try: value=guid(value.strip(),"subscription ID")
        except Stop as error:
            messagebox.showerror("Subscription ID",str(error)); return
        def done(report):
            window=tk.Toplevel(self.root);window.title("Subscription diagnostic - read only");window.geometry("820x540")
            box=tk.Text(window,wrap="word",font=("Consolas",10));box.pack(fill="both",expand=True,padx=16,pady=16)
            box.insert("1.0",report);box.configure(state="disabled")
            def copy():
                self.root.clipboard_clear();self.root.clipboard_append(report)
            ttk.Button(window,text="Copy results",command=copy).pack(pady=(0,12))
            self.status.set("Subscription diagnostic complete. Review the separate results window.")
            window.lift()
        self.work("Checking subscription access and workspaces...",lambda:self.session.check_subscription(value),done)

    def subscription_changed(self,event=None):
        self.workspace=None; self.plan=None; self.workspaces=[]
        self.wsbox.configure(values=[]); self.vars["workspace"].set("")
        index=self.subbox.current()
        if index<0: return
        sub=self.subscriptions[index]
        self.auth_hint=sub["tenantId"]
        self.vars["tenant"].set(self.auth_hint)
        self.identity.set("Tenant ID: "+sub["tenantId"]+"\nSubscription ID: "+sub["id"]+
                          "\nResource group: Pending discovery\nWorkspace name: Pending discovery\nWorkspace ID: Pending discovery")
        self.discover()

    def discover(self):
        index=self.subbox.current()
        if not self.session or index<0:
            messagebox.showerror("Sign in first","Sign in and select a subscription."); return
        sub=self.subscriptions[index]
        self.workspace=None; self.plan=None; self.workspaces=[]
        self.wsbox.configure(values=[]); self.vars["workspace"].set("")
        def done(rows):
            self.workspaces=rows
            self.wsbox.configure(values=[r["workspace_name"]+" | "+r["resource_group"] for r in rows])
            if rows:
                self.wsbox.current(0); self.workspace_changed()
                self.status.set(f"Found {len(self.subscriptions)} subscription(s), {len(rows)} workspace(s) here. Confirm the selected destination before copying.")
            else:
                self.identity.set("Tenant ID: "+sub["tenantId"]+"\nSubscription ID: "+sub["id"]+
                                  "\nResource group: Not available\nWorkspace name: No accessible workspaces\nWorkspace ID: Not available")
                self.status.set("No workspaces visible in this subscription. Check access or select another subscription.")
        self.work("Discovering workspace details...",lambda:self.session.discover(sub["id"],sub["tenantId"]),done)

    def workspace_changed(self,event=None):
        index=self.wsbox.current()
        if index<0: return
        self.workspace=dict(self.workspaces[index]); self.plan=None
        account=self.session.account or {}
        self.identity.set("Signed in: "+account.get("user",{}).get("name","unknown")+"\n"+
            "\n".join(label+": "+self.workspace[key] for key,label in
                      (("tenant_id","Tenant ID"),("subscription_id","Subscription ID"),("resource_group","Resource group"),
                       ("workspace_name","Workspace name"),("workspace_id","Workspace ID"))))

    def payload(self):
        v=self.values()
        require(self.workspace and self.session, "Sign in and select a discovered workspace first.")
        require(v["tenant"] == self.auth_hint, "Tenant field changed since sign-in. Sign in to the selected tenant again.")
        sub=self.subscriptions[self.subbox.current()]
        require(sub["tenantId"].lower()==self.workspace["tenant_id"].lower(), "Client/tenant selection changed.")
        require(re.fullmatch(r"[a-z][a-z0-9-]{1,47}",v["client_slug"]), "Use a lowercase client slug.")
        require(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*",v["label"]), "Use a lowercase workspace label.")
        target=v["client_slug"]+"-"+v["label"]
        principals=[v["preview_principal"],v["deploy_principal"]]
        if v["mode"]=="Permissions only":
            require(all(principals), "Enter the preview and deployment enterprise application Object IDs in Setup options.")
            [guid(x,"service principal Object ID") for x in principals]
            config=dict(self.workspace,target=target)
        else:
            config=config_from_workspace(self.workspace,v["client_slug"],v["label"],v,self.extras)
        return config,v["mode"],principals

    def onboard(self,apply):
        try:
            config,mode,principals=self.payload()
            token=fingerprint(config,mode,principals)
            if apply:
                require(self.plan and self.plan[0]==token and time.time()-self.plan[1]<900,
                        "Preview this exact configuration first. Plans expire after 15 minutes.")
            target=config.get("target") or config["client"]+"-"+config["workspace_label"]
            summary=(mode+"\nTarget: "+target+"\nTenant: "+config["tenant_id"]+
                     "\nSubscription: "+config["subscription_id"]+"\nWorkspace: "+config["workspace_name"]+
                     "\nResource group: "+config["resource_group"])
            self.summary.set(summary)
            if apply and not messagebox.askokcancel("Apply reviewed setup",summary+
                    "\n\nThis changes permissions"+(" and Azure/GitHub onboarding resources." if mode!="Permissions only" else ".")+
                    "\nProceed with this destination?",parent=self.root):
                return
            self.tabs.select(2)
            self.plan=None
            def task():
                if mode=="Permissions only":
                    return permission_run(self.session,config,target,principals,apply)
                return full_run(self.session,config,apply)
            def done(result):
                self.log(result)
                self.status.set(result)
                if not apply: self.plan=(token,time.time())
                elif mode!="Permissions only": self.log("Review the PR and environment bypass settings before the first rule deployment.")
            self.work("Applying reviewed setup..." if apply else "Building a read-only setup plan...",task,done)
        except Exception as error: messagebox.showerror("Setup details",str(error))

    def render_details(self):
        self.details.configure(state="normal")
        self.details.delete("1.0","end")
        self.details.insert("1.0",self.identity.get())
        self.details.configure(state="disabled")

    def copy_workspace(self):
        if not self.workspace:
            messagebox.showinfo("Select workspace","Discover and select a workspace first.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.identity.get())
        self.status.set("Workspace details copied to the clipboard.")

    def export_config(self):
        try:
            v=self.values()
            config=config_from_workspace(self.workspace,v["client_slug"],v["label"],v,self.extras)
            path=filedialog.asksaveasfilename(defaultextension=".json",initialfile=v["client_slug"]+"-"+v["label"]+".json",
                                            filetypes=[("JSON configuration","*.json")])
            if path:
                Path(path).write_text(json.dumps(config,indent=2),encoding="utf-8")
                self.status.set("Onboarding configuration exported.")
        except Exception as error: messagebox.showerror("Export",str(error))

    def import_config(self):
        path=filedialog.askopenfilename(filetypes=[("JSON configuration","*.json")])
        if not path:return
        try:
            c=json.loads(Path(path).read_text(encoding="utf-8-sig")); validate_config(c)
            self.clear_selection()
            mapping={"tenant":"tenant_id","client_slug":"client","label":"workspace_label","github_owner":"github_owner",
                     "github_repo":"github_repo","oidc_subject_format":"oidc_subject_format","rule_path":"initial_rule_path",
                     "existing_preview_app_client_id":"existing_preview_app_client_id","existing_deploy_app_client_id":"existing_deploy_app_client_id"}
            for local,source in mapping.items(): self.vars[local].set(c.get(source,""))
            self.vars["client_name"].set(c["client"])
            self.vars["app_owners"].set(", ".join(c["app_owner_user_ids"]))
            reviewers=c["production_reviewers"]
            require(len({r["type"] for r in reviewers})==1,"This UI supports all User or all Team reviewers in a single config.")
            self.vars["reviewer_type"].set(reviewers[0]["type"])
            self.vars["reviewers"].set(", ".join(r["name"] for r in reviewers))
            self.vars["allow_missing_mitre"].set(c.get("allow_missing_mitre",False))
            self.vars["mode"].set("Full Azure + GitHub onboarding")
            self.update_mode()
            self.extras={"human_groups":c.get("human_groups",[])}
            self.status.set("Imported settings. Sign in and rediscover the workspace to verify the destination.")
            self.log("Imported "+str(len(self.extras["human_groups"]))+" optional human group definitions.")
        except Exception as error: messagebox.showerror("Import",str(error))

    def github_login(self):
        def task():
            session=self.session or Session(logger=self.log)
            require(session.gh_exe,"Install GitHub CLI first.")
            # Explicit user action. Device authorization text is shown live; no tokens are printed.
            p=subprocess.Popen([session.gh_exe,"auth","login","--hostname","github.com","--git-protocol","https","--web"],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                encoding="utf-8",errors="replace",env=session.env,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            p.stdin.write("\n"); p.stdin.flush(); p.stdin.close()
            timer=threading.Timer(900,p.kill); timer.start()
            try:
                for line in p.stdout: self.log(line.rstrip())
                require(p.wait()==0,"GitHub sign-in did not complete. Check the displayed authorization message.")
            finally: timer.cancel()
            return "GitHub sign-in completed."
        self.tabs.select(2)
        self.work("Complete GitHub authorization in the browser; a one-time code may appear below.",task,
                  lambda result:self.status.set(result))

    def logout(self):
        if not self.session:return
        session=self.session
        def done(_):
            self.session=None; self.clear_selection(); self.status.set("Azure app session signed out.")
        self.work("Signing out of this app's Azure session...",session.logout,done)

    def close(self):
        if self.busy:
            messagebox.showinfo("Operation running","Wait for the operation to finish before closing. Cloud changes may still be in progress.")
            return
        if self.session:
            session=self.session
            self.work("Signing out before closing...",session.logout,lambda _:self.root.destroy())
        else:self.root.destroy()

def main():
    if os.name=="nt":
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError,OSError):
            pass
    root=tk.Tk()
    App(root)
    root.mainloop()

if __name__=="__main__":
    main()

