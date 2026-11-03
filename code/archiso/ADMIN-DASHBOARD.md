# EduOS persistent state and Lab Assignments

EduOS still boots an immutable ArchISO into SDDM and XFCE X11. Both dashboards
retain fullscreen startup, F11 toggle and Escape to leave fullscreen. The admin
has Exit Dashboard and its existing personal menu launcher. Neither GUI runs as
root. The student portal exits before opening Tk for non-student Linux accounts;
the admin portal still checks real/effective UID and the exact `eduos` identity.

## Create, build and run

From `/home/Dhawal/eduOS-project`:

```sh
# Once: creates a sparse 4 GiB ext4 image; NEVER reformats an existing image.
./scripts/create-eduos-state-disk.sh

# Build into a fresh work directory. Do not remove runtime/ during build cleanup.
sudo mkarchiso -v -w "$(mktemp -d /tmp/eduos-admin-build.XXXXXX)" \
  -o "$PWD/out-admin" "$PWD/code/archiso/profile"

# Uses the newest ISO in out-admin/ or out/, with the SAME writable state image.
./scripts/run-eduos.sh
# Or explicitly choose an ISO:
./scripts/run-eduos.sh /absolute/path/to/eduos.iso
```

The run script preserves KVM, 4096 MiB RAM, four vCPUs and CD boot. It attaches
`runtime/eduos-state.img` as a raw writable virtio drive. It never creates a new
disk on boot and does not use QEMU snapshot mode. Shut down the VM cleanly before
backing up or moving the state image. Do not boot two VMs against the same image.
`runtime/` is ignored by Git and separate from ISO output directories. The image
contains password hashes and student code: keep the host file private (0600).

## Why data disappeared, and what persists now

Previously `/etc/passwd`, `/etc/shadow`, JSON changes and student submissions
were all in the live ISO's temporary overlay. Reboot discarded that overlay.
Writing to an ISO path did not make those changes part of the ISO.

`eduos-state.service` runs before `eduos-setup.service`. It waits for udev,
looks only for `/dev/disk/by-label/EDUOS_STATE`, and mounts ext4 at
`/var/lib/eduos` with `nodev,nosuid,noexec`. It never formats a guest disk.
If the label is absent it logs an explicit EPHEMERAL warning and provisioning
continues on the temporary filesystem. If a labeled disk exists but mounting
fails, setup fails rather than quietly creating a second, temporary dataset.

```text
/var/lib/eduos/                         root-owned, 0755
  student-data.json                    root:root, 0644 (profile metadata)
  assignments.json                     root:root, 0600 (central coursework)
  account-state.json                   root:root, 0600 (UID/GID + shadow state)
  account-recovery.json                root:root, 0600 (prior lock/expiry state)
  deleted-students.json                root:root, 0600 (permanent deletion markers)
  student-views/                       root-owned, 0755
    student1000/                      student1000-owned, 0700
      assignments.json                student1000-owned, 0600
  submissions/                        root-owned, 0755
    student1000/                      student1000-owned, 0700
      LAB-0001/Q1/                    student1000-owned, 0700
        source.cpp                    student1000-owned, 0600
        submission.json               student1000-owned, 0600
```

First boot seeds student metadata from the ISO default only if the persistent
file does not exist. Assignments start empty and account registries are created
only if missing. Existing files are validated, never reset or reseeded. A
root-only `deleted-students.json` marker prevents permanently deleted student
IDs from being restored or seeded again. Invalid state requires administrator
repair, not silent replacement. Ordinary home files and XFCE preferences remain
ephemeral; this feature persists the managed EduOS state above, not the entire
live OS or `/home`.

## Account restoration and password handling

`eduos-setup` retains the administrator provisioning and invokes the root-only
`eduos-state-restore` entry point for students. This boot entry point is NOT an
action accepted by the sudo helper. On a fresh state disk it provisions the same
five built-in students with the existing initial credentials.

After create/reset/disable/enable (and profile saves), while holding the existing
administrative lock, the helper snapshots validated managed Linux identities and
shadow password state into `account-state.json` using the existing atomic writer.
It stores UID, private GID, password HASH, last-change/aging fields, and expiry.
The separate recovery document retains the prior lock/expiry state for Enable.
Plaintext passwords are used transiently for `chpasswd` stdin only; they are never
written to JSON, logs, argv, projections or helper responses. Restored hashes also
travel through stdin (`chpasswd --encrypted`), never command-line arguments.

