"""Student EduLab workspace. Clipboard/shortcut controls are best-effort X11 UI controls."""
import datetime as dt
import json
import logging
import os
from pathlib import Path
import pwd
import socket
import subprocess
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import eduos_ui as ui

SOCKET = '/run/eduos-edulab.sock'
SNAPSHOT = Path.home() / '.local/share/eduos/edulab-shortcuts.json'
CHANNEL = 'xfce4-keyboard-shortcuts'
LOG = logging.getLogger(__name__)
STARTER_CPP = '''#include <bits/stdc++.h>
using namespace std;

int main() {
    // Write your code here

    return 0;
}
'''


class EduLabUnavailable(ValueError):
    """The local service cannot be reached; safe to display to students."""


def request(action, **fields):
    try:
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(190 if action in ('submit', 'state', 'run_example') else 5)
            connection.connect(SOCKET)
            connection.sendall((json.dumps({'action': action, **fields}) + '\n').encode())
            with connection.makefile('rb') as stream:
                raw = stream.readline(2_000_001)
    except OSError as error:
        LOG.warning('EduLab socket %s unavailable: %s', SOCKET, error)
        raise EduLabUnavailable('EduLab is currently unavailable. Try Refresh Assessments later.') from error
    if not raw:
        LOG.warning('EduLab socket %s closed without a response', SOCKET)
        raise EduLabUnavailable('EduLab is currently unavailable. Try Refresh Assessments later.')
    if len(raw) > 2_000_000:
        raise ValueError('EduLab response is too large.')
    response = json.loads(raw)
    if not response.get('ok'):
        raise ValueError(response.get('error', 'EduLab request failed.'))
    return response


def xfconf(*args):
    return subprocess.run(['/usr/bin/xfconf-query', '--channel', CHANNEL, *args],
                          capture_output=True, text=True, timeout=5, check=False)


def restore_lockdown():
    if not SNAPSHOT.exists():
        return
    try:
        saved = json.loads(SNAPSHOT.read_text())
        if not isinstance(saved, dict) or any(not key.startswith(('/xfwm4/', '/commands/')) for key in saved):
            raise ValueError('Invalid shortcut snapshot.')
        for property_name, value in saved.items():
            xfconf('--property', property_name, '--set', value)
        SNAPSHOT.unlink()
    except (OSError, ValueError):
        # Retain the snapshot for the next app startup or manual recovery.
        pass


def apply_lockdown():
    restore_lockdown()
    listing = xfconf('--list')
    if listing.returncode:
        return
    candidates = [name for name in listing.stdout.splitlines()
                  if name.startswith(('/xfwm4/', '/commands/')) and any(term in name.lower()
                  for term in ('tab', 'workspace', 'super', 'alt', 'launcher'))]
    saved = {}
    for property_name in candidates:
        value = xfconf('--property', property_name)
        if value.returncode == 0:
            saved[property_name] = value.stdout.rstrip('\n')
    if not saved:
        return
    SNAPSHOT.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.shortcuts-', dir=SNAPSHOT.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(saved, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, SNAPSHOT)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    for property_name in saved:
        xfconf('--property', property_name, '--set', '')


def result_text(assessment, attempt):
    lines = [f"{assessment['title']}\n",
             f"Total Score: {attempt.get('total_score', 0)} / "
             f"{sum(q['max_score'] for q in assessment['questions'])}\n"]
    if attempt.get('final_score') is not None:
        lines.append(f"Instructor Final Score: {attempt['final_score']}\n")
    if attempt.get('feedback'):
        lines.append(f"Instructor Feedback: {attempt['feedback']}\n")
    for question in assessment['questions']:
        qid = question['question_id']
        score = attempt.get('scores', {}).get(qid, {})
        lines.append(f"\n{question['title']}\nVerdict: {score.get('status', 'Not evaluated')}\n"
                     f"Score: {score.get('score_awarded', 0)} / {question['max_score']}\n"
                     f"Your Code:\n{attempt.get('drafts', {}).get(qid, '') or '(no answer)'}\n")
        if score.get('compile_error'):
            lines.append(f"Compilation Error:\n{score['compile_error']}\n")
        for output in score.get('output', []):
            lines.append(f"Test {output['test']}: {output['verdict']}\n"
                         f"Your Output:\n{output['output'] or '(no output)'}\n")
    return ''.join(lines)


