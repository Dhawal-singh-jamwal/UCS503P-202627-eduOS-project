# EduOS Evaluator — local C++ prototype

The evaluator compiles C++17 locally and runs exactly two visible test cases.
It does not add a server, container infrastructure, grading, history or submission
storage by itself. Run Tests results remain transient. The subsequent
[local submission feature](SUBMISSIONS.md) adds explicit Submit Code with a fresh
evaluation and private persistent-state storage.

## Compatibility with the actual repository

The current portals already use `assignments.json` (lab → problems), with private
student projections. `student-data.json` retains student profiles and deprecated
`tasks` arrays; the helper explicitly forbids changing those arrays. This feature
extends the active problem format instead of reviving a second coursework model.
The legacy JSON and existing assignment/submission records are not migrated.

The admin edits assignment title, description, due information and student
membership in the existing assignment editor. Within its Problems tab, each
problem has its own title, status, statement, language, evaluator checkbox and
two tabs containing multiline input/expected output. New problems default to C++
with evaluation enabled. Existing problems without tests keep evaluation disabled
until the admin enables it. Existing non-C++ records remain editable, but cannot
have evaluator tests. No additional language execution is implemented.

Example record inside the existing `assignments` array:

```json
{
  "id": "LAB-0001",
  "title": "C++ Lab 1",
  "description": "Practice input and output",
  "due": "2026-10-30",
  "assigned_to": ["student1000"],
  "created_by": "eduos",
  "created_at": "2026-10-27T12:00:00+00:00",
  "next_problem_id": 2,
  "problems": [
    {
      "id": "Q1",
      "title": "Addition of Two Numbers",
      "status": "Assigned",
      "language": "C++",
      "problem_statement": "Read two integers and print their sum.",
      "test_cases": [
        {"input": "2 3\n", "expected_output": "5\n"},
        {"input": "10 20\n", "expected_output": "30\n"}
      ]
    }
  ]
}
```

`status` and `test_cases` are optional additions. Tests must contain exactly two
objects, each with string `input` and `expected_output`. Empty input/output are
valid. Each string is limited to 16 KiB of UTF-8. Existing overall 1 MiB metadata
limits, validation, helper lock, revision check, atomic replacement and private
projection publication still apply. Due information stays on the parent lab.

## Execution and UI

The student opens a lab, selects a problem and clicks Open Problem. Evaluator
problems open an EduOS Evaluator workspace with statement, monospace editor,
visible test cases, Run Tests, Submit Code, submission status and results. It follows the parent's fullscreen
setting; F11 toggles fullscreen and Escape leaves fullscreen.

Run Tests snapshots the editor, disables its button and starts a Python worker
thread. The worker calls `eduos_evaluator.evaluate_cpp(source_code, test_cases)`.
Tk polls a queue; the worker does not touch widgets. Opening/refreshing the portal
does not replace an open editor. Closing the workspace preserves its draft in
memory; exiting the application discards drafts. Closing during evaluation asks
the user to wait for completion. A non-daemon worker finishes bounded cleanup if
the root window closes.

Each evaluation:

1. Reject root/elevated UID execution, malformed tests and empty/oversized source
   (maximum 128 KiB UTF-8).
2. Create a private `eduos-evaluator-*` temporary directory explicitly in `/tmp`.
3. Write `solution.cpp`; compile with `/usr/bin/g++ solution.cpp -std=c++17 -O2 -o solution` inside Bubblewrap.
4. Bound compilation to 20 seconds and 64 KiB per output stream.
5. Run the binary twice inside Bubblewrap, with separate fresh test working directories and stdin
   from each case. Each test has a two-second wall-clock timeout.
6. Drain stdout/stderr incrementally, keeping at most 64 KiB of each. Exceeding
   either limit immediately terminates the process group. All launches use argv
   arrays, fixed environment variables, and no shell.
7. Kill remaining group members and reap the direct process on every completion,
   error or timeout. Remove the whole temporary evaluation directory.
8. Return structured compilation/test results. Show PASS, FAIL, RUNTIME ERROR,
   TIME LIMIT EXCEEDED or OUTPUT LIMIT EXCEEDED, with input, expected, actual,
   bounded diagnostics and an overall X / 2 passed count.

Compilation errors execute no tests. Missing compiler or filesystem errors appear
as Evaluation unavailable. Output comparison converts CRLF to LF, strips trailing
whitespace on each line and trailing blank lines; leading/interior spaces and
interior blank lines remain significant.

The executable `/usr/local/bin/eduos-evaluator` exposes the same component through
JSON stdin/stdout: request keys `source_code` and `test_cases`, maximum 1 MiB;
response keys `compile_success`, `compile_error`, `tests`, and `error`. Tests have
`passed`, `status`, `input`, `expected`, `actual`, `error`. The GUI imports the
module directly; it does not launch the CLI or use the administrator helper.