On reboot, recorded identities are restored before new built-ins are allocated.
Persisted registry identities are restored unless marked deleted; this also
preserves recovery of a partially created account with missing metadata.
Built-in bootstrap requires a persistent student record and no deletion marker.
Missing groups/accounts are recreated with their recorded GID/UID, home and bash
shell; roll information is restored from persistent metadata. Hashes and aging/
expiry are reapplied. Persisted students never receive default passwords again.
UID/GID collisions with unrelated accounts cause a safe failure rather than
reassigning another user's files. Names and roll numbers remain in student-data.
Private projections/submission roots are then ensured and views regenerated.
Reusing the same state disk with a rebuilt compatible ISO retains this state.

Linux account operations and JSON persistence cannot form a single transaction.
If storage fails after an account change, the helper reports PARTIAL FAILURE;
repair the disk and persist the state before rebooting. Failed provisioning keeps
accounts/homes for recovery instead of deleting them. Persistence is guaranteed
for completed successful operations, not an interrupted account mutation.

## Delete Student

The Students tab offers **Delete Student** for both existing Linux accounts and
orphaned EduOS records. The action requires confirmation and goes through the
same fixed sudo helper; no GUI-supplied path or package command is accepted.
The helper accepts only `student` followed by four ASCII digits, checks that
an existing account is an ordinary, local, non-privileged student, verifies
its home is `/home/<username>`, and refuses deletion while that UID has any
running process. Existing accounts are removed with `userdel --remove`.

Before removing an account or metadata, the helper atomically records the ID
in root-only `deleted-students.json`. It then removes that student from
`student-data.json`, persistent account/recovery records, assignment recipient
lists, and the exact per-student submission and view trees under
`/var/lib/eduos`. An assignment with no remaining recipients is removed;
shared assignments stay assigned to the other students. Every JSON update
uses the existing lock and atomic writer. If cleanup fails, the marker blocks
re-creation at reboot and the admin can refresh and retry deletion. Deleted
IDs cannot be reused through Create Student; choose a new student ID.

For an orphaned record, a stale `/home/<username>` is removed only when its
directory is not a symlink or mount and its owner matches the UID retained in
the trusted account registry. If that UID is unavailable, the home is left
untouched and the helper returns a warning. The per-student EduOS state trees
have root-owned parent directories and are removed by exact validated name.
No arbitrary path is accepted. The live ISO does not persist `/home`, but
cleanup still handles a stale home in the current boot.

## One coursework model: lab → problems → submissions

The admin has Students and Lab Assignments tabs. The visible Tasks UI and its
editing actions have been removed. Existing `tasks` fields remain untouched as
deprecated legacy metadata; helper saves reject changes to existing task lists.
No automatic destructive task migration takes place.

Create Lab Assignment opens three steps/tabs: details (title, description, due),
actual student selection (Select All/Clear All), and problems. Each problem has
a title, large multiline statement and language. Optional C++ evaluator settings
add a status and exactly two multiline input/expected-output pairs; see
[EVALUATOR.md](EVALUATOR.md) for the workflow and safety limitations. At least one problem and one
student are required to save/publish. Problems may also be added/edited/deleted
from the selected assignment afterward. Deleting the final problem is rejected;
delete the lab instead if it is no longer needed.

The central store has an `assignments` array and a persisted `next_id` counter.
Each lab has stable `LAB-0001`-style ID, title, description, due, assigned Linux
usernames, creator/time, `problems`, and `next_problem_id`. Problems have stable
`Q1`-style IDs, title, statement and language. IDs are preserved on edit and
committed deleted IDs are not reused. Revision checks prevent stale/repeated
creation requests. Due information is descriptive, not a deadline policy.
The previous single-problem prototype format is not silently rewritten: any
manually imported old assignment document must be converted explicitly first.

## Private assignment visibility

The central assignment document is 0600, inaccessible to students. Students open
only their own `student-views/<real Linux username>/assignments.json`, containing
only labs assigned to that account. Other students cannot traverse the 0700
parent or read its 0600 file. The base directory is root-owned, so a student
cannot replace another student's top-level directory. UID identity, not HOME or
USER environment variables, chooses the projection and submission root.