def example_text(question, result=None):
    lines = ['Example Input:\n' + (question.get('example_input', '') or '(empty)'),
             'Expected Output:\n' + (question.get('example_output', '') or '(not configured)')]
    if result is not None:
        lines.append('Status: ' + result['status'])
        if result['compile_error']:
            lines.append('Compilation Error:\n' + result['compile_error'])
        else:
            lines.append('Your Output:\n' + (result['output'] or '(no output)'))
            if result['runtime_error']:
                lines.append('Runtime Diagnostics:\n' + result['runtime_error'])
    return '\n\n'.join(lines)


class EduLabWorkspace(tk.Toplevel):
    def __init__(self, parent, response, refreshed, show_result):
        super().__init__(parent)
        ui.install_theme(self)
        ui.protect_popup(self, parent)
        self.parent = parent
        self.refreshed = refreshed
        self.show_result = show_result
        self.assessment = response['assessment']
        self.attempt = response['attempt']
        self.example_outputs = {}
        self.index = 0
        self.closed = False
        self.fullscreen_exit_recorded = False
        self.parent_escape = parent.bind('<Escape>')
        self.parent_f11 = parent.bind('<F11>')
        parent.bind('<Escape>', lambda _: 'break')
        parent.bind('<F11>', lambda _: 'break')
        self.title('EduLab — ' + self.assessment['title'])
        ui.set_fullscreen(self, True)
        self.protocol('WM_DELETE_WINDOW', self.close_attempt)
        self.bind('<Escape>', self.escape_attempt)
        self.bind('<F11>', lambda _: 'break')
        self.bind('<Alt-Tab>', self.block)
        self.bind('<Alt-Shift-Tab>', self.block)
        for sequence in ('<Control-c>', '<Control-Insert>', '<Control-v>', '<Shift-Insert>',
                         '<Control-x>', '<Button-2>'):
            self.bind_all(sequence, self.block, add='+')
        self.grab_set()
        apply_lockdown()
        outer = ttk.Frame(self, padding=24)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='EDULAB', style='Subtitle.TLabel').pack(anchor='w')
        ttk.Label(outer, text=self.assessment['title'], style='PageTitle.TLabel').pack(anchor='w')
        self.timer = tk.StringVar()
        ttk.Label(outer, textvariable=self.timer, style='Warning.TLabel').pack(anchor='e')
        self.heading = tk.StringVar()
        ttk.Label(outer, textvariable=self.heading, style='Heading.TLabel').pack(anchor='w', pady=(12, 5))
        controls = ttk.Frame(outer)
        controls.pack(fill='x', pady=(0, 8))
        ttk.Button(controls, text='Previous', command=lambda: self.navigate(-1)).pack(side='left')
        ttk.Button(controls, text='Save Draft', command=self.save).pack(side='left', padx=8)
        ttk.Button(controls, text='Run Code', command=self.run_example).pack(side='left')
        ttk.Button(controls, text='Next', command=lambda: self.navigate(1)).pack(side='left')
        ttk.Button(controls, text='Submit Assessment', style='Primary.TButton', command=self.submit).pack(side='right')
        self.violations = tk.StringVar()
        ttk.Label(outer, textvariable=self.violations).pack(anchor='w')
        panes = ttk.Panedwindow(outer, orient='vertical')
        panes.pack(fill='both', expand=True)
        statement_page, editor_page, result_page = (ttk.Frame(panes) for _ in range(3))
        for page, weight in ((statement_page, 1), (editor_page, 3), (result_page, 2)):
            panes.add(page, weight=weight)
        ttk.Label(statement_page, text='Problem Statement').pack(anchor='w')
        self.statement = ScrolledText(statement_page, height=3, wrap='word')
        ui.code_widget(self.statement)
        self.statement.pack(fill='both', expand=True)
        ttk.Label(editor_page, text='C++ Code Editor').pack(anchor='w')
        self.code = ScrolledText(editor_page, height=9, undo=True, wrap='none')
        ui.code_widget(self.code)
        self.code.pack(fill='both', expand=True)
        # Widget bindings run before Text class bindings. A bind_all handler
        # alone would record paste only after Tk had already inserted it.
        for sequence in ('<Control-c>', '<Control-Insert>', '<Control-v>', '<Shift-Insert>',
                         '<Control-x>', '<Button-2>'):
            self.code.bind(sequence, self.block)
        ttk.Label(result_page, text='Example Run Results').pack(anchor='w')
        self.output = ScrolledText(result_page, height=7, wrap='word')
        ui.code_widget(self.output)
        self.output.pack(fill='both', expand=True)
        self.show_question()
        ui.refresh_classic_widgets(self)
        self.tick()
        self.after(5000, self.autosave)

    def block(self, _event=None):
        return 'break'

    def check_fullscreen(self):
        """Only an actual loss of fullscreen counts as a violation."""
        if self.closed or self.attempt['status'] != 'in_progress':
            return
        if self.attributes('-fullscreen'):
            self.fullscreen_exit_recorded = False
            return
        # Restore the kiosk before doing socket I/O. A slow or unavailable
        # logger must never leave the desktop exposed.
        self.attributes('-fullscreen', True)
        self.attributes('-topmost', True)
        self.lift()
        self.focus_force()
        if self.fullscreen_exit_recorded:
            return
        self.fullscreen_exit_recorded = True
        if self.attempt['status'] == 'in_progress':
            try:
                self.attempt = request('violation', assessment_id=self.assessment['assessment_id'],
                                       type='FULLSCREEN_EXIT')['attempt']
                self.violations.set(f"Violations: {len(self.attempt['violations'])}")
            except (OSError, ValueError):
                # Keep the desktop covered and retry the audit event on the
                # next guard tick if the local service was briefly unavailable.
                self.fullscreen_exit_recorded = False

    def show_question(self):
        question = self.assessment['questions'][self.index]
        self.heading.set(f"Question {self.index + 1} of {len(self.assessment['questions'])} — "
                         f"{question['max_score']} Marks — {question['title']}")
        self.display_text(self.statement, question['problem_statement'])
        self.display_text(self.output, self.example_outputs.get(question['question_id'],
                          example_text(question)))
        self.code.delete('1.0', 'end')
        self.code.insert('1.0', self.attempt['drafts'].get(question['question_id'], STARTER_CPP))
        self.violations.set(f"Violations: {len(self.attempt['violations'])}")

    @staticmethod
    def display_text(widget, content):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', content)
        widget.configure(state='disabled')
        widget.see('1.0')

    def save(self):
        if self.attempt['status'] != 'in_progress':
            return
        question = self.assessment['questions'][self.index]
        response = request('save', assessment_id=self.assessment['assessment_id'],
                           question_id=question['question_id'], source=self.code.get('1.0', 'end-1c'))
        self.attempt = response['attempt']

    def navigate(self, step):
        try:
            self.save()
            self.index = max(0, min(len(self.assessment['questions']) - 1, self.index + step))
            self.show_question()
        except (OSError, ValueError) as error:
            messagebox.showerror('EduLab', str(error), parent=self)

    def run_example(self):
        question = self.assessment['questions'][self.index]
        try:
            self.save()
            self.display_text(self.output, 'Compiling and running your code on the public example...')
            self.update_idletasks()
            result = request('run_example', assessment_id=self.assessment['assessment_id'],
                             question_id=question['question_id'], source=self.code.get('1.0', 'end-1c'))['example']
        except (OSError, ValueError) as error:
            self.display_text(self.output, 'Example run unavailable: ' + str(error))
            return
        self.example_outputs[question['question_id']] = example_text(question, result)
        self.display_text(self.output, self.example_outputs[question['question_id']])

    def autosave(self):
        if self.closed or self.attempt['status'] != 'in_progress':
            return
        try:
            self.save()
        except (OSError, ValueError):
            pass
        self.after(5000, self.autosave)

    def tick(self):
        if self.closed:
            return
        self.check_fullscreen()
        deadline = dt.datetime.fromisoformat(self.attempt['deadline'])
        seconds = max(0, int((deadline - dt.datetime.now(dt.timezone.utc)).total_seconds()))
        self.timer.set(f'Time Remaining: {seconds // 60:02d}:{seconds % 60:02d}')
        if seconds == 0 and self.attempt['status'] == 'in_progress':
            self.finish('timeout')
            return
        self.after(1000, self.tick)

    def submit(self):
        if messagebox.askyesno('Submit Assessment', 'Submit assessment? You cannot edit afterward.', parent=self):
            self.finish('manual')

    def finish(self, reason):
        try:
            if reason == 'manual':
                self.save()
            response = request('submit' if reason == 'manual' else 'state',
                               assessment_id=self.assessment['assessment_id'])
            self.attempt = response['attempt']
        except (OSError, ValueError) as error:
            messagebox.showerror('EduLab', str(error), parent=self)
            return
        self.closed = True
        restore_lockdown()
        for sequence in ('<Control-c>', '<Control-Insert>', '<Control-v>', '<Shift-Insert>', '<Control-x>', '<Button-2>'):
            self.unbind_all(sequence)
        self.parent.bind('<Escape>', self.parent_escape)
        self.parent.bind('<F11>', self.parent_f11)
        self.grab_release()
        self.destroy()
        self.refreshed()
        messagebox.showinfo('EduLab', 'TIME EXPIRED — Assessment submitted automatically.' if reason == 'timeout'
                            else 'Assessment submitted.', parent=self.parent)
        self.show_result(response)

    def close_attempt(self, _event=None):
        return self.escape_attempt(_event)

    def escape_attempt(self, _event=None):
        """Escape never opens a dialog or yields the desktop during an attempt."""
        self.attributes('-fullscreen', True)
        self.attributes('-topmost', True)
        self.lift()
        self.focus_force()
        self.after_idle(lambda: self.attributes('-fullscreen', True))
        return 'break'


