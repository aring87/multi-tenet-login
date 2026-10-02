"""Select client workspaces and review assignment changes before a draft PR."""
import copy
import json
import re
import tkinter as tk
from tkinter import ttk
import webbrowser

from .rule_assignments import AssignmentService, assignment_diff


class AssignmentDialog:
    def __init__(self, app, snapshot=None, rule=None, record=None):
        self.app = app
        self.snapshot = copy.deepcopy(snapshot) if snapshot else {'clients': []}
        self.rule = copy.deepcopy(rule) if rule else {'name': record['plan']['name'], 'path': record['plan']['rule_path']}
        self.service = AssignmentService()
        self.folder = app.rule_builder.data_dir / 'assignment-requests'
        self.record, self.locked = record, record is not None
        self.selected, self.visible = set(), {}
        self.window = tk.Toplevel(app.root)
        self.window.title('Assign rule to clients'); self.window.geometry('1050x800'); self.window.minsize(900,720)
        self.window.transient(app.root); self.window.grab_set(); self.window.protocol('WM_DELETE_WINDOW',self.close)
        body = ttk.Frame(self.window,padding=18); body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=1); body.rowconfigure(3,weight=1); body.rowconfigure(5,weight=2)
        ttk.Label(body,text='Assign rule to clients',font=('Segoe UI',16,'bold')).grid(row=0,column=0,sticky='w')
        ttk.Label(body,text=self.rule['name']+'\n'+self.rule['path'],wraplength=850).grid(row=1,column=0,sticky='ew',pady=(5,12))
        toolbar=ttk.Frame(body);toolbar.grid(row=2,column=0,sticky='ew',pady=(0,8))
        ttk.Label(toolbar,text='Search').pack(side='left',padx=(0,8))
        self.search=tk.StringVar(); self.search_box=ttk.Entry(toolbar,textvariable=self.search,width=22)
        self.search_box.pack(side='left',fill='x',expand=True,padx=(0,12))
        ttk.Label(toolbar,text='Rule state for selected clients').pack(side='left',padx=(0,8))
        self.enabled=tk.StringVar(value='Disabled')
        self.state_box=ttk.Combobox(toolbar,textvariable=self.enabled,values=('Disabled','Enabled'),state='readonly',width=10)
        self.state_box.pack(side='right')
        frame=ttk.Frame(body);frame.grid(row=3,column=0,sticky='nsew')
        self.clients=ttk.Treeview(frame,columns=('pick','client','workspace','target','assignment','state'),show='headings',height=7)
        for key,title,width in [('pick','Pick',40),('client','Client',140),('workspace','Workspace',150),('target','Target',210),('assignment','Current rule',120),('state','Target state',110)]:
            self.clients.heading(key,text=title);self.clients.column(key,width=width,minwidth=40)
        scroll=ttk.Scrollbar(frame,command=self.clients.yview);scroll.pack(side='right',fill='y')
        self.clients.configure(yscrollcommand=scroll.set);self.clients.pack(fill='both',expand=True)
        self.clients.bind('<Button-1>',self.clicked);self.clients.bind('<space>',self.toggle_focused)
        selection=ttk.Frame(body);selection.grid(row=4,column=0,sticky='ew',pady=8)
        self.all_button=ttk.Button(selection,text='Select shown',command=self.select_shown);self.all_button.pack(side='left')
        self.clear_button=ttk.Button(selection,text='Clear selection',command=self.clear);self.clear_button.pack(side='left',padx=8)
        self.count=tk.StringVar();ttk.Label(selection,textvariable=self.count).pack(side='left')
        review=ttk.Frame(body);review.grid(row=5,column=0,sticky='nsew')
        self.text=tk.Text(review,height=12,width=50,wrap='word',state='disabled',font=('Consolas',10))
        scroll=ttk.Scrollbar(review,command=self.text.yview);self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.text.pack(fill='both',expand=True)
        self.notice=tk.StringVar(value='Unselected clients stay unchanged. This creates a review PR; it does not deploy or enable workspace targets.')
        label=ttk.Label(body,textvariable=self.notice,wraplength=850);label.grid(row=6,column=0,sticky='ew',pady=10)
        body.bind('<Configure>',lambda e:label.configure(wraplength=max(400,e.width)),add='+')
        actions=ttk.Frame(body);actions.grid(row=7,column=0,sticky='ew')
        self.prepare_button=ttk.Button(actions,text='Prepare review',command=self.prepare)
        self.submit_button=ttk.Button(actions,text='Create / recover draft PR',command=self.submit,state='disabled')
        self.open_button=ttk.Button(actions,text='Open pull request',command=self.open_link,state='disabled')
        self.close_button=ttk.Button(actions,text='Close',command=self.close)
        for button in (self.prepare_button,self.submit_button,self.open_button,self.close_button):button.pack(side='left',padx=(0,8))
        self.search.trace_add('write',lambda *a:self.render_clients())
        self.enabled.trace_add('write',lambda *a:self.invalidate())
        self.render_clients()
        if record:self.render_review()
        self.refresh_controls()

    def close(self):
        if not self.app.busy:self.window.destroy()

    def render_clients(self):
        self.clients.delete(*self.clients.get_children());self.visible={}
        term=self.search.get().strip().casefold()
        for i,c in enumerate(sorted(self.snapshot['clients'],key=lambda c:(c['name'].casefold(),c['target'].casefold()))):
            if term and term not in ' '.join((c['name'],c['workspace'],c['target'])).casefold():continue
            self.visible[str(i)]=c['target']
            assignment=next((a for a in c['assignments'] if a['path']==self.rule['path']),None)
            effective=None if assignment is None else assignment.get('overrides',{}).get('enabled',self.rule['raw'].get('enabled'))
            current='Unassigned' if assignment is None else 'Enabled' if effective is True else 'Disabled' if effective is False else 'Not specified'
            self.clients.insert('','end',iid=str(i),values=('Yes' if c['target'] in self.selected else '',c['name'],c['workspace'],c['target'],current,'Enabled' if c['raw']['enabled'] else 'Disabled'))
        hidden=len(self.selected-set(self.visible.values()))
        self.count.set(f'{len(self.selected)} selected ({hidden} hidden by search)')

    def invalidate(self):
        if self.locked or self.app.busy:return
        self.record=None;self.show('');self.refresh_controls()
        self.notice.set('Selection changed. Prepare a fresh review. Unselected clients stay unchanged.')
        self.render_clients()

    def toggle(self,item):
        if self.locked or self.app.busy:return
        target=self.visible.get(item)
        if target is None:return
        if target in self.selected:self.selected.remove(target)
        else:self.selected.add(target)
        self.invalidate()
        if self.clients.exists(item):self.clients.focus(item);self.clients.selection_set(item)

    def clicked(self,event):
        item=self.clients.identify_row(event.y)
        if item:self.toggle(item);return 'break'

    def toggle_focused(self,event):self.toggle(self.clients.focus());return 'break'

    def select_shown(self):
        if not self.locked and not self.app.busy:self.selected.update(self.visible.values());self.invalidate()

    def clear(self):
        if not self.locked and not self.app.busy:self.selected.clear();self.invalidate()

    def show(self,value):
        self.text.configure(state='normal');self.text.delete('1.0','end');self.text.insert('1.0',value);self.text.configure(state='disabled')

    def refresh_controls(self):
        for widget in (self.search_box,self.all_button,self.clear_button,self.prepare_button):widget.configure(state='disabled' if self.locked else 'normal')
        self.state_box.configure(state='disabled' if self.locked else 'readonly')
        self.submit_button.configure(state='normal' if self.record and not self.record.get('pull_request') else 'disabled')
        self.open_button.configure(state='normal' if self.record and self.record.get('pull_request') else 'disabled')
        self.close_button.configure(state='normal')

    def work(self,title,task,done):
        if self.app.busy:return
        for widget in (self.search_box,self.state_box,self.all_button,self.clear_button,self.prepare_button,self.submit_button,self.open_button,self.close_button):widget.configure(state='disabled')
        self.notice.set(title)
        def wrapped():
            try:return task(),None
            except Exception as error:return None,str(error)
        def finished(result):
            value,error=result
            if error:self.notice.set(error+(' Recover this request from '+str(self.folder) if self.locked else ''))
            else:done(value)
            self.refresh_controls()
        self.app.work(title,wrapped,finished,page=5)

    def prepare(self):
        if self.app.busy or self.locked:return
        self.record=None;self.show('')
        targets=sorted(self.selected);enabled=self.enabled.get()=='Enabled'
        def done(record):self.record=record;self.render_review()
        self.work('Preparing client assignment diffs...',lambda:self.service.prepare(self.snapshot['repository'],self.snapshot['revision'],self.rule['path'],targets,enabled),done)

    def render_review(self):
        p=self.record['plan']
        lines=['Repository: '+p['repository'],'GitHub account: '+p['identity'],'Reviewed main: '+p['base_sha'],
               'Rule: '+p['rule_path'],'Requested rule state: '+('Enabled' if p['enabled'] else 'Disabled'),
               'Changed client files: '+str(len(p['files'])),'']
        for c in p['impact']:
            lines.append(c['target']+' | '+c['workspace']+' | '+c['action']+' | target '+('enabled' if c['target_enabled'] else 'disabled')+' | overrides: '+json.dumps(c['overrides']))
        lines += ['',*p['warnings'],'',assignment_diff(p)]
        self.show('\n'.join(lines))
        self.notice.set('Review all file changes before creating the draft PR. Existing repository workflows may run; the app does not merge or dispatch deployments.')
        if self.record.get('pull_request'):self.notice.set('Draft PR: '+self.record['pull_request'])

    def submit(self):
        if self.app.busy or not self.record or self.record.get('pull_request'):return
        self.locked=True
        def done(url):self.notice.set('Draft PR: '+url);self.app.catalog_page.note.set('Assignment PR: '+url+'. After merge, refresh the catalog before previewing.')
        self.work('Creating or recovering assignment review...',lambda:self.service.submit(self.record,self.folder),done)

    def open_link(self):
        if self.record and self.record.get('pull_request'):
            url=self.record['pull_request']
            if re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/\d+',url):webbrowser.open(url)
