"""Unprivileged EduLab controls for the existing administrator dashboard."""
import datetime as dt
import csv
import io
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import eduos_ui as ui


def format_results(item, attempts):
    """Instructor view: submitted code, observed output, verdict, and score."""
    lines = []
    for record in attempts.values():
        if record['assessment_id'] != item['assessment_id'] or record['status'] == 'in_progress':
            continue
        lines.append(f"Student: {record['student']}\n")
        lines.append(f"Automatic Score: {record.get('total_score', 0)}\n")
        if record.get('final_score') is not None:
            lines.append(f"Instructor Final Score: {record['final_score']}\n")
        if record.get('feedback'):
            lines.append(f"Instructor Feedback: {record['feedback']}\n")
        for question in item['questions']:
            qid = question['question_id']
            score = record.get('scores', {}).get(qid, {})
            lines.append(f"\n{qid} — {question['title']}\n")
            lines.append(f"Verdict: {score.get('status', 'Not evaluated')}\n")
            lines.append(f"Score: {score.get('score_awarded', 0)} / {question['max_score']}\n")
            lines.append(f"Code:\n{record.get('drafts', {}).get(qid, '') or '(no answer)'}\n")
            if score.get('compile_success') is False and score.get('status') != 'No answer':
                lines.append(f"Compilation error:\n{score.get('compile_error') or 'Diagnostics unavailable for this earlier result.'}\n")
            for output in score.get('output', []):
                lines.append(f"Test {output['test']}: {output['verdict']}\n"
                             f"Output:\n{output['output'] or '(no output)'}\n")
            if not score.get('output') and score.get('compile_success'):
                lines.append('Output: unavailable for this earlier result.\n')
        lines.append('\n' + '=' * 70 + '\n')
    return ''.join(lines) or 'No submitted results for this assessment.\n'


def csv_report(item, attempts):
    """Export only report fields; quote spreadsheet formulas in text cells."""
    def safe(value):
        value = str(value)
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['Assessment ID', 'Student', 'Status', 'Submitted At',
                     'Automatic Score', 'Final Score', 'Maximum Score', 'Feedback'])
    maximum = sum(q['max_score'] for q in item['questions'])
    records = sorted((record for record in attempts.values()
                      if record['assessment_id'] == item['assessment_id'] and record['status'] != 'in_progress'),
                     key=lambda record: record['student'])
    for record in records:
        automatic = record.get('total_score', 0)
        final = record.get('final_score')
        writer.writerow([item['assessment_id'], record['student'], record['status'],
                         safe(record.get('submitted_at') or ''), automatic,
                         automatic if final is None else final, maximum,
                         safe(record.get('feedback', ''))])
    return output.getvalue()


