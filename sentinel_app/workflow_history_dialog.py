"""Search saved preview/deployment requests and explicitly refresh their run status."""
import json
import tkinter as tk
from tkinter import ttk, filedialog
import webbrowser

from .workflow_history import list_receipts, load_receipt, summary, workflow_url, WorkflowHistoryService


class WorkflowHistoryDialog:
    def __init__(self,app):
        self.app=app;self.folder=app.rule_builder.data_dir
        self.service=WorkflowHistoryService();self.rows=[];self.visible={};self.current=None
        self.window=tk.Toplevel(app.root);self.window.title('Preview and deployment history')
        self.window.geometry('1080x800');self.window.minsize(920,720)
        self.window.transient(app.root);self.window.grab_set();self.window.protocol('WM_DELETE_WINDOW',self.close)
        body=ttk.Frame(self.window,padding=18);body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=1);body.rowconfigure(3,weight=1);body.rowconfigure(4,weight=1)
        ttk.Label(body,text='Preview and deployment history',font=('Segoe UI',16,'bold')).grid(row=0,column=0,sticky='w')
        ttk.Label(body,text='Saved on this computer. Refresh a request to read GitHub status; this screen cannot submit or retry deployments.',
                  wraplength=860).grid(row=1,column=0,sticky='ew',pady=(6,12))
        bar=ttk.Frame(body);bar.grid(row=2,column=0,sticky='ew',pady=(0,10))
        ttk.Label(bar,text='Search rule, client, workspace or repository').pack(side='left',padx=(0,8))
        self.search=tk.StringVar();self.search_box=ttk.Entry(bar,textvariable=self.search)
        self.search_box.pack(side='left',fill='x',expand=True,padx=(0,8))
        self.mode=tk.StringVar(value='All requests')
        self.mode_box=ttk.Combobox(bar,textvariable=self.mode,values=('All requests','Preview','Deploy'),state='readonly',width=15)
        self.mode_box.pack(side='right')
        frame=ttk.Frame(body);frame.grid(row=3,column=0,sticky='nsew')
        self.table=ttk.Treeview(frame,columns=('mode','rule','repo','clients','state','created'),show='headings',selectmode='browse',height=7)
        for key,label,width in (('mode','Type',75),('rule','Rule',190),('repo','Repository',180),('clients','Workspaces',80),('state','Saved status',240),('created','Requested (UTC)',170)):
            self.table.heading(key,text=label);self.table.column(key,width=width,minwidth=50)
        self.scroll_table(frame,self.table)
        frame=ttk.Frame(body);frame.grid(row=4,column=0,sticky='nsew',pady=10)
        self.detail=tk.Text(frame,height=9,width=60,wrap='word',state='disabled',font=('Consolas',10))
        scroll=ttk.Scrollbar(frame,command=self.detail.yview);scroll.pack(side='right',fill='y')
        self.detail.configure(yscrollcommand=scroll.set);self.detail.pack(fill='both',expand=True)
        frame=ttk.Frame(body);frame.grid(row=5,column=0,sticky='ew')
        self.runs=ttk.Treeview(frame,columns=('batch','run','state','checked'),show='headings',selectmode='browse',height=4)
        for key,label,width in (('batch','Batch',50),('run','Run ID',105),('state','Last checked result',465),('checked','Checked (UTC)',180)):
            self.runs.heading(key,text=label);self.runs.column(key,width=width,minwidth=40)
        self.scroll_table(frame,self.runs)
        self.notice=tk.StringVar(value='Loading saved requests...')
        label=ttk.Label(body,textvariable=self.notice,wraplength=850);label.grid(row=6,column=0,sticky='ew',pady=10)
        body.bind('<Configure>',lambda e:label.configure(wraplength=max(400,e.width)),add='+')
        actions=ttk.Frame(body);actions.grid(row=7,column=0,sticky='ew')
        self.reload_button=ttk.Button(actions,text='Reload saved history',command=self.reload)
        self.status_button=ttk.Button(actions,text='Refresh GitHub status',command=self.refresh_status)
        self.open_button=ttk.Button(actions,text='Open selected run / Actions',command=self.open_run)
        self.file_button=ttk.Button(actions,text='Open receipt file',command=self.open_file)
        self.close_button=ttk.Button(actions,text='Close',command=self.close)
        self.buttons=(self.reload_button,self.status_button,self.open_button,self.file_button,self.close_button)
        for button in self.buttons:button.pack(side='left',padx=(0,8))
        self.search.trace_add('write',lambda *args:self.render())
        self.mode.trace_add('write',lambda *args:self.render())
        self.table.bind('<<TreeviewSelect>>',self.selected)
        self.reload()

    def scroll_table(self,frame,table):
        vertical=ttk.Scrollbar(frame,command=table.yview)
        horizontal=ttk.Scrollbar(frame,orient='horizontal',command=table.xview)
        table.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        table.grid(row=0,column=0,sticky='nsew');vertical.grid(row=0,column=1,sticky='ns');horizontal.grid(row=1,column=0,sticky='ew')
        frame.columnconfigure(0,weight=1);frame.rowconfigure(0,weight=1)

    def close(self):
        if not self.app.busy:self.window.destroy()

    def controls(self):
        busy=self.app.busy
        for button in self.buttons:button.configure(state='disabled' if busy else 'normal')
        if not self.current or busy:
            self.status_button.configure(state='disabled');self.open_button.configure(state='disabled')
        self.search_box.configure(state='disabled' if busy else 'normal')
        self.mode_box.configure(state='disabled' if busy else 'readonly')

    def work(self,title,task,done):
        if self.app.busy:return
        for button in self.buttons:button.configure(state='disabled')
        self.search_box.configure(state='disabled');self.mode_box.configure(state='disabled');self.notice.set(title)
        def wrapped():
            try:return task(),None
            except Exception as error:return None,str(error)
        def finished(result):
            value,error=result
            if error:self.notice.set(error)
            else:done(value)
            self.controls()
        self.app.work(title,wrapped,finished,page=5)

    def reload(self):
        def done(result):
            self.rows,issues=result;self.render()
            self.notice.set(f'{len(self.rows)} saved requests. GitHub status is only updated when you request it.'+
                ('\n'+'\n'.join(issues[:3]) if issues else ''))
        self.work('Reading locally saved workflow receipts...',lambda:list_receipts(self.folder),done)

    def render(self):
        self.table.delete(*self.table.get_children());self.visible={}
        term=self.search.get().strip().casefold();mode=self.mode.get()
        for i,row in enumerate(self.rows):
            record=row['record'];p=record['plan']
            if mode!='All requests' and row['mode']!=mode.lower():continue
            haystack=' '.join([p['rule_path'],p['repository'],record['request_id'],*p['targets'],
                *[c[k] for c in p['clients'] for k in ('name','workspace')]]).casefold()
            if term and term not in haystack:continue
            self.visible[str(i)]=row
            self.table.insert('','end',iid=str(i),values=(row['mode'].title(),p['rule_path'].rsplit('/',1)[-1],
                p['repository'],len(p['targets']),summary(record),record['created_at']))
        self.selected()

    def selected(self,event=None):
        selection=self.table.selection();self.current=self.visible.get(selection[0]) if selection else None
        self.runs.delete(*self.runs.get_children());text='Select a saved request to see its intended clients and run receipts.'
        if self.current:
            r=self.current['record'];p=r['plan']
            lines=['Repository: '+p['repository'],'Requested by: '+p['identity'],'Request ID: '+r['request_id'],
                'Rule: '+p['rule_path'],'Reviewed main revision: '+p['revision'],'Receipt: '+str(self.current['file']),
                '', 'Saved intended workspaces (workflow success is not a fresh Sentinel inventory):']
            for c in p['clients']:
                lines.extend([c['name']+' | '+c['workspace']+' | '+c['target']+' | rule '+c['rule_state'],
                    'Subscription: '+c['subscription_id']+' | Resource group: '+c['resource_group']])
            for i,result in enumerate(r['results']):
                state=result.get('history_error') or result['state']
                self.runs.insert('','end',iid=str(i),values=(i+1,result.get('run_id','Unknown'),state,result.get('checked_at','Not checked')))
                lines.extend(['',f'Batch {i+1}: '+json.dumps(result['inputs']),
                    'Actual run revision: '+result.get('actual_revision','Not checked')])
                if not result.get('run_id'):lines.append('No confirmed run ID. Inspect Actions; do not assume submission failed or retry automatically.')
            text='\n'.join(lines)
        self.detail.configure(state='normal');self.detail.delete('1.0','end');self.detail.insert('1.0',text);self.detail.configure(state='disabled')
        self.controls()

    def refresh_status(self):
        if self.app.busy or not self.current:return
        row=self.current;path=row['file']
        def done(result):
            record,mode,issues=result;row.update(record=record,mode=mode)
            item=next((key for key,value in self.visible.items() if value is row),None)
            self.render()
            if item in self.visible:self.table.selection_set(item);self.selected()
            self.notice.set('Status refreshed from GitHub.' if not issues else 'Some runs could not be refreshed:\n'+'\n'.join(issues[:3]))
        self.work('Reading GitHub run status; no deployments are submitted...',lambda:self.service.refresh(path),done)

    def open_run(self):
        if self.app.busy or not self.current:return
        selection=self.runs.selection();index=int(selection[0]) if selection else None
        webbrowser.open(workflow_url(self.current['record'],index))

    def open_file(self):
        if self.app.busy:return
        path=filedialog.askopenfilename(parent=self.window,title='Open workflow receipt',initialdir=str(self.folder),
            filetypes=[('Workflow receipts','*.preview.json *.deployment.json')])
        if not path:return
        try:
            record,mode=load_receipt(path)
            row=dict(file=path,record=record,mode=mode);self.rows=[row]+[r for r in self.rows if str(r['file'])!=path]
            self.search.set('');self.mode.set('All requests');self.render();self.table.selection_set('0');self.selected()
            self.notice.set('Opened a local receipt. No GitHub requests were made.')
        except Exception as error:self.notice.set('Receipt unavailable: '+str(error))
