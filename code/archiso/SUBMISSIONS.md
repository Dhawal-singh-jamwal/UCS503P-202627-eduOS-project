# Local evaluator code submissions

The C++ workspace now has **Run Tests** and **Submit Code**. Run Tests remains
transient. Submit Code evaluates the current editor snapshot with the unchanged
`eduos_evaluator.evaluate_cpp` implementation, then saves one JSON document.
Wrong answers, runtime failures, timeouts and compilation errors can be submitted.
An unavailable evaluator (for example, missing compiler), revoked/changed problem,
or failed storage operation does not report successful submission.

Both buttons disable while a worker runs. The worker never calls Tk; results
arrive through its queue. Editing during a run does not change the snapshot being
submitted. The workspace displays submission status, UTC timestamp and result.
On reopening, it restores submitted source unless an in-session draft already
exists. Due information is displayed but does not prevent submission.

## Identity and schema

Active coursework lives in `assignments.json`, with immutable `LAB-0001` assignment
IDs and `Q1` problem IDs. The composite submission ID is `LAB-0001-Q1`. Titles,
due dates and statement edits do not alter it. Existing problems already have
these IDs and need no new `task_id` field or migration. Legacy `student-data.json`
task arrays remain untouched. Problems without evaluator tests retain the existing
submission workflow under `/var/lib/eduos/submissions`.

Evaluator submissions use the real Linux account from `pwd`/UID, ignoring HOME,
USER and XDG_DATA_HOME. The student API accepts only assignment ID, problem ID and
source; it accepts no target username or filesystem path.

```text
/home/student1000/.local/share/eduos/submissions/LAB-0001-Q1.json
```