Every assignment/problem/membership mutation clears old projections before
committing the central update, then atomically publishes fresh projections.
If updating fails, some views may be empty; Refresh in the admin Lab Assignments
tab repairs them from the authoritative central store. This fails closed rather
than leaving newly revoked assignments in stale files. Boot also regenerates
views. Projection writes use directory descriptors, exclusive temporary files,
fsync and atomic replacement without following student-controlled symlinks.
Students can edit their own files; projections enforce confidentiality from
other accounts, not tamper-proof local progress. Previously read/copied work
cannot be revoked from a student's memory or their own copies.

## Student workflow and submission storage

The dashboard shows only Lab Assignments. Open Lab lists problems and their
Pending/Submitted status. Open Problem shows the statement and a code editor;
For problems without evaluator test cases, Submit Code is the submission mechanism. Existing code is loaded for editing
and resubmission. There is no filename input, file picker, upload or Save As.

Fixed language mapping: C++ → `source.cpp`, C → `source.c`, Java → `Main.java`,
Python → `solution.py`, Text → `source.txt`. The application chooses all paths
from the authenticated username and validated lab/problem IDs. It refuses empty
or over-1-MiB code, rechecks the private assignment view, and writes only under
`submissions/<username>/<lab>/<problem>/`. No code is stored under `/usr/share`.

Metadata contains username, assignment ID, problem ID, UTC submitted_at, language,
Submitted status, and a content digest. Resubmission replaces the same source and
metadata. Source/metadata use individual atomic writes and a directory lock; an
interruption between them can produce a digest mismatch, reported as Pending
until resubmitted. Directories/files reject symlinks, foreign ownership, unsafe
permissions and nonregular files; privileged reads also reject hard links and
use bounded nonblocking opens. Removing an assignment, problem, or student
membership never deletes saved submissions.

Both portals count current problems: 0/N = Not Started, some/N = In Progress,
N/N = Submitted. This is local completion tracking, not grading or proof that
student-controlled metadata is authentic.

## Admin submission viewer and helper boundary

Selecting a student loads Assigned Lab Work and progress. View Submissions lists
problem status and submission time. View Code opens a disabled/read-only text
widget. Refresh Lab Progress retrieves the latest counts after student submissions.

Only the existing sudo-authorized helper reads other users' source. The fixed
`list_student_submissions` and `get_submission` actions validate the local
nonprivileged student account, lab ID, problem ID and current central membership.
They construct paths internally and return only requested data. There is no
arbitrary file/path/command action. `get_submission` performs no code mutation.
The sudoers policy is unchanged; the GUI remains an ordinary `eduos` process.
C++ problems can now enable the [EduOS Evaluator](EVALUATOR.md) with exactly two
visible test cases. Evaluation runs unprivileged in the student session; it does
not use this helper or update grading/marks. Explicit Submit Code saves a fresh
evaluation privately in the student home; this helper reads those records through
the same restricted viewing operations. See [SUBMISSIONS.md](SUBMISSIONS.md).

## Validation and VM acceptance

```sh
python3 -B -m unittest discover -s code/archiso/tests -v
bash -n scripts/*.sh code/archiso/profile/profiledef.sh
```

Tests use temporary files and mocked Linux account operations, including a
simulated reboot that removes the account database while retaining disk state.
Host script tests format only a newly allocated temporary image, verify its
filesystem label, and prove existing images are left untouched. They do not boot
QEMU or modify host users. Tk callbacks use mocks if Tk is unavailable.

After rebuilding, perform the real VM acceptance test: create student1005, create
a multi-problem LAB-0001 for that student, log in and submit Q1, view it as admin,
shut down cleanly, and rerun `scripts/run-eduos.sh` using the same disk. Verify
login/password, assignments, progress and code. Repeat with a rebuilt ISO.
Also verify another student cannot read the central store, private projection or
submission directory; verify reset/disable/enable across reboot; and test a boot
without the state disk for the explicit ephemeral warning. Source tests alone do
not certify the final XFCE layout or full guest boot/shutdown lifecycle.

## Evaluator submissions

The existing View Submissions window now shows evaluator scores and timestamps.
View Code includes compilation results, test statuses and source. See
[SUBMISSIONS.md](SUBMISSIONS.md) for home storage, security checks, logout/login
persistence limits, build commands and the complete demo.
