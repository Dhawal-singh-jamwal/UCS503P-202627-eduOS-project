# EduLab local assessment prototype

EduLab adds timed C++ assessments to the existing EduOS student and admin
applications. Normal Lab Assignments, Run Tests, and submissions are separate.

## Data and permissions

The persistent state disk is mounted at `/var/lib/eduos` with `nodev,nosuid,noexec`.
The root service creates `/var/lib/eduos/edulab` as root-owned mode `0700`.
`assessments.json` and `attempts.json` are mode `0600`, written by temporary
file, `fsync`, and `os.replace` while holding `/run/eduos-edulab.lock`.
Students cannot read or change these files. The student socket returns only
public question fields; it never returns hidden input or expected output.

Assessment objects contain `assessment_id` (`EXAM-0001`), `title`, `instructions`,
`scheduled_start` (ISO 8601 with timezone), `duration_minutes` (1–240), `state`
(`draft`, `active`, `completed`), and `questions`. Each question has `question_id`
(`q1`), `title`, `problem_statement`, `language: "cpp"`, integer `max_score`,
optional `example_input` and `example_output` (public to the student), and 2–20
`hidden_test_cases` containing `input` and `expected_output`.
The editor starts with one question and can add/remove any practical number
of questions; the protected store's 4 MiB atomic-write limit is the overall
resource bound. It presents a public example input followed by its expected output, and two hidden
cases per question. Each hidden case stacks Input above Expected Output so both
fields remain accessible in narrow windows. Older input-only examples remain valid.
`active` assessments become available at their scheduled start and close
after the duration; a separate scheduled state is unnecessary in this version.

Attempts are keyed by `assessment_id:studentXXXX` and contain `started_at`,
immutable `deadline`, `status`, question `drafts`, timestamped `violations`,
`submitted_at`, `reason`, per-question `scores`, and `total_score`. The
deadline is the earlier of attempt start plus duration and the assessment's
global end. Reopening uses the same attempt record and deadline. At expiry,
the service evaluates saved drafts and marks the attempt `timed_out`.
Manual submission marks it `submitted`; both states reject later edits.

Question result fields are `score_awarded`, `max_score`, `tests_passed`,
`tests_total`, `compile_success`, and `status`. The result also retains compiler
diagnostics and each test's actual output and verdict for the student's own
post-submission view. Passed tests receive
`max_score * passed / total`, rounded to two decimal places. The total is the
sum of question scores. Empty or noncompiling answers score zero. After an
attempt closes, the student can view only their own code, actual output,
verdict, score, and compiler diagnostics. Hidden inputs and expected outputs
are never returned by the student service.

An instructor may review a completed attempt, add up to 4096 characters of
feedback, and optionally set a final score between zero and the assessment's
maximum marks. The automatic question scores remain intact. The student result
view shows the review after refresh, and the admin can export a CSV containing
student, status, automatic score, final score, and feedback. CSV text fields are
escaped to avoid spreadsheet formula execution. Resetting an attempt removes its
review with the attempt.

## Privilege boundary

The admin app uses the existing fixed `eduos-admin-helper` sudo rule. EduLab
admin operations have fixed names and strict request shapes. The student app
connects to `/run/eduos-edulab.sock`; the root service derives identity from
Unix `SO_PEERCRED`, requires a `studentXXXX` account, and rejects path-bearing
identifiers and arbitrary request fields. GUI-supplied student names are never
trusted for student operations. The service reads hidden tests and invokes
the existing `eduos-evaluator` with `setpriv` using the fixed
`edulab-runner` UID and GID. This directly drops identity without a login
shell or PAM session. The evaluator
and submitted C++ run under that non-login, non-root UID, never as root.
Source code cannot modify protected attempts or results. No sudoers change
is needed.

The socket is created by `eduos-edulab.socket` during the normal
`sockets.target` phase. Its unit has no dependency on the later state-disk
service; the activated `eduos-edulab.service` waits for that disk. This avoids
an early-boot ordering cycle. The student app shows a friendly unavailable
message if the socket is absent, while Lab Assignments remain usable. The
root-owned EduLab storage directory is created on first service access.

## Assessment controls and recovery