class EduLabPanel:
    def __init__(self, parent, container):
        self.parent = parent
        self.items = {}
        self.attempts = {}
        self.status = tk.StringVar(value='Open this tab to load assessments.')
        ttk.Label(container, text='EduLab Assessments', style='PageTitle.TLabel').pack(anchor='w', pady=(0, 5))
        ttk.Label(container, text='Timed C++ assessments with hidden tests', style='Subtitle.TLabel').pack(anchor='w')
        self.table = ttk.Treeview(container, columns=('status', 'starts', 'duration', 'marks'),
                                  show='tree headings', height=8)
        self.table.heading('#0', text='Assessment')
        ui.configure_tree_tags(self.table)
        self.table.column('#0', width=340, stretch=True)
        for key, label, width in [('status', 'Status', 125), ('starts', 'Starts', 230),
                                  ('duration', 'Minutes', 80), ('marks', 'Total Marks', 105)]:
            self.table.heading(key, text=label)
            self.table.column(key, width=width, stretch=key == 'starts')
        self.table.pack(fill='both', expand=True, pady=(12, 8))
        ttk.Label(container, textvariable=self.status, style='Subtitle.TLabel').pack(anchor='w')
        controls = ttk.Frame(container)
        controls.pack(fill='x', pady=(12, 0))
        ttk.Button(controls, text='Refresh Assessments', command=self.refresh).pack(side='left')
        ttk.Button(controls, text='Start / Resume Assessment', style='Primary.TButton',
                   command=self.start).pack(side='left', padx=10)
        ttk.Button(controls, text='View My Result', command=self.view_result).pack(side='left')

    def assessment_status(self, item):
        attempt = self.attempts.get(item['assessment_id'] + ':' + self.student_name)
        if attempt:
            return {'in_progress': 'In Progress', 'submitted': 'Submitted',
                    'timed_out': 'Timed Out'}.get(attempt['status'], 'Unavailable')
        start = dt.datetime.fromisoformat(item['scheduled_start'])
        current = dt.datetime.now(dt.timezone.utc)
        if current < start:
            return 'Scheduled'
        if current >= start + dt.timedelta(minutes=item['duration_minutes']):
            return 'Closed'
        return 'Available'

    def refresh(self):
        try:
            response = request('list')
        except (OSError, ValueError) as error:
            LOG.warning('Unable to refresh EduLab assessments: %s', error)
            self.items = {}
            self.attempts = {}
            self.table.delete(*self.table.get_children())
            self.status.set('EduLab is currently unavailable. Lab Assignments remain available.')
            return
        self.items = {item['assessment_id']: item for item in response['assessments']}
        self.attempts = response.get('attempts', {})
        self.student_name = pwd.getpwuid(os.getuid()).pw_name
        self.table.delete(*self.table.get_children())
        for index, item in enumerate(self.items.values()):
            status = self.assessment_status(item)
            status_tag = 'success' if status in ('Available', 'Submitted') else (
                'warning' if status in ('Scheduled', 'In Progress') else 'even')
            self.table.insert('', 'end', iid=item['assessment_id'], text=item['title'],
                              values=(status, item['scheduled_start'], item['duration_minutes'],
                                      sum(q['max_score'] for q in item['questions'])),
                              tags=(status_tag, 'even' if index % 2 == 0 else 'odd'))
        self.status.set(f'{len(self.items)} assessment(s) listed.' if self.items else
                        'No EduLab assessments currently available.')

    def start(self):
        selection = self.table.selection()
        if not selection:
            return
        item = self.items[selection[0]]
        rules = (f"{item['title']}\nDuration: {item['duration_minutes']} minutes\n"
                 f"Questions: {len(item['questions'])}\n"
                 f"Total Marks: {sum(q['max_score'] for q in item['questions'])}\n"
                 'Test cases are hidden. Copy and paste are disabled. Leaving fullscreen is recorded. '
                 'The assessment submits at its deadline. Start or resume?')
        if not messagebox.askyesno('EduLab Rules', rules, parent=self.parent):
            return
        try:
            response = request('start', assessment_id=item['assessment_id'])
            if response['attempt']['status'] != 'in_progress':
                messagebox.showinfo('EduLab', 'Attempt already submitted or timed out.', parent=self.parent)
                return
            EduLabWorkspace(self.parent, response, self.refresh, self.show_result_response)
        except (OSError, ValueError) as error:
            messagebox.showerror('EduLab', str(error), parent=self.parent)

    def view_result(self):
        selection = self.table.selection()
        if not selection:
            return
        item = self.items[selection[0]]
        try:
            response = request('result', assessment_id=item['assessment_id'])
        except (OSError, ValueError) as error:
            messagebox.showinfo('EduLab Result', str(error), parent=self.parent)
            return
        self.show_result_response(response)

    def show_result_response(self, response):
        item = response['assessment']
        window = tk.Toplevel(self.parent)
        ui.install_theme(window)
        ui.protect_popup(window, self.parent)
        window.title('My EduLab Result — ' + item['title'])
        window.geometry('900x650')
        ui.install_theme(window)
        outer = ttk.Frame(window, padding=20)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='My Assessment Result', style='PageTitle.TLabel').pack(anchor='w', pady=(0, 10))
        text = ScrolledText(outer, wrap='word')
        ui.code_widget(text)
        text.pack(fill='both', expand=True)
        text.insert('1.0', result_text(response['assessment'], response['attempt']))
        text.configure(state='disabled')
