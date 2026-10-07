"""A focused client -> sign-in -> workspace flow; advanced actions stay expandable."""
import tkinter as tk
from tkinter import ttk
from .desktop_backend import CYBERQP_PORTALS


def build_connection(app, parent):
    parent.columnconfigure(0,weight=1,uniform='connection')
    parent.columnconfigure(1,weight=1,uniform='connection')
    card,left=app.card(parent,'Choose a client','Select a saved profile, or add a client to get started.')
    card.grid(row=0,column=0,sticky='nsew',padx=(0,14),pady=(0,14))
    app.clientbox=app.form(left,'Saved client','client_name',values=[])
    app.clientbox.bind('<<ComboboxSelected>>',app.client_changed)
    app.button(left,'Add client',app.add_client).pack(fill='x',pady=(0,6))
    app.profile_options=app.disclosure(left,'Manage saved profile')
    app.form(app.profile_options,'Client slug','client_slug')
    app.button(app.profile_options,'Save profile changes',app.save_client).pack(fill='x',pady=(0,6))
    app.button(app.profile_options,'Import configuration',app.import_config).pack(fill='x',pady=(0,6))
    app.button(app.profile_options,'Delete saved client',app.delete_client,'Danger.TButton').pack(fill='x')
    app.button(left,'Open CyberQP',app.open_cyberqp).pack(fill='x',pady=(0,4))
    hint=ttk.Label(left,text='Need temporary access? Activate it in CyberQP before signing in.',style='Muted.TLabel')
    hint.pack(fill='x');app.wrap_to_parent(hint)

    card,right=app.card(parent,'Connect to Azure','Use this client’s authorized account. Microsoft handles sign-in and MFA.')
    card.grid(row=0,column=1,sticky='nsew',pady=(0,14))
    app.form(right,'Client tenant ID or domain (optional)','tenant')
    app.signin_primary=app.button(right,'Sign in & discover',app.login,'Primary.TButton')
    app.signin_primary.pack(fill='x',pady=(0,6))
    app.signin_options=app.disclosure(right,'Sign-in options')
    app.form(app.signin_options,'Sign-in method','login_method','Browser',['Browser','Windows account window'])
    hint=ttk.Label(app.signin_options,text='A saved profile fills in the tenant. Leave it blank only if you need to discover available directories.',style='Muted.TLabel')
    hint.pack(fill='x');app.wrap_to_parent(hint)
    app.signin_progress=ttk.Frame(right,style='Card.TFrame')
    app.signin_button=ttk.Button(app.signin_progress,text='Show Microsoft sign-in',command=app.bring_signin_forward,state='disabled')
    app.signin_button.pack(fill='x',pady=(4,6))
    app.cancel_signin_button=ttk.Button(app.signin_progress,text='Cancel sign-in',command=app.cancel_signin,state='disabled')
    app.cancel_signin_button.pack(fill='x',pady=(0,6))
    hint=ttk.Label(app.signin_progress,text='Closed the browser tab? Cancel here to stop waiting.',style='Muted.TLabel')
    hint.pack(fill='x');app.wrap_to_parent(hint)

    card,body=app.card(parent,'Choose a workspace','After sign-in, select the destination for your work.')
    card.grid(row=1,column=0,columnspan=2,sticky='ew',pady=(0,14))
    app.connection_note=tk.StringVar(value='Sign in above to load your subscriptions and workspaces.')
    note=ttk.Label(body,textvariable=app.connection_note,style='Muted.TLabel')
    note.pack(fill='x',pady=(0,10));app.wrap_to_parent(note)
    row=ttk.Frame(body,style='Card.TFrame');row.pack(fill='x')
    row.columnconfigure(0,weight=1,uniform='selectors');row.columnconfigure(1,weight=1,uniform='selectors')
    sub=ttk.Frame(row,style='Card.TFrame');sub.grid(row=0,column=0,sticky='ew',padx=(0,14))
    ws=ttk.Frame(row,style='Card.TFrame');ws.grid(row=0,column=1,sticky='ew')
    app.subbox=app.form(sub,'Subscription','subscription',values=[])
    app.subbox.bind('<<ComboboxSelected>>',app.subscription_changed)
    app.wsbox=app.form(ws,'Log Analytics workspace','workspace',values=[])
    app.wsbox.bind('<<ComboboxSelected>>',app.workspace_changed)
    actions=ttk.Frame(body,style='Card.TFrame');actions.pack(fill='x',pady=(0,6))
    app.workspace_actions=[]
    for label,page,style in (('View analytics rules',4,'Primary.TButton'),('Export audit evidence',3,'Secondary.TButton'),('Onboard workspace',1,'Secondary.TButton')):
        button=app.button(actions,label,lambda n=page:app.tabs.select(n),style)
        button.pack(side='left',padx=(0,8));app.workspace_actions.append(button)
    app.workspace_details=app.disclosure(body,'Workspace identifiers')
    app.identity=tk.StringVar(value='No authenticated workspace selected.')
    app.details=tk.Text(app.workspace_details,width=1,height=7,wrap='word',font=('Consolas',10),
        bg='#f4f7fa',fg='#233248',relief='flat',padx=12,pady=10)
    app.details.pack(fill='x',pady=(0,8))
    app.button(app.workspace_details,'Copy workspace details',app.copy_workspace).pack(anchor='w')
    app.identity.trace_add('write',lambda *args:(app.render_details(),app.update_workspace_context(),app.sync_connection()))
    app.render_details()

    card,body=app.card(parent,'Access & support','Use these tools when setting up a client or troubleshooting access.')
    card.grid(row=2,column=0,columnspan=2,sticky='ew',pady=(0,14))
    app.first_time_tools=app.disclosure(body,'First-time access setup')
    app.offer_access_management=tk.BooleanVar(value=False)
    offer=ttk.Checkbutton(app.first_time_tools,text='Review access management after the next sign-in',variable=app.offer_access_management)
    offer.pack(anchor='w');app.controls.append((offer,'normal'))
    hint=ttk.Label(app.first_time_tools,text='For authorized Global Administrators. Enter the client tenant ID above. Review any access change before applying it.',style='Muted.TLabel')
    hint.pack(fill='x',pady=(4,10));app.wrap_to_parent(hint)
    for label,command in (('Enable Azure access management',app.azure_access_management),('Check setup access / Contributor',app.setup_access)):
        app.button(app.first_time_tools,label,command).pack(fill='x',pady=(0,6))
    app.connection_support=app.disclosure(body,'Connection troubleshooting')
    for label,command in (('Refresh setup access',app.refresh_setup_access),('Sign in again / refresh session',app.reconnect_azure),
                          ('Refresh subscriptions',app.refresh_subscriptions),('Refresh workspaces',app.discover),
                          ('Check missing subscription',app.check_subscription),('Open Azure portal',app.open_azure_portal),('Sign out',app.logout)):
        app.button(app.connection_support,label,command).pack(fill='x',pady=(0,6))
    app.form(app.connection_support,'CyberQP region','cyberqp_region','US',list(CYBERQP_PORTALS))
    activity=app.disclosure(body,'Saved access activity')
    app.access_summary=tk.StringVar(value='Select a subscription to view saved access activity.')
    label=ttk.Label(activity,textvariable=app.access_summary,style='Muted.TLabel')
    label.pack(fill='x');app.wrap_to_parent(label)
    app.sync_connection()
