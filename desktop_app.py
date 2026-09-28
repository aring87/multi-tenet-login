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
                             config_from_workspace, full_run, validate_tenant_hint, CYBERQP_PORTALS)
from lighthouse_onboarding import validate_config
from datetime import datetime, timedelta, timezone
from audit_evidence import collect_evidence, date_range
from audit_analysis import validate_expectations

# Constants that are identical for every client under the delegated model. They are
# entered once and persisted locally, rather than retyped per onboarding.
SETTINGS_KEYS = ("github_owner", "github_repo", "managing_tenant_id", "deploy_group_object_id",
                 "read_group_object_id", "engineer_group_object_id", "preview_environment",
                 "production_environment", "msp_offer_name", "delegation_location")

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
        self.settingsfile = DATA / "settings.json"
        self.clients = self.read_clients()
        self.settings = self.read_settings()
        self.vars = {}
        self.controls = []
        self.evidence_folder = None
        self.operation_page = 2
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

    def read_settings(self):
        if self.settingsfile.exists():
            try:
                value=json.loads(self.settingsfile.read_text(encoding="utf-8"))
                require(isinstance(value,dict), "Invalid saved settings.")
                require(not (value.keys()-set(SETTINGS_KEYS)), "Saved settings contain unknown fields.")
                require(all(isinstance(v,str) for v in value.values()), "Saved settings must be strings.")
                return value
            except Exception as error:
                messagebox.showerror("Settings",str(error))
                return {}
        return {}

    def save_settings(self):
        value={k:self.vars[k].get().strip() for k in SETTINGS_KEYS if k in self.vars}
        for key in ("managing_tenant_id","deploy_group_object_id","read_group_object_id",
                    "engineer_group_object_id"):
            if value.get(key):
                try:
                    value[key]=guid(value[key],key.replace("_"," "))
                except Stop as error:
                    messagebox.showerror("Settings",str(error),parent=self.root)
                    return
        try:
            temp=self.settingsfile.with_suffix(".tmp")
            temp.write_text(json.dumps(value,indent=2),encoding="utf-8")
            temp.replace(self.settingsfile)
        except OSError as error:
            messagebox.showerror("Settings",str(error),parent=self.root)
            return
        self.settings=value
        for key,current in value.items():
            if key in self.vars:
                self.vars[key].set(current)
        self.status.set("Managing-tenant settings saved locally. They apply to every client onboarding.")

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
        style.configure("Card.TLabelframe",background="#ffffff",bordercolor="#d7dee8")
        style.configure("Card.TLabelframe.Label",background="#ffffff",foreground="#35455d",font=("Segoe UI",10,"bold"))
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
        sidebar=tk.Frame(self.root,bg="#12213a",width=276)
        sidebar.pack(side="left",fill="y");sidebar.pack_propagate(False)
        tk.Label(sidebar,text="SENTINEL",bg="#12213a",fg="#ffffff",font=("Segoe UI",18,"bold")).pack(anchor="w",padx=22,pady=(30,4))
        tk.Label(sidebar,text="WORKSPACE DISCOVERY",bg="#12213a",fg="#9bb4d4",font=("Segoe UI",9,"bold")).pack(anchor="w",padx=22,pady=(0,30))
        self.nav=[]
        for i,(title,desc) in enumerate([("Discover","Sign in & copy details"),("Configure","Delegation & target file"),("Review","Preview & apply"),("Audit Evidence","Read-only Azure & Sentinel")]):
            row=tk.Frame(sidebar,bg="#12213a",cursor="hand2",takefocus=1)
            row.pack(fill="x",padx=12,pady=4)
            number=tk.Label(row,text=f"0{i+1}",bg="#12213a",fg="#7893b3",
                            font=("Segoe UI",9,"bold"),cursor="hand2")
            number.grid(row=0,column=0,rowspan=2,sticky="n",padx=(12,12),pady=(13,0))
            name=tk.Label(row,text=title,bg="#12213a",fg="#d8e5f5",
                          font=("Segoe UI",11,"bold"),anchor="w",cursor="hand2")
            name.grid(row=0,column=1,sticky="ew",padx=(0,8),pady=(10,0))
            description=tk.Label(row,text=desc,bg="#12213a",fg="#9bb4d4",
                                 font=("Segoe UI",9),anchor="w",justify="left",wraplength=190,cursor="hand2")
            description.grid(row=1,column=1,sticky="ew",padx=(0,8),pady=(2,11))
            row.columnconfigure(1,weight=1)
            for part in (row,number,name,description):
                part.bind("<Button-1>",lambda event,n=i:self.tabs.select(n))
            row.bind("<Return>",lambda event,n=i:self.tabs.select(n))
            row.bind("<space>",lambda event,n=i:self.tabs.select(n))
            self.nav.append((row,number,name,description))
        bottom=tk.Frame(sidebar,bg="#12213a");bottom.pack(side="bottom",fill="x",padx=22,pady=22)
        tk.Label(bottom,text="LOCAL WORKSPACE",bg="#12213a",fg="#8ba4c5",font=("Segoe UI",8,"bold")).pack(anchor="w")
        tk.Label(bottom,text="Your approved Azure access",bg="#12213a",fg="#c2d2e5",font=("Segoe UI",9),wraplength=220,justify="left").pack(anchor="w",pady=(6,16))
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
        for name in ("Connect","Configure","Review","Audit Evidence"):
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
        connect,settings,review,audit=self.page_contents
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
        self.button(connect,"Advanced: delegation & onboarding  >",lambda:self.tabs.select(1),"Primary.TButton").grid(
            row=1,column=1,sticky="e",pady=(0,16))
        top,body=self.card(settings,"This client",
            "Onboarding deploys one Azure Lighthouse delegation in the client's tenant and opens a target-file pull request. "
            "No app registrations, federated credentials, GitHub environments or custom roles are created per client.")
        top.pack(fill="x",pady=(0,16))
        self.form(body,"Workspace identity label","label","primary")
        ttk.Label(body,text="The primary label uses workspace.yml. Additional labels keep separate workspace files and targets.",
                  style="Muted.TLabel",wraplength=810).pack(anchor="w",pady=(0,14))
        self.form(body,"Initial rule path - optional","rule_path")
        self.vars["allow_missing_mitre"]=tk.BooleanVar(value=False)
        cb=ttk.Checkbutton(body,text="Allow missing MITRE metadata (approved exception)",variable=self.vars["allow_missing_mitre"])
        cb.pack(anchor="w",pady=(0,8));self.controls.append((cb,"normal"))
        ttk.Label(body,text="An initial rule is committed disabled. Leave blank to create the target with no rules selected.",
                  style="Muted.TLabel",wraplength=810).pack(anchor="w")
        self.constantscard,body=self.card(settings,"Managing tenant (set once)",
            "These are identical for every client. Saved locally in desktop-data and reused on each onboarding.")
        self.constantscard.pack(fill="x",pady=(0,16))
        self.form(body,"GitHub organization","github_owner",self.settings.get("github_owner",""))
        self.form(body,"GitHub repository","github_repo",self.settings.get("github_repo",""))
        self.form(body,"Managing tenant ID","managing_tenant_id",self.settings.get("managing_tenant_id",""))
        ttk.Label(body,text="The managing tenant receives the delegated access. It is written into the target file as tenant_id, because the pipeline authenticates there and the client's subscription is visible from it.",
                  style="Muted.TLabel",wraplength=810).pack(anchor="w",pady=(0,14))
        self.form(body,"Deploy group Object ID","deploy_group_object_id",self.settings.get("deploy_group_object_id",""))
        self.form(body,"Preview group Object ID","read_group_object_id",self.settings.get("read_group_object_id",""))
        self.form(body,"Engineers group Object ID","engineer_group_object_id",self.settings.get("engineer_group_object_id",""))
        ttk.Label(body,text="Groups in the managing tenant, not the client's. The delegation grants them built-in roles; Lighthouse cannot delegate custom role definitions.",
                  style="Muted.TLabel",wraplength=810).pack(anchor="w",pady=(0,14))
        self.form(body,"Shared preview environment","preview_environment",self.settings.get("preview_environment",""))
        self.form(body,"Shared production environment","production_environment",self.settings.get("production_environment",""))
        ttk.Label(body,text="Both environments are shared by every client and created once by hand, with AZURE_CLIENT_ID and required reviewers. Onboarding verifies them and refuses to create them.",
                  style="Muted.TLabel",wraplength=810).pack(anchor="w",pady=(0,14))
        self.form(body,"Offer name - optional","msp_offer_name",self.settings.get("msp_offer_name",""))
        self.form(body,"Delegation location - optional","delegation_location",self.settings.get("delegation_location",""))
        row=ttk.Frame(body,style="Card.TFrame");row.pack(fill="x",pady=(4,0))
        self.button(row,"Save as defaults",self.save_settings,"Primary.TButton").pack(side="left",padx=(0,10))
        self.button(row,"Sign in to GitHub",self.github_login).pack(side="left",padx=(0,10))
        self.button(row,"Export configuration",self.export_config).pack(side="left")
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
        auditcard,body=self.card(audit,"Collect Azure / Sentinel evidence",
            "Read-only collection using your signed-in Azure access. Choose a workspace in Discover first.")
        auditcard.pack(fill="x",pady=(0,16))
        ttk.Label(body,textvariable=self.identity,style="Muted.TLabel",wraplength=650,justify="left").pack(anchor="w",pady=(0,18))
        today=datetime.now(timezone.utc).date()
        dates=ttk.Frame(body,style="Card.TFrame");dates.pack(fill="x")
        left=ttk.Frame(dates,style="Card.TFrame");left.pack(side="left",fill="x",expand=True,padx=(0,16))
        right=ttk.Frame(dates,style="Card.TFrame");right.pack(side="left",fill="x",expand=True)
        self.form(left,"Start date (UTC, YYYY-MM-DD)","audit_start",(today-timedelta(days=29)).isoformat())
        self.form(right,"End date (UTC, inclusive)","audit_end",today.isoformat())
        ttk.Label(body,text="Collects current workspace settings and period evidence. The report shows daily coverage, incomplete requests and observations backed by evidence. No compliance score is assigned.",style="Muted.TLabel",wraplength=650,justify="left").pack(anchor="w",pady=(0,18))
        self.audit_requirements_toggle=self.button(body,"Edit client requirements (optional)",self.toggle_audit_requirements)
        self.audit_requirements_toggle.pack(anchor="w",pady=(0,10))
        self.audit_requirement_summary=tk.StringVar()
        ttk.Label(body,textvariable=self.audit_requirement_summary,style="Muted.TLabel",wraplength=650,justify="left").pack(anchor="w",pady=(0,14))
        requirements=ttk.LabelFrame(body,text="Client requirements",style="Card.TLabelframe",padding=14)
        self.audit_requirements_frame=requirements
        self.form(requirements,"Minimum searchable retention (days; blank skips comparison)","audit_retention")
        self.form(requirements,"Expected log tables (comma separated; up to 20)","audit_tables")
        self.form(requirements,"Critical rules required enabled (IDs or exact names; separate with ;)","audit_rules")
        ttk.Label(requirements,text="Use the client's approved requirements. Retention compares the workspace default and listed tables only, not archive retention. Expected tables receive direct daily-count queries. Critical rule names must be unique.",style="Muted.TLabel",wraplength=610,justify="left").pack(anchor="w",pady=(0,12))
        self.button(requirements,"Save requirements for this workspace",self.save_audit_requirements).pack(anchor="w")
        self.audit_collect_button=self.button(body,"Collect evidence & save package",self.collect_audit,"Primary.TButton")
        self.audit_collect_button.pack(anchor="w")
        for key in ("audit_retention","audit_tables","audit_rules"):
            self.vars[key].trace_add("write",lambda *args:self.update_audit_requirements_summary())
        self.update_audit_requirements_summary()
        resultcard,body=self.card(audit,"Evidence results","Packages contain a readable report, JSON/CSV evidence and a file-hash manifest.")
        resultcard.pack(fill="x",pady=(0,16))
        self.audit_summary=tk.StringVar(value="No evidence collected yet. Choose an approved local folder when collecting.")
        ttk.Label(body,textvariable=self.audit_summary,style="Muted.TLabel",wraplength=650,justify="left").pack(anchor="w",pady=(0,16))
        actions=ttk.Frame(body,style="Card.TFrame");actions.pack(fill="x")
        self.button(actions,"Open coverage & findings",lambda:self.open_evidence(True)).pack(side="left",padx=(0,10))
        self.button(actions,"Open package folder",self.open_evidence).pack(side="left")
        self.tabs.bind("<<NotebookTabChanged>>",self.page_changed)
        self.page_changed()
        self.root.bind("<MouseWheel>",self.scroll_page)

    def scroll_page(self,event):
        if event.widget.winfo_class() in ("Text","TCombobox"):
            return
        self.page_canvases[self.tabs.index("current")].yview_scroll(int(-event.delta/120),"units")

    def page_changed(self,event=None):
        index=self.tabs.index("current")
        titles=["Connect your client","Configure client onboarding","Review & apply","Audit evidence"]
        subtitles=["Discover the right workspace without navigating the Azure portal.",
                   "Deploy a Lighthouse delegation and open the client's target-file pull request.",
                   "Confirm the destination, preview the setup, then apply.",
                   "Collect and export Azure / Sentinel evidence for the selected workspace."]
        self.page_title.set(titles[index]);self.page_subtitle.set(subtitles[index])
        for i,(row,number,name,description) in enumerate(self.nav):
            selected=i==index
            background="#244566" if selected else "#12213a"
            row.configure(bg=background)
            number.configure(bg=background,fg="#a9c8ff" if selected else "#7893b3")
            name.configure(bg=background,fg="#ffffff" if selected else "#d8e5f5")
            description.configure(bg=background,fg="#d5e5fb" if selected else "#9bb4d4")

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
        for key in ("client_name","tenant","client_slug"):
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
                    if self.busy and self.operation_page==3:
                        self.status.set(data[:220])
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end",data+"\n"); self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                elif kind=="done":
                    callback,result,error=data
                    self.set_busy(False)
                    if error:
                        self.plan=None
                        self.status.set("Stopped - "+error[:160])
                        self.log(error); self.tabs.select(self.operation_page)
                        if self.operation_page==3:
                            self.audit_summary.set("Collection stopped: "+error)
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

    def work(self,title,task,done=None,page=2):
        if self.busy: return
        self.operation_page=page
        self.set_busy(True); self.status.set(title); self.log(title)
        def run():
            try: result,error=task(),None
            except Exception as ex: result,error=None,str(ex)
            self.events.put(("done",(done,result,error)))
        threading.Thread(target=run,daemon=True).start()

    def collect_audit(self):
        if self.busy: return
        try:
            require(self.session and self.workspace,"Sign in and select a discovered workspace first.")
            start,end=self.vars["audit_start"].get().strip(),self.vars["audit_end"].get().strip()
            date_range(start,end)
            expectations=self.audit_requirements()
        except Stop as error:
            messagebox.showerror("Audit evidence",str(error),parent=self.root);return
        destination=filedialog.askdirectory(title="Choose an approved local folder for client evidence",parent=self.root)
        if not destination:return
        workspace=dict(self.workspace);session=self.session
        self.evidence_folder=None
        self.audit_summary.set("Collecting evidence for "+workspace["workspace_name"]+". This may take several minutes.")
        def done(result):
            self.evidence_folder=Path(result["folder"])
            rows=result["manifest"]["results"]
            assessment=result["manifest"].get("analysis_summary",{})
            self.audit_summary.set("Workspace: "+workspace["workspace_name"]+"\n"+
                f"Observed issues: {assessment.get('observed_issues',0)} | Needs review: {assessment.get('needs_review',0)} | Insufficient evidence: {assessment.get('insufficient_evidence',0)}\n\n"+
                "\n".join(r["title"]+": "+r["status"].replace("_"," ")+f" ({r['record_count']} records)" for r in rows)+
                "\n\nSaved to: "+result["folder"])
            self.status.set("Evidence package saved. Review collection statuses and limitations in the report.")
            self.tabs.select(3)
        self.work("Collecting read-only Azure / Sentinel evidence...",
                  lambda:collect_evidence(session,workspace,start,end,destination,expectations),done,page=3)

    def audit_requirements(self):
        value=self.vars["audit_retention"].get().strip()
        require(not value or value.isascii() and value.isdigit(),"Enter a whole number of retention days, or leave it blank.")
        return validate_expectations(dict(minimum_retention_days=int(value) if value else None,
            expected_tables=[x.strip() for x in self.vars["audit_tables"].get().split(",") if x.strip()],
            critical_rules=[x.strip() for x in self.vars["audit_rules"].get().split(";") if x.strip()]))

    def toggle_audit_requirements(self):
        if self.audit_requirements_frame.winfo_manager():
            self.audit_requirements_frame.pack_forget()
            self.audit_requirements_toggle.configure(text="Edit client requirements (optional)")
        else:
            self.audit_requirements_frame.pack(fill="x",pady=(0,18),before=self.audit_collect_button)
            self.audit_requirements_toggle.configure(text="Hide client requirements")

    def update_audit_requirements_summary(self):
        days=self.vars["audit_retention"].get().strip()
        tables=len([x for x in self.vars["audit_tables"].get().split(",") if x.strip()])
        rules=len([x for x in self.vars["audit_rules"].get().split(";") if x.strip()])
        self.audit_requirement_summary.set(
            f"Requirements for this run: searchable retention {days or 'not specified'}{' days' if days else ''}; {tables} expected tables; {rules} critical rules."
            if days or tables or rules else "No client requirements entered. Collection and general observations still run.")

    def audit_requirements_path(self):
        require(self.workspace,"Select a discovered workspace first.")
        return DATA/"audit-requirements"/guid(self.workspace["tenant_id"],"tenant")/(guid(self.workspace["workspace_id"],"workspace")+".json")

    def save_audit_requirements(self):
        if self.busy:return
        try:
            value=self.audit_requirements()
            path=self.audit_requirements_path()
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value,indent=2),encoding="utf-8")
            temporary.replace(path)
            self.status.set("Audit requirements saved locally for "+self.workspace["workspace_name"]+".")
        except (Stop,OSError) as error:
            messagebox.showerror("Audit requirements",str(error),parent=self.root)

    def load_audit_requirements(self):
        for key in ("audit_retention","audit_tables","audit_rules"):
            if key in self.vars:self.vars[key].set("")
        if not self.workspace:return
        try:
            path=self.audit_requirements_path()
            if not path.exists():return
            value=validate_expectations(json.loads(path.read_text(encoding="utf-8")))
            self.vars["audit_retention"].set(str(value["minimum_retention_days"]) if value["minimum_retention_days"] is not None else "")
            self.vars["audit_tables"].set(", ".join(value["expected_tables"]))
            self.vars["audit_rules"].set("; ".join(value["critical_rules"]))
        except (Stop,OSError,ValueError) as error:
            messagebox.showerror("Audit requirements","Saved requirements could not be loaded: "+str(error),parent=self.root)

    def open_evidence(self,report=False):
        if not self.evidence_folder:
            messagebox.showinfo("Audit evidence","Collect an evidence package first.",parent=self.root);return
        os.startfile(str(self.evidence_folder / "report.html" if report else self.evidence_folder))

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
        self.load_audit_requirements()
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
            rows=sorted(rows,key=lambda r:(r.get("name","").casefold(),r["id"]))
            self.subscriptions=rows
            self.subbox.configure(values=[r["name"]+" | "+r["id"]+" | "+r.get("state","Unknown")+" | Tenant: "+r["tenantId"] for r in rows])
            self.subbox.current(next((i for i,r in enumerate(rows) if r.get("isDefault")),0))
            self.subscription_changed()
        self.work("Waiting for Microsoft sign-in...",task,done)

    def refresh_subscriptions(self):
        if not self.session or not self.subscriptions:
            messagebox.showinfo("Sign in first","Complete Sign in & discover first."); return
        current=self.subscriptions[self.subbox.current()]["id"]
        def done(rows):
            self.clear_selection()
            rows=sorted(rows,key=lambda r:(r.get("name","").casefold(),r["id"]))
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
        self.load_audit_requirements()
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
        return config_from_workspace(self.workspace,v["client_slug"],v["label"],v,self.extras)

    def onboard(self,apply):
        try:
            config=self.payload()
            token=fingerprint(config,"lighthouse-delegation",[])
            if apply:
                require(self.plan and self.plan[0]==token and time.time()-self.plan[1]<900,
                        "Preview this exact configuration first. Plans expire after 15 minutes.")
            target=config["client"]+"-"+config["workspace_label"]
            summary=("Lighthouse delegation onboarding\nTarget: "+target+
                     "\nClient tenant: "+config["tenant_id"]+
                     "\nManaging tenant: "+config["managing_tenant_id"]+
                     "\nSubscription: "+config["subscription_id"]+"\nWorkspace: "+config["workspace_name"]+
                     "\nResource group: "+config["resource_group"])
            self.summary.set(summary)
            if apply and not messagebox.askokcancel("Apply reviewed setup",summary+
                    "\n\nThis delegates the client subscription to the managing tenant and opens a "
                    "target-file pull request.\nProceed with this destination?",parent=self.root):
                return
            self.tabs.select(2)
            self.plan=None
            def task():
                return full_run(self.session,config,apply)
            def done(result):
                self.log(result)
                self.status.set(result)
                if not apply: self.plan=(token,time.time())
                else: self.log("Merge the reviewed PR, run preview on main, review the what-if, then run an approved deployment.")
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
                self.status.set("Onboarding configuration exported. It contains client and managing-tenant identifiers; store it accordingly.")
        except Exception as error: messagebox.showerror("Export",str(error))

    def import_config(self):
        path=filedialog.askopenfilename(filetypes=[("JSON configuration","*.json")])
        if not path:return
        try:
            c=json.loads(Path(path).read_text(encoding="utf-8-sig")); validate_config(c)
            self.clear_selection()
            mapping={"tenant":"tenant_id","client_slug":"client","label":"workspace_label",
                     "github_owner":"github_owner","github_repo":"github_repo",
                     "managing_tenant_id":"managing_tenant_id",
                     "deploy_group_object_id":"deploy_group_object_id",
                     "read_group_object_id":"read_group_object_id",
                     "engineer_group_object_id":"engineer_group_object_id",
                     "preview_environment":"preview_environment",
                     "production_environment":"production_environment",
                     "msp_offer_name":"msp_offer_name","delegation_location":"delegation_location",
                     "rule_path":"initial_rule_path"}
            for local,source in mapping.items(): self.vars[local].set(c.get(source,""))
            self.vars["client_name"].set(c["client"])
            self.vars["allow_missing_mitre"].set(c.get("allow_missing_mitre",False))
            self.extras={}
            self.status.set("Imported settings. Sign in and rediscover the workspace to verify the destination.")
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