The EduLab workspace uses fullscreen/topmost Tk and blocks ordinary copy, cut,
paste, Escape, F11, and close actions. Escape and window-close attempts do not
open modal dialogs; they immediately reassert fullscreen, topmost, and focus.
Only an actual loss of the fullscreen
window state is logged as `FULLSCREEN_EXIT`; focus changes, typing, and button
clicks are not violations. It saves drafts every five seconds and on navigation. Relevant XFCE
shortcut values are saved in the student's private
`~/.local/share/eduos/edulab-shortcuts.json`, changed for the attempt, and
restored on submission, timeout, or next app startup after a crash. Violations
are review events only and do not automatically change the score.

This is a best-effort kiosk layer on X11, not a secure exam browser. It does not
prevent TTY switching, root or kernel access, other devices/cameras, or
sophisticated X11 manipulation. C++ runs as a separate non-root account inside
the evaluator's Bubblewrap namespace with resource limits. It has not been
security audited for adversarial exams. Instructors should review
violation records. Students can compile and run their current answer against
the instructor's single public example input; this never uses a hidden test or
returns a hidden expected answer. The student workspace opens each unanswered
question with the C++ starter template used in Lab Assignments. Its separate,
scrollable results pane shows the public example input and expected output,
then the student's actual output or compiler diagnostics after **Run Code**.
Submission opens the student's own code, actual output, verdict, and score;
the result remains available through **View My Result**. A failed evaluation leaves the attempt open so it can be
retried; once the deadline passes, retry requires an admin reset.

## Build and boot

From the repository root:

```sh
sudo mkarchiso -v -w "$(mktemp -d /tmp/eduos-edulab-build.XXXXXX)" \
  -o "$PWD/out-admin" "$PWD/code/archiso/profile"
ISO="$(ls -t "$PWD"/out-admin/*.iso | head -n 1)"
echo "$ISO"
qemu-system-x86_64 -enable-kvm -m 4096 -smp 4 -boot order=d \
  -cdrom "$ISO" -drive "file=$PWD/runtime/eduos-state.img,format=raw,if=virtio"
```

Inside the VM, `cat /usr/share/eduos/build-info.txt` must include
`edulab-student-workspace-v2`. Check socket activation with:

```sh
systemctl status eduos-edulab.socket --no-pager
ls -l /run/eduos-edulab.sock
```

## Manual demonstration

1. Log in as `eduos`. Open Admin Dashboard → EduLab → Create Assessment.
   Enter `C++ Lab Assessment 1`, a timezone-qualified start time at or just
   before the current VM time, and duration `10`. Set question 1 to `Sum`,
   maximum marks `10`, statement `Read two integers and print their sum.`
   Set public example input to `2 3`. Add hidden tests `2 3` → `5` and
   `10 20` → `30`. Fill question 2 with a
   small C++ problem and two hidden tests. Save, select the assessment, and
   click Activate.
2. Log in as `student1000`. In the student app, select the assessment and
   click Start / Resume Assessment. Read the rules and confirm. Verify the
   fullscreen workspace, countdown, C++ starter template, public example
   input, and public statements; hidden tests are absent. Type a solution,
   click Run Code, and inspect Your Output in the scrollable results pane.
   Navigate between questions and return to verify that drafts persist.
3. Try Ctrl+V, Ctrl+C, and Alt+Tab. Clipboard shortcuts are blocked but do
   not count as violations. An actual exit from fullscreen is recorded.
   Press Escape and verify the workspace remains fullscreen with no popup.
   Return to the editor and submit. The result opens automatically with only
   your own code, actual output, verdict, score, and any compiler error.
4. Log in as `eduos`. Refresh EduLab, select the assessment, and click
   Results. Review source, actual output, verdict, score, and any compilation
   error. An assessment with no active attempt can be deleted along with its
   completed results and source after confirmation.
   To demonstrate timeout, click Reset Attempt for `student1000`, use a
   short-duration assessment, start it as the student, and wait for expiry.
   The saved drafts are submitted automatically and the result reason is
   `timeout`. Reset is an admin-only operation.
5. Both dashboards provide **Log Out / Switch User**, which invokes XFCE's
   fixed logout command and returns to SDDM. The control is intentionally not
   reachable while an EduLab attempt owns the fullscreen/grab.