class AssessmentEditor(tk.Toplevel):
    def __init__(self, parent, item=None):
        super().__init__(parent)
        self.title('EduLab Assessment')
        self.geometry(f'{min(950, self.winfo_screenwidth() - 40)}x{min(740, self.winfo_screenheight() - 60)}')
        self.minsize(700, 540)
        self.transient(parent)
        self.result = None
        self.item = item
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        ui.install_theme(self)
        ui.protect_popup(self, parent)
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill='both', expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(4, weight=1)
        ttk.Label(outer, text='Assessment Editor', style='PageTitle.TLabel').grid(row=0, column=0, sticky='w')
        form = ttk.Frame(outer)
        form.grid(row=1, column=0, sticky='ew', pady=(4, 6))
        self.title_var = tk.StringVar(value=item['title'] if item else '')
        self.start_var = tk.StringVar(value=item['scheduled_start'] if item else
                                      dt.datetime.now().astimezone().replace(second=0, microsecond=0).isoformat())
        self.duration_var = tk.StringVar(value=str(item['duration_minutes']) if item else '10')
        for row, (label, variable) in enumerate([('Title', self.title_var), ('Start (ISO 8601 with timezone)', self.start_var),
                                                   ('Duration, minutes', self.duration_var)]):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', pady=3)
            ttk.Entry(form, textvariable=variable, width=65).grid(row=row, column=1, sticky='ew', pady=3)
        form.columnconfigure(1, weight=1)
        ttk.Label(outer, text='Instructions').grid(row=2, column=0, sticky='w')
        self.instructions = ScrolledText(outer, height=2)
        self.instructions.grid(row=3, column=0, sticky='ew')
        self.instructions.insert('1.0', item['instructions'] if item else 'Answer all questions in C++. Hidden tests determine the score.')
        question_area = ttk.Frame(outer)
        question_area.grid(row=4, column=0, sticky='nsew', pady=8)
        question_area.columnconfigure(1, weight=1)
        question_area.rowconfigure(0, weight=1)
        question_sidebar = ttk.Frame(question_area, style='Card.TFrame', padding=10)
        question_sidebar.grid(row=0, column=0, sticky='ns', padx=(0, 10))
        question_sidebar.rowconfigure(1, weight=1)
        question_sidebar.columnconfigure(0, weight=1)
        ttk.Label(question_sidebar, text='Questions', style='CardTitle.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 8))
        self.question_list = ttk.Treeview(question_sidebar, show='tree', selectmode='browse', height=2)
        self.question_list.column('#0', width=175, minwidth=140)
        self.question_list.grid(row=1, column=0, sticky='nsew')
        question_scroll = ttk.Scrollbar(question_sidebar, orient='vertical', command=self.question_list.yview)
        question_scroll.grid(row=1, column=1, sticky='ns')
        self.question_list.configure(yscrollcommand=question_scroll.set)
        question_actions = ttk.Frame(question_sidebar)
        question_actions.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        ttk.Button(question_actions, text='+ Add Question', command=self.add_question).pack(fill='x', pady=(0, 4))
        ttk.Button(question_actions, text='Delete Question', command=self.remove_question).pack(fill='x')
        self.notebook = ttk.Notebook(question_area, style='Navigation.TNotebook')
        self.notebook.grid(row=0, column=1, sticky='nsew')
        self.question_list.bind('<<TreeviewSelect>>', self.select_question)
        self.questions = []
        self.case_frames = {}
        self.populate_questions(item['questions'] if item else [])
        actions = ttk.Frame(outer)
        actions.grid(row=5, column=0, sticky='ew')
        ttk.Button(actions, text='Cancel', command=self.destroy).pack(side='right')
        ttk.Button(actions, text='Save Assessment', style='Primary.TButton', command=self.save).pack(side='right', padx=8)
        self.grab_set()
        ui.refresh_classic_widgets(self)
        parent.wait_window(self)

    def populate_questions(self, original):
        self.next_question_id = max((int(q['question_id'][1:]) for q in original), default=0) + 1
        for question in original or [{}]:
            self.add_question(question)
        first = self.questions[0]
        self.question_list.selection_set(first[0])
        self.notebook.select(first[1])

    def add_question(self, question=None):
        question = question or {}
        qid = question.get('question_id')
        if qid is None:
            qid = f'q{self.next_question_id}'
            self.next_question_id += 1
        page = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(page, text=f'Question {len(self.questions) + 1}')
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        canvas = tk.Canvas(page, highlightthickness=0)
        scrollbar = ttk.Scrollbar(page, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky='nsew')
        scrollbar.grid(row=0, column=1, sticky='ns')
        detail = ttk.Frame(canvas, padding=(4, 0, 8, 4))
        detail_window = canvas.create_window((0, 0), window=detail, anchor='nw')
        detail.bind('<Configure>', lambda _event, c=canvas: c.configure(scrollregion=c.bbox('all')))
        canvas.bind('<Configure>', lambda event, c=canvas, w=detail_window: c.itemconfigure(w, width=event.width))
        ttk.Label(detail, text='Selected Question', style='Heading.TLabel').pack(anchor='w', pady=(0, 6))
        title = tk.StringVar(value=question.get('title', ''))
        score = tk.StringVar(value=str(question.get('max_score', 10)))
        ttk.Label(detail, text='Question Title').pack(anchor='w')
        ttk.Entry(detail, textvariable=title).pack(fill='x')
        title.trace_add('write', lambda *_args, q=qid, v=title: self.update_question_label(q, v.get()))
        ttk.Label(detail, text='Maximum Marks').pack(anchor='w', pady=(6, 0))
        ttk.Entry(detail, textvariable=score, width=10).pack(anchor='w')
        ttk.Label(detail, text='Problem Statement').pack(anchor='w', pady=(6, 0))
        statement = ScrolledText(detail, height=5)
        statement.pack(fill='x')
        statement.insert('1.0', question.get('problem_statement', ''))
        ttk.Label(detail, text='Public Example Input — shown to students').pack(anchor='w', pady=(8, 0))
        example_input = ScrolledText(detail, height=3)
        example_input.pack(fill='x')
        example_input.insert('1.0', question.get('example_input', ''))
        ttk.Label(detail, text='Public Example Expected Output — shown to students').pack(anchor='w', pady=(8, 0))
        example_output = ScrolledText(detail, height=3)
        example_output.pack(fill='x')
        example_output.insert('1.0', question.get('example_output', ''))
        ttk.Label(detail, text='Hidden Tests — admin only', style='Heading.TLabel').pack(anchor='w', pady=(12, 2))
        case_area = ttk.Frame(detail)
        case_area.pack(fill='x')
        cases = []
        self.questions.append((qid, page, title, score, statement, example_input, cases, example_output))
        self.case_frames[qid] = case_area
        prior = question.get('hidden_test_cases') or []
        for case_index in range(max(2, len(prior))):
            test = prior[case_index] if case_index < len(prior) else {}
            self.add_hidden_test(qid, test)
        case_actions = ttk.Frame(detail)
        case_actions.pack(fill='x', pady=(8, 0))
        ttk.Button(case_actions, text='+ Add Hidden Test', command=lambda q=qid: self.add_hidden_test(q)).pack(side='left')
        ttk.Button(case_actions, text='Remove Hidden Test', command=lambda q=qid: self.remove_hidden_test(q)).pack(side='left', padx=8)
        self.question_list.insert('', 'end', iid=qid, text=f'{len(self.questions)}. {title.get() or "Untitled question"}')
        self.question_list.selection_set(qid)
        self.notebook.select(page)

    def add_hidden_test(self, qid, test=None):
        cases = next(entry[6] for entry in self.questions if entry[0] == qid)
        if len(cases) >= 20:
            messagebox.showerror('EduLab', 'A question supports at most 20 hidden tests.', parent=self)
            return
        test = test or {}
        frame = ttk.Frame(self.case_frames[qid])
        frame.pack(fill='x', pady=(6, 0))
        ttk.Label(frame, text=f'Test {len(cases) + 1}').pack(anchor='w')
        pair = ttk.Frame(frame)
        pair.pack(fill='x')
        widgets = []
        for label, key in [('Input', 'input'), ('Expected Output', 'expected_output')]:
            box = ttk.Frame(pair)
            box.pack(fill='x', padx=4, pady=(0, 6))
            ttk.Label(box, text=label).pack(anchor='w')
            widget = ScrolledText(box, height=3, width=1)
            ui.code_widget(widget)
            widget.pack(fill='both', expand=True)
            widget.insert('1.0', test.get(key, ''))
            widgets.append(widget)
        cases.append((frame, *widgets))

    def remove_hidden_test(self, qid):
        cases = next(entry[6] for entry in self.questions if entry[0] == qid)
        if len(cases) <= 2:
            messagebox.showerror('EduLab', 'Keep at least two hidden tests per question.', parent=self)
            return
        cases.pop()[0].destroy()

    def update_question_label(self, qid, title):
        if self.question_list.exists(qid):
            position = next(i for i, entry in enumerate(self.questions, 1) if entry[0] == qid)
            self.question_list.item(qid, text=f'{position}. {title or "Untitled question"}')

    def select_question(self, _event=None):
        selection = self.question_list.selection()
        if not selection:
            return
        for qid, page, *_rest in self.questions:
            if qid == selection[0]:
                self.notebook.select(page)
                break

    def remove_question(self):
        if len(self.questions) == 1:
            messagebox.showerror('EduLab', 'Keep at least one question.', parent=self)
            return
        selection = self.question_list.selection()
        if not selection:
            return
        index = next(i for i, entry in enumerate(self.questions) if entry[0] == selection[0])
        self.notebook.forget(self.questions[index][1])
        self.question_list.delete(self.questions[index][0])
        self.case_frames.pop(self.questions[index][0])
        self.questions.pop(index)
        for position, entry in enumerate(self.questions, 1):
            self.notebook.tab(entry[1], text=f'Question {position}')
            self.question_list.item(entry[0], text=f'{position}. {entry[2].get() or "Untitled question"}')
        target = self.questions[min(index, len(self.questions) - 1)]
        self.question_list.selection_set(target[0])
        self.notebook.select(target[1])

    def save(self):
        try:
            if not self.title_var.get().strip():
                raise ValueError('Enter an assessment title.')
            if not self.instructions.get('1.0', 'end-1c').strip():
                raise ValueError('Enter assessment instructions.')
            try:
                start = dt.datetime.fromisoformat(self.start_var.get())
            except ValueError as error:
                raise ValueError('Enter a valid ISO 8601 start timestamp with timezone.') from error
            if start.tzinfo is None or start.utcoffset() is None:
                raise ValueError('Start timestamp needs a timezone.')
            try:
                duration = int(self.duration_var.get())
            except ValueError as error:
                raise ValueError('Duration must be a whole number of minutes.') from error
            if not 1 <= duration <= 240:
                raise ValueError('Duration must be 1–240 minutes.')
            if not self.questions:
                raise ValueError('Add at least one question.')
            questions = []
            for qid, _page, title, score, statement, example_input, cases, example_output in self.questions:
                if not title.get().strip():
                    raise ValueError(f'{qid}: enter a question title.')
                try:
                    marks = int(score.get())
                except ValueError as error:
                    raise ValueError(f'{qid}: maximum marks must be a whole number.') from error
                if not 1 <= marks <= 1000:
                    raise ValueError(f'{qid}: maximum marks must be 1–1000.')
                if not statement.get('1.0', 'end-1c').strip():
                    raise ValueError(f'{qid}: enter a problem statement.')
                if len(cases) < 2:
                    raise ValueError(f'{qid}: add at least two hidden tests.')
                questions.append({'question_id': qid, 'title': title.get().strip(),
                                  'problem_statement': statement.get('1.0', 'end-1c'), 'language': 'cpp',
                                  'max_score': marks,
                                  'example_input': example_input.get('1.0', 'end-1c'),
                                  'example_output': example_output.get('1.0', 'end-1c'),
                                  'hidden_test_cases': [{'input': pair[1].get('1.0', 'end-1c'),
                                                         'expected_output': pair[2].get('1.0', 'end-1c')}
                                                        for pair in cases]})
            self.result = {'title': self.title_var.get(), 'instructions': self.instructions.get('1.0', 'end-1c'),
                           'scheduled_start': self.start_var.get(), 'duration_minutes': duration,
                           'state': 'draft', 'questions': questions}
        except ValueError as error:
            messagebox.showerror('EduLab', str(error), parent=self)
            return
        self.destroy()


class EduLabPanel:
    def __init__(self, dashboard, parent):
        self.dashboard = dashboard
        self.items = {}
        self.attempts = {}
        outer = ttk.Frame(parent, padding=14)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='EduLab Assessments', style='PageTitle.TLabel').pack(anchor='w')
        ttk.Label(outer, text='Create timed assessments and review submitted results.',
                  style='Subtitle.TLabel').pack(anchor='w', pady=(2, 10))
        self.list = ttk.Treeview(outer, columns=('state', 'start', 'duration'), show='tree headings')
        ui.configure_tree_tags(self.list)
        for column, label in [('state', 'State'), ('start', 'Scheduled Start'), ('duration', 'Minutes')]:
            self.list.heading(column, text=label)
        self.list.pack(fill='both', expand=True, pady=8)
        controls = ttk.Frame(outer)
        controls.pack(fill='x')
        for label, callback in [('Create Assessment', self.create), ('Edit Draft', self.edit),
                                ('Delete Assessment', self.delete), ('Activate', self.activate),
                                ('Deactivate', self.deactivate), ('Refresh', self.refresh)]:
            style = 'Primary.TButton' if label == 'Create Assessment' else (
                'Danger.TButton' if label == 'Delete Assessment' else 'TButton')
            dashboard.button(controls, label, callback, style=style).pack(side='left', padx=2)
        review_controls = ttk.Frame(outer)
        review_controls.pack(fill='x', pady=(5, 0))
        for label, callback in [('Results', self.results), ('Review Attempt', self.review),
                                ('Export Results CSV', self.export_csv), ('Reset Attempt', self.reset)]:
            dashboard.button(review_controls, label, callback).pack(side='left', padx=2)

    def selected(self):
        selection = self.list.selection()
        return self.items.get(selection[0]) if selection else None

    def refresh(self):
        self.dashboard.request({'action': 'edulab_list'}, self.loaded)

    def loaded(self, response):
        self.items = {item['assessment_id']: item for item in response['assessments']}
        self.attempts = response['attempts']
        self.list.delete(*self.list.get_children())
        for index, (identifier, item) in enumerate(sorted(self.items.items())):
            state_tag = 'success' if item['state'] == 'active' else ('warning' if item['state'] == 'scheduled' else 'even')
            self.list.insert('', 'end', iid=identifier, text=f"{identifier} — {item['title']}",
                             values=(item['state'], item['scheduled_start'], item['duration_minutes']),
                             tags=(state_tag, 'even' if index % 2 == 0 else 'odd'))

    def create(self):
        editor = AssessmentEditor(self.dashboard.root)
        if editor.result:
            self.dashboard.request({'action': 'edulab_create', 'assessment': editor.result}, lambda _: self.refresh())

    def edit(self):
        item = self.selected()
        if item and item['state'] == 'draft':
            editor = AssessmentEditor(self.dashboard.root, item)
            if editor.result:
                self.dashboard.request({'action': 'edulab_edit', 'assessment_id': item['assessment_id'],
                                        'assessment': editor.result}, lambda _: self.refresh())

    def change(self, action):
        item = self.selected()
        if item and messagebox.askyesno('EduLab', f'{action.title()} {item["title"]}?', parent=self.dashboard.root):
            self.dashboard.request({'action': 'edulab_' + action, 'assessment_id': item['assessment_id']},
                                   lambda _: self.refresh())

    def delete(self):
        item = self.selected()
        if item and messagebox.askyesno('Delete Assessment',
                f"Permanently delete {item['title']} and its completed results and code? "
                'An assessment with an active attempt cannot be deleted.', parent=self.dashboard.root):
            self.dashboard.request({'action': 'edulab_delete', 'assessment_id': item['assessment_id']},
                                   lambda _: self.refresh())

    def activate(self):
        self.change('activate')

    def deactivate(self):
        self.change('deactivate')

    def results(self):
        item = self.selected()
        if not item:
            return
        window = tk.Toplevel(self.dashboard.root)
        ui.install_theme(window)
        ui.protect_popup(window, self.dashboard.root)
        window.title('EduLab Results — ' + item['title'])
        window.geometry('950x650')
        text = ScrolledText(window, wrap='word', font=('DejaVu Sans Mono', 10))
        ui.code_widget(text)
        text.pack(fill='both', expand=True)
        text.insert('end', format_results(item, self.attempts))
        text.configure(state='disabled')

    def review(self):
        from tkinter import simpledialog
        item = self.selected()
        if not item:
            return
        student = simpledialog.askstring('Review Attempt', 'Student username (studentXXXX):',
                                         parent=self.dashboard.root)
        if student is None:
            return
        record = self.attempts.get(item['assessment_id'] + ':' + student)
        if record is None or record['status'] == 'in_progress':
            messagebox.showerror('Review Attempt', 'Select a student with a completed attempt.',
                                 parent=self.dashboard.root)
            return
        current = record.get('final_score')
        score = simpledialog.askstring('Review Attempt',
            'Final score (blank keeps the automatic score):',
            initialvalue='' if current is None else str(current), parent=self.dashboard.root)
        if score is None:
            return
        try:
            final = None if not score.strip() else float(score)
        except ValueError:
            messagebox.showerror('Review Attempt', 'Enter a numeric score.', parent=self.dashboard.root)
            return
        feedback = simpledialog.askstring('Review Attempt', 'Feedback for the student:',
            initialvalue=record.get('feedback', ''), parent=self.dashboard.root)
        if feedback is None:
            return
        self.dashboard.request({'action': 'edulab_review', 'assessment_id': item['assessment_id'],
                                'student': student, 'feedback': feedback, 'final_score': final},
                               lambda _: self.refresh())

    def export_csv(self):
        from tkinter import filedialog
        item = self.selected()
        if not item:
            return
        path = filedialog.asksaveasfilename(parent=self.dashboard.root, title='Export EduLab Results',
            initialfile=item['assessment_id'] + '-results.csv', defaultextension='.csv',
            filetypes=[('CSV files', '*.csv')])
        if not path:
            return
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                stream.write(csv_report(item, self.attempts))
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            messagebox.showerror('Export Results', str(error), parent=self.dashboard.root)
            return
        messagebox.showinfo('Export Results', 'CSV export saved.', parent=self.dashboard.root)

    def reset(self):
        from tkinter import simpledialog
        item = self.selected()
        if not item:
            return
        name = simpledialog.askstring('Reset Attempt', 'Student username (studentXXXX):', parent=self.dashboard.root)
        if name and messagebox.askyesno('Reset Attempt', f'Reset {name} for {item["title"]}?', parent=self.dashboard.root):
            self.dashboard.request({'action': 'edulab_reset', 'assessment_id': item['assessment_id'], 'student': name},
                                   lambda _: self.refresh())