Existing submission controls remain available for problems without evaluator
cases. Run Tests does not save or change submission progress. Submit Code now
stores the fresh evaluation explicitly and contributes to submitted counts; see
[SUBMISSIONS.md](SUBMISSIONS.md). Passing tests alone does not imply submission.

## Safety boundary

Compilation and execution now require Bubblewrap. It exposes `/usr` and `/etc`
read-only, a private `/tmp`, and only the current scratch directory as writable;
the student's home and EduOS state are not mounted. `--unshare-all` removes host
network access and separates the process namespace. `prlimit` caps address space,
file size, open files, CPU time, and new processes. Missing or failed isolation
stops evaluation. The EduLab service also changes to a dedicated non-root UID
before invoking the evaluator. This is stronger isolation, but it has not been
security audited as an adversarial exam sandbox; kernel vulnerabilities and
resource exhaustion outside these limits remain possible.

The evaluator never uses sudo or the admin helper. The submission feature extends
the helper only to read validated saved records; see [SUBMISSIONS.md](SUBMISSIONS.md).
The sudoers rule, account provisioning, desktop/login configuration and persistence
mount behavior are unchanged. The ISO includes `gcc`, `bubblewrap`, and `prlimit`
(from util-linux); Python and Tk were
already in the profile. The evaluator needs an executable `/tmp` filesystem.

## Validation

```bash
python3 -B -m unittest discover -s code/archiso/tests -v
```

Run as an ordinary user with g++ installed. Real compiler tests are skipped under
root or without g++; do not bypass the root refusal. Tests cover correct/wrong
answers, compile errors/timeouts/output caps, runtime failures/crashes, infinite
loops, stdout/stderr caps, empty and multiline output, normalization, malformed
requests, temporary cleanup, per-case directory separation, old records, admin
save/reopen/stale saves, editor serialization, worker polling and draft snapshots.
Existing account, persistence, projection and submission tests also run. GUI
callback tests use mocks; they do not replace a visual VM acceptance test.

## Clean build and boot

Use these commands in the same Bash session. A newly allocated build directory
avoids stale mkarchiso state and preserves earlier images and persistent data.
The profile produces an `eduos-*.iso` filename.

```bash
cd /home/Dhawal/eduOS-project
build_root="$(mktemp -d /home/Dhawal/eduOS-project/evaluator-build.XXXXXX)"
sudo mkarchiso -v -w "$build_root/work" -o "$build_root/out" \
  /home/Dhawal/eduOS-project/code/archiso/profile

# Creates the existing project's state disk only if absent; never reformats it.
./scripts/create-eduos-state-disk.sh
iso_files=("$build_root"/out/*.iso)
qemu-system-x86_64 -enable-kvm -m 4096 -smp 4 -boot order=d \
  -cdrom "${iso_files[0]}" \
  -drive file=/home/Dhawal/eduOS-project/runtime/eduos-state.img,format=raw,if=virtio
```

## Manual end-to-end demo

1. Log in as `eduos`; open Admin Dashboard → Lab Assignments → Create Lab Assignment.
2. Set the lab title/due date, assign `student1000`, and select Problems → Add Problem.
3. Set title **Addition of Two Numbers**, status **Assigned**, language **C++**,
   and keep **Enable EduOS Evaluator** checked. Enter the statement from the example.
4. In Test Case 1 enter `2 3` and expected `5`; in Test Case 2 enter `10 20` and
   expected `30`. Save Problem, then Save Lab Assignment. Edit it again to verify
   the statement and both test cases survived saving.
5. Log out; log in as `student1000`. Open the assigned lab and its problem. Enter:

   ```cpp
   #include <iostream>
   using namespace std;
   int main() {
       int a, b;
       cin >> a >> b;
       cout << a + b << '\n';
       return 0;
   }
   ```

6. Run Tests: expect PASS for both cases and **2 / 2 passed**.
7. Change `a + b` to `(a == 10 ? 29 : a + b)`; run again: expect PASS, FAIL and
   **1 / 2 passed**, with actual `29` for the second case.
8. Remove a semicolon; run again: expect Compilation Failed, compiler diagnostics,
   and no tests executed. Correct it afterward.
9. Replace the program with `int main() { while (true) {} }`; run again: both
   cases should report TIME LIMIT EXCEEDED. The UI should remain responsive.
10. Close/reopen the workspace: its draft should remain. Exit/restart the student
    application: unsaved drafts/results should be gone; explicitly submitted source
    and status are restored by the submission feature. Existing account management and
    non-evaluator submission workflows should still function.

No ISO build or QEMU boot is performed by the automated unit tests.