Example record (each test retains the evaluator's existing result fields):

```json
{
  "schema_version": 1,
  "student": {"username": "student1000", "roll_number": "1000"},
  "task": {
    "task_id": "LAB-0001-Q1",
    "title": "Addition of Two Numbers",
    "language": "C++"
  },
  "source_code": "#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b;}\n",
  "submitted_at": "2026-10-27T15:15:00+00:00",
  "evaluation": {
    "compile_success": true,
    "compile_error": "",
    "error": "",
    "passed": 2,
    "total": 2,
    "tests": [
      {"passed": true, "status": "PASS", "input": "2 3\n", "expected": "5\n", "actual": "5", "error": ""},
      {"passed": true, "status": "PASS", "input": "10 20\n", "expected": "30\n", "actual": "30", "error": ""}
    ]
  }
}
```

Compilation failure records have `compile_success: false`, compiler diagnostics,
`passed: 0`, `total: 2`, and an empty `tests` array because no cases ran. A problem
snapshot is loaded freshly before evaluation and checked again before saving, so
a detected edit/revocation during evaluation requires reopening and retrying.

## Atomic storage and permissions

`eduos_submissions.py` reuses the existing atomic writer: exclusive temporary
0600 file, write, flush, fsync, atomic replacement and directory fsync. One
nonblocking directory lock serializes saves. Resubmission replaces the same JSON;
there is no attempt history or separate source/result transaction. Source is
preserved exactly. Records have a 2 MiB serialized limit and strict field/type,
identity, timestamp and result validation.

All directory traversal uses file descriptors and `O_NOFOLLOW`. The `/home`
anchor must be root-owned and not writable by other users. The account's home
must match `/home/<validated username>` and belong to that account. Home,
`.local`, and `share` cannot be writable by other users. `eduos` and `submissions`
are private 0700 directories; existing unsafe directories are rejected rather
than silently changing permissions. Newly created parents are also 0700.

Readers reject symlinks, hard links, foreign-owned files, group/world-accessible
files, nonregular files and oversized/malformed JSON. Opens are nonblocking so
an attacker-created FIFO cannot hang the privileged reader. Root never executes
or evaluates submitted code. Invalid records display as unavailable/pending with
a warning rather than being trusted.

A failure before replacement preserves the old record and removes the temporary
file. A directory-fsync failure after replacement can leave the new complete
record visible despite an error; the UI reports failure because durability could
not be confirmed. Reopen to inspect it and retry. An abrupt power loss may leave
a private temporary file, but never a partially replaced destination JSON.

## Administrator viewing

Use **Students → select student → Refresh Lab Progress → select assigned lab →
View Submissions → select problem → View Code**. The Submissions window includes
time and evaluation summary. The disabled source viewer shows student, roll,
stable task ID/title, timestamp, compilation result/diagnostics, individual test
statuses/runtime diagnostics and complete source. Refresh Lab Progress before
reopening the window after a resubmission.

The existing fixed JSON helper requests remain:

```json
{"action":"list_student_submissions","username":"student1000"}
{"action":"get_submission","username":"student1000","id":"LAB-0001","problem_id":"Q1"}
```

Their existing authorization, strict request shape, local nonprivileged student
validation, ID validation and current assignment-membership checks remain. The
helper selects the evaluator reader only for evaluator problems, constructs the
path internally and returns validated data. Lists omit full source/result bodies;
the selected viewer retrieves the full record. Extra `path` or filename arguments
are rejected. The sudoers rule is unchanged. Students save directly as themselves,
without sudo or the helper. Existing non-evaluator submissions remain readable.

## Limitations

Submission JSON belongs to the student and can be edited or deleted by that
student outside the GUI. Stored evaluation and timestamps are local coursework
metadata, not tamper-proof grades. Native C++ execution retains the evaluator's
existing limitations; this feature adds no sandbox, network restriction or grading.
Historical records are not retained. The admin viewer lists currently assigned
problems; deleted/revoked problems are not exposed by the helper, although their
files are not deleted.

**Home persistence:** files survive logout and reopening the app, but this live
ArchISO currently does not persist `/home` across VM reboot. The existing state
disk persists `/var/lib/eduos`, including older non-evaluator submissions. This
milestone does not change mounts, provisioning or copy home records into that
state disk. Demonstrate the requested logout/login flow within the same VM boot.

## Checks, build and demo

```bash
python3 -B -m unittest discover -s code/archiso/tests -v
```

Tests use temporary fake student homes, mocked privileged account/ownership
boundaries, and harmless real C++ programs. Coverage includes fresh evaluation,
pass/partial/compile-error saves, exact source/IDs/timestamps, replacement,
atomic failure, permissions, traversal/symlink/hardlink/FIFO rejection, helper
reads, malformed records, unchanged old records, transient Run Tests and UI
success/failure paths. GUI widgets are mocked; a real VM smoke test is still needed.

Use the following commands in the same Bash session:

```bash
cd /home/Dhawal/eduOS-project
build_root="$(mktemp -d /home/Dhawal/eduOS-project/submissions-build.XXXXXX)"
sudo mkarchiso -v -w "$build_root/work" -o "$build_root/out" \
  /home/Dhawal/eduOS-project/code/archiso/profile
./scripts/create-eduos-state-disk.sh
iso_files=("$build_root"/out/*.iso)
qemu-system-x86_64 -enable-kvm -m 4096 -smp 4 -boot order=d \
  -cdrom "${iso_files[0]}" \
  -drive file=/home/Dhawal/eduOS-project/runtime/eduos-state.img,format=raw,if=virtio
```

The fresh directory avoids stale mkarchiso state; the state-disk script preserves
an existing disk. No package additions are needed for this feature.

1. Admin: log in as `eduos`, create/confirm a C++ evaluator problem assigned to
   `student1000`, with addition cases `2 3 → 5` and `10 20 → 30`.
2. Student: log in as `student1000`, open the problem, enter correct C++ source.
   Run Tests; verify 2/2 and **Not Submitted** (use a new problem for this check).
3. Click Submit Code. Verify **Submission successful**, timestamp and 2/2.
   Close/reopen the problem; verify status/source. Log out without rebooting.
4. Admin: select student1000, refresh lab progress, select its lab and View
   Submissions. Select Q1 → View Code; check time, score, statuses and exact source.
5. Student: log back in, alter code to produce `29` for the second case, and submit
   again. Verify successful submission with 1/2; no all-pass requirement exists.
6. Admin: refresh/reopen submissions; verify updated time, source and 1/2. The same
   task has one current record. Repeat with a syntax error to show compilation-
   error submission; ordinary Run Tests must never replace the saved record.
