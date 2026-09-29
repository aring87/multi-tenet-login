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
from desktop_theme import configure_theme
from rules_page import RulesPage
from signin_window import create_handoff
from workspace_tools import access_plan, apply_contributor
from desktop_backend import (BASE, DATA, Session, Stop, require, guid, fingerprint,
                             config_from_workspace, full_run, validate_tenant_hint, CYBERQP_PORTALS)
from lighthouse_onboarding import validate_config, validate_target, slug, CLIENT_MAXIMUM
from datetime import datetime, timedelta, timezone
from audit_evidence import (collect_evidence, date_range, validate_options, CONFIGURATIONS,
                            list_log_tables, preview_log_tables)

# Constants that are identical for every client under the delegated model. They are
# entered once and persisted locally, rather than retyped per onboarding.
SETTINGS_KEYS = ("github_owner", "github_repo", "managing_tenant_id", "deploy_group_object_id",
                 "read_group_object_id", "engineer_group_object_id", "preview_environment",
                 "production_environment", "msp_offer_name", "delegation_location")

class App:
    def __init__(self, root):
        self.root = root
        root.title("Sentinel Workspace | Client Operations")
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
        self.signin_handoff = None
        self.signin_timer = None
        self.signin_active = False
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
        caption=ttk.Label(box,text=label,style="Field.TLabel",wraplength=600)
        caption.pack(anchor="w",pady=(0,6));self.wrap_to_parent(caption)
        v=self.var(name,default)
        if values is None:
            w=ttk.Entry(box,textvariable=v)
        else:
            w=ttk.Combobox(box,textvariable=v,values=values,state="readonly")
        w.pack(fill="x")
        self.controls.append((w,"readonly" if values is not None else "normal"))
        return w

    def wrap_to_parent(self,label,margin=0):
        label.master.bind("<Configure>",lambda event,w=label,m=margin:w.configure(wraplength=max(180,event.width-m)),add="+")

    def card(self, parent, title, subtitle=""):
        outer=ttk.Frame(parent,style="Card.TFrame",padding=22)
        ttk.Label(outer,text=title,style="CardTitle.TLabel").pack(anchor="w")
        if subtitle:
            label=ttk.Label(outer,text=subtitle,style="Muted.TLabel",justify="left",wraplength=600)
            label.pack(fill="x",pady=(6,18));self.wrap_to_parent(label,44)
        else:ttk.Frame(outer,style="Card.TFrame",height=16).pack()
        body=ttk.Frame(outer,style="Card.TFrame");body.pack(fill="both",expand=True)
        return outer,body

    def disclosure(self,parent,title,expanded=False):
        outer=ttk.Frame(parent,style="Card.TFrame");outer.pack(fill="x",pady=(4,12))
        body=ttk.Frame(outer,style="Card.TFrame",padding=(0,12,0,0))
        def toggle():
            if body.winfo_manager():body.pack_forget();button.configure(text="+  "+title)
            else:body.pack(fill="x");button.configure(text="−  "+title)
        button=self.button(outer,("−  " if expanded else "+  ")+title,toggle,"Disclosure.TButton")
        button.pack(fill="x")
        if expanded:body.pack(fill="x")
        return body

    def form_pair(self,parent,fields):
        row=ttk.Frame(parent,style="Card.TFrame");row.pack(fill="x")
        for index,field in enumerate(fields):
            column=ttk.Frame(row,style="Card.TFrame");column.pack(side="left",fill="both",expand=True,padx=(0,16) if index==0 else 0)
            self.form(column,*field)
        return row

    def section(self,parent,title,description=""):
        frame=ttk.Frame(parent,style="Card.TFrame");frame.pack(fill="x",pady=(6,12))
        ttk.Label(frame,text=title,style="Section.TLabel").pack(anchor="w",pady=(0,10))
        if description:
            label=ttk.Label(frame,text=description,style="Muted.TLabel",wraplength=600,justify="left")
            label.pack(fill="x",pady=(0,12));self.wrap_to_parent(label)
        return frame

    def update_workspace_context(self):
        if self.workspace:
            self.workspace_context.set(self.workspace["workspace_name"]+"  /  Tenant "+self.workspace["tenant_id"])
        else:self.workspace_context.set("No workspace selected  /  Connect an Azure account to begin")

    def build(self):
        configure_theme(self.root)
        sidebar=tk.Frame(self.root,bg="#142638",width=248)
        sidebar.pack(side="left",fill="y");sidebar.pack_propagate(False)
        tk.Label(sidebar,text="SENTINEL",bg="#142638",fg="#ffffff",font=("Segoe UI",18,"bold")).pack(anchor="w",padx=22,pady=(30,4))
        tk.Label(sidebar,text="CLIENT OPERATIONS",bg="#142638",fg="#9bb4d4",font=("Segoe UI",9,"bold")).pack(anchor="w",padx=22,pady=(0,30))
        # Display order is independent of the notebook indices used by actions.
        navigation=[(0,"Workspaces","Connect & discover"),(4,"Analytics rules","Enabled & disabled"),
                    (1,"Onboarding","Configure client access"),(2,"Review & apply","Preview deployment"),
                    (3,"Sentinel audit","Configuration & logs")]
        self.nav=[None]*len(navigation)
        for position,(i,title,desc) in enumerate(navigation):
            row=tk.Frame(sidebar,bg="#142638",cursor="hand2",takefocus=1,
                         highlightthickness=1,highlightbackground="#142638",highlightcolor="#86c8bd")
            row.pack(fill="x",padx=12,pady=4)
            number=tk.Label(row,text=f"0{position+1}",bg="#142638",fg="#7893b3",
                            font=("Segoe UI",9,"bold"),cursor="hand2")
            number.grid(row=0,column=0,rowspan=2,sticky="n",padx=(12,12),pady=(13,0))
            name=tk.Label(row,text=title,bg="#142638",fg="#d8e5f5",
                          font=("Segoe UI",11,"bold"),anchor="w",cursor="hand2")
            name.grid(row=0,column=1,sticky="ew",padx=(0,8),pady=(10,0))
            description=tk.Label(row,text=desc,bg="#142638",fg="#9bb4d4",
                                 font=("Segoe UI",9),anchor="w",justify="left",wraplength=168,cursor="hand2")
            description.grid(row=1,column=1,sticky="ew",padx=(0,8),pady=(2,11))
            row.columnconfigure(1,weight=1)
            for part in (row,number,name,description):
                part.bind("<Button-1>",lambda event,n=i:self.tabs.select(n))
            row.bind("<Return>",lambda event,n=i:self.tabs.select(n))
            row.bind("<space>",lambda event,n=i:self.tabs.select(n))
            self.nav[i]=(row,number,name,description)
        bottom=tk.Frame(sidebar,bg="#142638");bottom.pack(side="bottom",fill="x",padx=22,pady=22)
        tk.Label(bottom,text="LOCAL WORKSPACE",bg="#142638",fg="#8ba4c5",font=("Segoe UI",8,"bold")).pack(anchor="w")
        tk.Label(bottom,text="Azure access • Local exports",bg="#142638",fg="#c2d2e5",font=("Segoe UI",9),wraplength=200,justify="left").pack(anchor="w",pady=(6,16))
        tk.Button(bottom,text="Help & prerequisites",command=lambda:os.startfile(str(BASE/"DESKTOP-START-HERE.md")),
                  bg="#142638",fg="#a9c5ff",activebackground="#203b60",relief="flat",anchor="w",bd=0).pack(anchor="w")
        tk.Button(bottom,text="Open app data",command=lambda:os.startfile(str(DATA)),
                  bg="#142638",fg="#a9c5ff",activebackground="#203b60",relief="flat",anchor="w",bd=0).pack(anchor="w",pady=(8,0))
        main=ttk.Frame(self.root);main.pack(side="left",fill="both",expand=True)
        head=ttk.Frame(main,padding=(30,26,30,18));head.pack(fill="x")
        self.page_title=tk.StringVar(value="Connect your client")
        self.page_subtitle=tk.StringVar(value="Discover the right workspace without navigating the Azure portal.")
        ttk.Label(head,textvariable=self.page_title,font=("Segoe UI",22,"bold"),foreground="#14263e").pack(anchor="w")
        subtitle=ttk.Label(head,textvariable=self.page_subtitle,style="Context.TLabel")
        subtitle.pack(anchor="w",pady=(6,0))
        self.wrap_to_parent(subtitle,60)
        self.workspace_context=tk.StringVar(value="No workspace selected  /  Connect an Azure account to begin")
        context=ttk.Label(head,textvariable=self.workspace_context,style="Context.TLabel")
        context.pack(anchor="w",pady=(14,0))
        self.wrap_to_parent(context,60)
        self.status=tk.StringVar(value="Ready. Use an authorized Azure account to connect a client.")
        statusframe=ttk.Frame(main,padding=(30,10));statusframe.pack(side="bottom",fill="x")
        self.activity_badge=ttk.Label(statusframe,text="READY",style="Badge.TLabel",padding=(9,4))
        self.activity_badge.pack(side="left",anchor="n",padx=(0,12))
        statusbody=ttk.Frame(statusframe);statusbody.pack(fill="both",expand=True)
        statuslabel=ttk.Label(statusbody,textvariable=self.status,style="Context.TLabel")
        statuslabel.pack(anchor="w");self.wrap_to_parent(statuslabel)
        self.progress=ttk.Progressbar(statusbody,mode="indeterminate",maximum=100)
        self.tabs=ttk.Notebook(main,style="Hidden.TNotebook");self.tabs.pack(fill="both",expand=True,padx=26,pady=(0,12))
        self.page_contents=[]
        self.page_canvases=[]
        for name in ("Connect","Configure","Review","Sentinel Audit","Analytics Rules"):
            page=ttk.Frame(self.tabs);self.tabs.add(page,text=name)
            canvas=tk.Canvas(page,bg="#f2f5f8",highlightthickness=0,bd=0)
            scroll=ttk.Scrollbar(page,orient="vertical",command=canvas.yview)
            scroll.pack(side="right",fill="y");canvas.pack(side="left",fill="both",expand=True)
            canvas.configure(yscrollcommand=scroll.set)
            self.page_canvases.append(canvas)
            content=ttk.Frame(canvas);window=canvas.create_window((0,0),window=content,anchor="nw")
            content.bind("<Configure>",lambda e,c=canvas:c.configure(scrollregion=c.bbox("all")))
            canvas.bind("<Configure>",lambda e,c=canvas,w=window:c.itemconfigure(w,width=e.width))
            self.page_contents.append(content)
        connect,settings,review,audit,rules=self.page_contents
        self.rules_page=RulesPage(self,rules)
        connect.columnconfigure(0,weight=1,uniform="discovery");connect.columnconfigure(1,weight=1,uniform="discovery")
        discovery,body=self.card(connect,"Connect to Azure","Sign in, then select the subscription and workspace you want to work with.")
        discovery.grid(row=0,column=1,sticky="nsew",pady=(0,16))
        self.form(body,"Tenant ID or domain · optional","tenant")
        hint=ttk.Label(body,text="Leave blank to discover the directories available to your account.",style="Muted.TLabel",wraplength=300,justify="left")
        hint.pack(fill="x",pady=(0,14));self.wrap_to_parent(hint)
        self.form(body,"Sign-in method","login_method","Browser",["Browser","Windows account window"])
        self.button(body,"Sign in & discover",self.login,"Primary.TButton").pack(fill="x",pady=(0,14))
        self.signin_button=ttk.Button(body,text="Bring sign-in window forward",command=self.bring_signin_forward,state="disabled")
        self.signin_button.pack(fill="x",pady=(0,14))
        self.subbox=self.form(body,"Subscription","subscription",values=[])
        self.subbox.bind("<<ComboboxSelected>>",self.subscription_changed)
        self.wsbox=self.form(body,"Log Analytics workspace","workspace",values=[])
        self.wsbox.bind("<<ComboboxSelected>>",self.workspace_changed)
        tools=self.disclosure(body,"Access & discovery tools")
        self.form(tools,"CyberQP region · optional","cyberqp_region","US",list(CYBERQP_PORTALS))
        hint=ttk.Label(tools,text="Activate JIT access in your browser, then return to sign in here.",style="Muted.TLabel",wraplength=300,justify="left")
        hint.pack(fill="x",pady=(0,12));self.wrap_to_parent(hint)
        for label,command in (("Check setup access / Contributor",self.setup_access),("Open Azure portal",self.open_azure_portal),("Refresh subscriptions",self.refresh_subscriptions),("Check missing subscription",self.check_subscription),("Refresh workspaces",self.discover)):
            self.button(tools,label,command).pack(fill="x",pady=(0,6))
        clientcard,body=self.card(connect,"Client profile","Optional. Save a familiar name and tenant for your next visit.")
        clientcard.grid(row=0,column=0,sticky="nsew",padx=(0,16),pady=(0,16))
        self.clientbox=self.form(body,"Saved client","client_name",values=[])
        self.clientbox.bind("<<ComboboxSelected>>",self.client_changed)
        self.button(body,"+ Add client",self.add_client).pack(fill="x",pady=(0,14))
        self.form(body,"Client slug","client_slug")
        self.button(body,"Save client profile",self.save_client).pack(fill="x",pady=(0,8))
        self.button(body,"Import configuration",self.import_config).pack(fill="x",pady=(0,8))
        manage=self.disclosure(body,"Manage saved profile")
        self.button(manage,"Delete saved client",self.delete_client,"Danger.TButton").pack(fill="x")
        self.button(body,"Open CyberQP",self.open_cyberqp).pack(fill="x",pady=(0,8))
        detailcard,body=self.card(connect,"Selected workspace","Confirm the destination before exporting evidence or configuring onboarding.")
        detailcard.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(0,16))
        self.identity=tk.StringVar(value="No workspace selected. Use Workspaces to sign in and select a destination.")
        self.details=tk.Text(body,width=1,height=7,wrap="word",font=("Consolas",10),
                             bg="#f4f7fa",fg="#233248",relief="flat",padx=14,pady=12)
        self.details.pack(fill="x",pady=(0,14))
        self.identity.trace_add("write",lambda *args:(self.render_details(),self.update_workspace_context()))
        self.render_details()
        actions=ttk.Frame(body,style="Card.TFrame");actions.pack(fill="x")
        self.button(actions,"Copy details",self.copy_workspace).pack(side="left")
        self.button(actions,"Open Sentinel audit",lambda:self.tabs.select(3),"Primary.TButton").pack(side="right")
        self.button(connect,"Configure client onboarding →",lambda:self.tabs.select(1)).grid(row=2,column=1,sticky="e",pady=(0,16))
        top,body=self.card(settings,"Client configuration",
            "Create an Azure Lighthouse delegation and prepare the client target-file pull request.")
        top.pack(fill="x",pady=(0,16))
        self.form(body,"Workspace identity label","label","primary")
        ttk.Label(body,text="The primary label uses workspace.yml. Additional labels keep separate workspace files and targets.",
                  style="Muted.TLabel",wraplength=650).pack(anchor="w",pady=(0,14))
        self.form(body,"Initial rule path - optional","rule_path")
        self.vars["allow_missing_mitre"]=tk.BooleanVar(value=False)
        cb=ttk.Checkbutton(body,text="Allow missing MITRE metadata (approved exception)",variable=self.vars["allow_missing_mitre"])
        cb.pack(anchor="w",pady=(0,8));self.controls.append((cb,"normal"))
        ttk.Label(body,text="An initial rule is committed disabled. Leave blank to create the target with no rules selected.",
                  style="Muted.TLabel",wraplength=650).pack(anchor="w")
        self.constantscard,body=self.card(settings,"Managing tenant defaults",
            "Set these once and reuse them for each client. Values are saved locally.")
        self.constantscard.pack(fill="x",pady=(0,16))
        group=self.section(body,"REPOSITORY","The private repository that receives the client's target-file pull request.")
        self.form_pair(group,[("GitHub organization","github_owner",self.settings.get("github_owner","")),("Repository","github_repo",self.settings.get("github_repo",""))])
        self.button(group,"Sign in to GitHub",self.github_login).pack(anchor="w")
        group=self.section(body,"DELEGATED ACCESS","Use the managing tenant and its groups. Azure Lighthouse grants the groups built-in roles in the client's subscription.")
        self.form(group,"Managing tenant ID","managing_tenant_id",self.settings.get("managing_tenant_id",""))
        self.form(group,"Deploy group Object ID","deploy_group_object_id",self.settings.get("deploy_group_object_id",""))
        self.form_pair(group,[("Preview group Object ID","read_group_object_id",self.settings.get("read_group_object_id","")),("Engineers group Object ID","engineer_group_object_id",self.settings.get("engineer_group_object_id",""))])
        group=self.section(body,"SHARED ENVIRONMENTS","Use existing GitHub environments with AZURE_CLIENT_ID and required reviewers. Onboarding verifies these environments.")
        self.form_pair(group,[("Preview environment","preview_environment",self.settings.get("preview_environment","")),("Production environment","production_environment",self.settings.get("production_environment",""))])
        optional=self.disclosure(body,"Optional delegation details",bool(self.settings.get("msp_offer_name") or self.settings.get("delegation_location")))
        self.form_pair(optional,[("Offer name","msp_offer_name",self.settings.get("msp_offer_name","")),("Delegation location","delegation_location",self.settings.get("delegation_location",""))])
        row=ttk.Frame(body,style="Card.TFrame");row.pack(fill="x",pady=(4,0))
        self.button(row,"Save defaults",self.save_settings,"Primary.TButton").pack(side="left",padx=(0,10))
        self.button(row,"Export configuration",self.export_config).pack(side="left")
        self.configfooter=ttk.Frame(settings)
        self.configfooter.pack(fill="x",pady=(0,16))
        self.button(self.configfooter,"Continue to review →",lambda:self.tabs.select(2),"Primary.TButton").pack(side="right")
        reviewcard,body=self.card(review,"Review the destination","Run a preview, review its results, then apply the same configuration.")
        reviewcard.pack(fill="x",pady=(0,16))
        self.summary=tk.StringVar(value="Select a workspace and configure the setup to create a preview.")
        ttk.Label(body,textvariable=self.summary,style="Muted.TLabel",wraplength=640,justify="left").pack(anchor="w",pady=(0,20))
        actions=ttk.Frame(body,style="Card.TFrame");actions.pack(fill="x")
        self.button(actions,"Preview setup",lambda:self.onboard(False),"Primary.TButton").pack(side="left",padx=(0,10))
        self.button(actions,"Apply reviewed setup",lambda:self.onboard(True)).pack(side="left",padx=(0,10))
        self.button(actions,"Sign out",self.logout).pack(side="right")
        logcard,body=self.card(review,"Operation activity","Preview checks, deployment results, and next steps appear here.")
        logcard.pack(fill="both",expand=True,pady=(0,16))
        logframe=ttk.Frame(body,style="Card.TFrame");logframe.pack(fill="both",expand=True)
        self.logbox=tk.Text(logframe,wrap="word",font=("Consolas",10),bg="#f6f8fc",fg="#354863",
                            height=16,state="disabled",bd=0,padx=14,pady=14,highlightthickness=0)
        logscroll=ttk.Scrollbar(logframe,orient="vertical",command=self.logbox.yview)
        logscroll.pack(side="right",fill="y");self.logbox.pack(fill="both",expand=True)
        self.logbox.configure(yscrollcommand=logscroll.set)
        auditcard,body=self.card(audit,"Build your evidence export",
            "Start with current configuration. Add only the logs your reviewer needs.")
        auditcard.pack(fill="x",pady=(0,16))
        ttk.Label(body,textvariable=self.identity,style="Muted.TLabel",wraplength=650,justify="left").pack(anchor="w",pady=(0,12))
        self.audit_flags={}
        def checkbox(parent,key,label,default=False):
            var=tk.BooleanVar(value=default);self.audit_flags[key]=var
            cb=ttk.Checkbutton(parent,text=label,variable=var)
            cb.pack(anchor="w",pady=3);self.controls.append((cb,"normal"))
        configs=ttk.LabelFrame(body,text="Current configuration",style="Card.TLabelframe",padding=16)
        configs.pack(fill="x",pady=(0,12))
        choices=ttk.Frame(configs,style="Card.TFrame");choices.pack(fill="x")
        choices.columnconfigure((0,1),weight=1,uniform="categories")
        for index,(key,(title,_,_)) in enumerate(CONFIGURATIONS.items()):
            cell=ttk.Frame(choices,style="Card.TFrame");cell.grid(row=index//2,column=index%2,sticky="ew")
            checkbox(cell,key,title,True)
        ttk.Label(configs,text="Snapshot taken now; available API settings may not cover every portal screen.",style="Muted.TLabel",wraplength=650).pack(anchor="w",pady=(8,0))
        logs=self.disclosure(body,"Add logs · optional")
        checkbox(logs,"audit","Sentinel configuration-change logs (SentinelAudit)")
        checkbox(logs,"health","Sentinel operational health logs (SentinelHealth)")
        today=datetime.now(timezone.utc).date()
        dates=ttk.Frame(logs,style="Card.TFrame");dates.pack(fill="x",pady=(10,0))
        left=ttk.Frame(dates,style="Card.TFrame");left.pack(side="left",fill="x",expand=True,padx=(0,16))
        right=ttk.Frame(dates,style="Card.TFrame");right.pack(side="left",fill="x",expand=True)
        self.form(left,"Log start date (UTC, YYYY-MM-DD)","audit_start",(today-timedelta(days=6)).isoformat())
        self.form(right,"Log end date (UTC, inclusive)","audit_end",today.isoformat())
        self.button(logs,"Choose security source logs…",self.choose_source_logs).pack(anchor="w")
        self.audit_source_tables=[]
        self.audit_source_summary=tk.StringVar(value="No source tables selected.")
        ttk.Label(logs,textvariable=self.audit_source_summary,style="Muted.TLabel",wraplength=650).pack(anchor="w",pady=8)
        self.form_pair(logs,[("Source export mode","audit_source_mode","sample",["sample","period"]),("Sample size per table (1–1000)","audit_sample_limit","100")])
        ttk.Label(logs,text="Sample: most recent records in the dates above. Period: collect selected dates, capped at 100,000 records per table; limits and failures are reported. Dates are ignored for configuration-only exports.",style="Muted.TLabel",wraplength=650,justify="left").pack(anchor="w",pady=(0,8))
        self.audit_scope_summary=tk.StringVar()
        scope=ttk.Label(body,textvariable=self.audit_scope_summary,style="Muted.TLabel",wraplength=600)
        scope.pack(fill="x",pady=(0,12));self.wrap_to_parent(scope)
        for var in self.audit_flags.values():var.trace_add("write",lambda *args:self.update_audit_scope_summary())
        self.audit_source_summary.trace_add("write",lambda *args:self.update_audit_scope_summary())
        self.update_audit_scope_summary()
        self.button(body,"Export evidence",self.collect_audit,"Primary.TButton").pack(anchor="w")
        resultcard,body=self.card(audit,"Export results","Readable configuration report, raw JSON, selected log CSVs, and collection status.")
        resultcard.pack(fill="x",pady=(0,16))
        self.audit_summary=tk.StringVar(value="No export collected yet.")
        ttk.Label(body,textvariable=self.audit_summary,style="Muted.TLabel",wraplength=650,justify="left").pack(anchor="w",pady=(0,16))
        actions=ttk.Frame(body,style="Card.TFrame");actions.pack(fill="x")
        self.button(actions,"Open report",lambda:self.open_evidence(True)).pack(side="left",padx=(0,10))
        self.button(actions,"Open export folder",self.open_evidence).pack(side="left")
        self.tabs.bind("<<NotebookTabChanged>>",self.page_changed)
        self.page_changed()
        self.root.bind("<MouseWheel>",self.scroll_page)

    def scroll_page(self,event):
        if event.widget.winfo_toplevel()!=self.root or event.widget.winfo_class() in ("Text","TCombobox","Treeview"):
            return
        canvas=self.page_canvases[self.tabs.index("current")]
        if canvas.yview()!=(0.0,1.0):canvas.yview_scroll(int(-event.delta/120),"units")

    def page_changed(self,event=None):
        index=self.tabs.index("current")
        titles=["Your client workspaces","Client onboarding","Review & apply","Sentinel audit","Analytics rules"]
        subtitles=["Discover the right workspace without navigating the Azure portal.",
                   "Deploy a Lighthouse delegation and open the client's target-file pull request.",
                   "Confirm the destination, preview the setup, then apply.",
                   "Export selected Sentinel configuration and logs for this workspace.",
                   "Browse enabled and disabled analytics rules for the selected workspace."]
        self.page_title.set(titles[index]);self.page_subtitle.set(subtitles[index])
        for i,(row,number,name,description) in enumerate(self.nav):
            selected=i==index
            background="#254655" if selected else "#142638"
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
                elif kind=="signin_finished":
                    self.finish_signin()
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
        self.activity_badge.configure(text="WORKING" if busy else "READY")
        if busy:
            self.progress.pack(fill="x",pady=(8,0));self.progress.start(12)
        else:
            self.progress.stop();self.progress.pack_forget()
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
            options=self.audit_options()
            if options["audit"] or options["health"] or options["source_tables"]: date_range(start,end)
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
            self.audit_summary.set("Workspace: "+workspace["workspace_name"]+"\n"+
                "\n".join(r["title"]+": "+r["status"].replace("_"," ")+f" ({r['record_count']} records)" for r in rows)+
                "\n\nSaved to: "+result["folder"])
            self.status.set("Evidence package saved. Review collection statuses and limitations in the report.")
            self.tabs.select(3)
        self.work("Exporting selected Sentinel configuration and logs...",
                  lambda:collect_evidence(session,workspace,start,end,destination,options),done,page=3)

    def update_audit_scope_summary(self):
        count=sum(self.audit_flags[key].get() for key in CONFIGURATIONS)
        activity=sum(self.audit_flags[key].get() for key in ("audit","health"))
        self.audit_scope_summary.set(f"Selected: {count} configuration categories · {activity} Sentinel activity logs · {len(self.audit_source_tables)} source tables")

    def audit_options(self):
        sample=self.vars["audit_sample_limit"].get().strip()
        require(sample.isascii() and sample.isdigit(),"Enter a sample size from 1 to 1000.")
        options=validate_options(dict(configurations=[k for k in CONFIGURATIONS if self.audit_flags[k].get()],
            audit=self.audit_flags["audit"].get(),health=self.audit_flags["health"].get(),
            source_tables=list(self.audit_source_tables),source_mode=self.vars["audit_source_mode"].get(),sample_limit=int(sample)))
        require(options["configurations"] or options["audit"] or options["health"] or options["source_tables"],"Select configuration or logs to export.")
        return options

    def reset_audit_selection(self):
        self.audit_source_tables=[]
        self.audit_source_summary.set("No source tables selected.")
        for key in ("audit","health"): self.audit_flags[key].set(False)
        self.evidence_folder=None
        self.audit_summary.set("No export collected for this workspace yet.")

    def choose_source_logs(self):
        if self.busy:return
        if not self.session or not self.workspace:
            messagebox.showerror("Source logs","Sign in and select a workspace first.",parent=self.root);return
        workspace=dict(self.workspace);session=self.session
        self.work("Loading source table inventory…",lambda:list_log_tables(session,workspace),
                  lambda result:self.show_source_picker(result,session,workspace),page=3)

    def show_source_picker(self,result,session,workspace):
        if self.workspace != workspace:return
        dialog=tk.Toplevel(self.root);dialog.title("Choose security source logs");dialog.geometry("980x620");dialog.minsize(900,520);dialog.transient(self.root)
        dialog.grab_set()
        frame=ttk.Frame(dialog,padding=16);frame.pack(fill="both",expand=True)
        ttk.Label(frame,text="Security source logs",font=("Segoe UI",18,"bold"),wraplength=840).pack(anchor="w")
        ttk.Label(frame,text="Select up to 20 tables with Ctrl/Shift. Inventory: "+result["status"].replace("_"," ")+". "+result.get("error", ""),wraplength=840).pack(anchor="w",pady=8)
        treeframe=ttk.Frame(frame);treeframe.pack(fill="both",expand=True)
        tree=ttk.Treeview(treeframe,columns=("plan","count","latest","status"),selectmode="extended",height=12)
        tree.heading("#0",text="Table");tree.column("#0",width=210)
        for key,label,width in (("plan","Plan",80),("count","Period records",100),("latest","Latest event (UTC)",190),("status","Preview status",160)):
            tree.heading(key,text=label);tree.column(key,width=width)
        scrollbar=ttk.Scrollbar(treeframe,orient="vertical",command=tree.yview);scrollbar.pack(side="right",fill="y")
        tree.configure(yscrollcommand=scrollbar.set);tree.pack(fill="both",expand=True)
        for row in sorted(result["records"],key=lambda r:r.get("name","")):
            name=row.get("name","")
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}",name) or name in ("SentinelAudit","SentinelHealth") or tree.exists(name):continue
            tree.insert("", "end",iid=name,text=name,values=((row.get("properties") or {}).get("plan","Unknown"),"—","—","Not previewed"))
            if name in self.audit_source_tables:tree.selection_add(name)
        note=tk.StringVar(value="Counts and latest timestamps use the log dates in the audit tab. No event payloads are fetched by preview. Basic/Auxiliary tables may be unavailable through this query API.")
        ttk.Label(frame,textvariable=note,wraplength=840).pack(anchor="w",pady=10)
        buttons=ttk.Frame(frame);buttons.pack(fill="x")
        previewing=[False]
        def close():
            if not previewing[0]:dialog.destroy()
        dialog.protocol("WM_DELETE_WINDOW",close)
        def selected():
            tables=list(tree.selection())
            validate_options(dict(source_tables=tables))
            return tables
        def preview():
            try:
                tables=selected();require(tables,"Select tables to preview.")
                start,end=self.vars["audit_start"].get().strip(),self.vars["audit_end"].get().strip();date_range(start,end)
            except Stop as error:messagebox.showerror("Source logs",str(error),parent=dialog);return
            previewing[0]=True;preview_button.state(["disabled"]);use_button.state(["disabled"])
            note.set("Counting selected tables for "+start+" through "+end+" UTC…")
            outcome=queue.Queue()
            def run():
                try:outcome.put((preview_log_tables(session,workspace,start,end,tables),None))
                except Exception as error:outcome.put((None,str(error)))
            def poll():
                try:rows,error=outcome.get_nowait()
                except queue.Empty:dialog.after(100,poll);return
                previewing[0]=False;preview_button.state(["!disabled"]);use_button.state(["!disabled"])
                if error:note.set("Preview failed: "+error);return
                note.set("Preview: "+start+" through "+end+" UTC. Counts reflect current account visibility; unavailable or partial results are not zero.")
                for row in rows:
                    record=row["records"][0] if row["records"] else {}
                    complete=row["status"] in ("collected","no_records")
                    tree.set(row["table"],"count",record.get("Records","—") if complete else "Unknown")
                    tree.set(row["table"],"latest",record.get("LatestEvent") or "—")
                    tree.set(row["table"],"status",row["status"].replace("_"," "))
            threading.Thread(target=run,daemon=True).start();dialog.after(100,poll)
        def use():
            try:tables=selected()
            except Stop as error:messagebox.showerror("Source logs",str(error),parent=dialog);return
            self.audit_source_tables=tables
            self.audit_source_summary.set("Selected source tables: "+", ".join(tables) if tables else "No source tables selected.")
            dialog.destroy()
        preview_button=ttk.Button(buttons,text="Preview selected counts",command=preview,style="Secondary.TButton");preview_button.pack(side="left")
        use_button=ttk.Button(buttons,text="Use selected tables",command=use,style="Primary.TButton");use_button.pack(side="right")

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
        self.rules_page.reset()
        self.subscriptions=[]; self.workspaces=[]; self.workspace=None; self.plan=None
        self.reset_audit_selection()
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
        try:
            require(v["client_name"], "Supply a client display name. Tenant is optional.")
            slug(v["client_slug"], "Client slug", CLIENT_MAXIMUM)
        except Stop as error:
            messagebox.showerror("Client details",str(error),parent=self.root); return
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

    def bring_signin_forward(self):
        if not self.signin_active:return
        try:
            found=self.signin_handoff and self.signin_handoff.bring_forward(manual=True)
        except OSError:found=False
        if not found:
            self.status.set("Sign-in is still opening or Windows blocked focus. Use Alt+Tab to select the Microsoft sign-in window.")

    def watch_signin(self):
        self.signin_timer=None
        if not self.signin_active:return
        if self.signin_handoff and time.monotonic()<self.signin_deadline:
            try:self.signin_handoff.bring_forward()
            except OSError:pass
            self.signin_timer=self.root.after(750,self.watch_signin)

    def finish_signin(self):
        self.signin_active=False
        if self.signin_timer is not None:
            self.root.after_cancel(self.signin_timer);self.signin_timer=None
        self.signin_handoff=None
        self.signin_button.configure(state="disabled")

    def login(self):
        if self.busy:return
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
        self.finish_signin()
        self.signin_handoff=create_handoff()
        self.signin_active=True
        self.signin_deadline=time.monotonic()+90
        self.signin_button.configure(state="normal" if self.signin_handoff else "disabled")
        self.watch_signin()
        def task():
            try:
                if old:
                    old.logout()
                return self.session.login(hint)
            finally:
                self.events.put(("signin_finished",None))
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
        self.rules_page.reset()
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
        self.rules_page.reset()
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
        self.reset_audit_selection()
        self.rules_page.reset()
        selected=dict(self.workspace)
        self.root.after_idle(lambda:self.rules_page.load() if self.workspace==selected and not self.busy else None)
        account=self.session.account or {}
        self.identity.set("Signed in: "+account.get("user",{}).get("name","unknown")+"\n"+
            "\n".join(label+": "+self.workspace[key] for key,label in
                      (("tenant_id","Tenant ID"),("subscription_id","Subscription ID"),("resource_group","Resource group"),
                       ("workspace_name","Workspace name"),("workspace_id","Workspace ID"))))

    def setup_access(self):
        if self.busy:return
        index=self.subbox.current()
        if not self.session or index<0:
            messagebox.showinfo("Select subscription","Sign in and select the client subscription first.",parent=self.root);return
        session=self.session
        sub=self.subscriptions[index]
        target=dict(tenant_id=sub["tenantId"],subscription_id=sub["id"])
        def reviewed(plan):
            if self.session is not session:return
            if not plan["needs_role"]:
                messagebox.showinfo("Setup access", "Your current session already permits Microsoft.ManagedServices registration. No additional Contributor role is needed. Preview setup will check the remaining onboarding permissions.",parent=self.root);return
            text=("Assign active Contributor to the signed-in user?\n\nAccount: " + plan["account"] +
                  "\nUser object ID: " + plan["principal"] + "\nClient tenant: " + target["tenant_id"] +
                  "\nScope: " + plan["scope"] +
                  "\n\nThis permits management of resources throughout this subscription. It remains assigned until removed; CyberQP session expiry does not remove this Azure assignment. Azure policies and role conditions still apply.")
            if not messagebox.askokcancel("Review Contributor assignment",text,parent=self.root):return
            self.plan=None
            self.work("Assigning reviewed Contributor access...",lambda:apply_contributor(session,plan),
                      lambda result:(self.log(result),self.status.set(result)),page=0)
        self.work("Checking current subscription access...",lambda:access_plan(session,target),reviewed,page=0)

    def payload(self):
        v=self.values()
        require(self.workspace and self.session, "Sign in and select a discovered workspace first.")
        require(v["tenant"] == self.auth_hint, "Tenant field changed since sign-in. Sign in to the selected tenant again.")
        sub=self.subscriptions[self.subbox.current()]
        require(sub["tenantId"].lower()==self.workspace["tenant_id"].lower(), "Client/tenant selection changed.")
        validate_target(v["client_slug"],v["label"])
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
