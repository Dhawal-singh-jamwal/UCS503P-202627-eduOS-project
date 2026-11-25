# EduOS

EduOS is an Arch Linux based live OS for programming labs. It boots into XFCE and provides separate administrator and student dashboards. A labeled `EDUOS_STATE` disk holds accounts, assignments, submissions, machine state, and EduLab assessment results across reboots.

## What it does

- Administrators manage student accounts, lab assignments, timed C++ assessments, and the local machine profile.
- Students open assigned problems, run visible C++ tests, submit code, and take timed assessments with hidden tests.
- Evaluated code runs through Bubblewrap with a separate filesystem and network namespace, resource limits, and a non-root identity. See [evaluator details](code/archiso/EVALUATOR.md).
- EduLab instructors can review completed attempts, leave feedback, set a final score, and export CSV results.
- Each new state disk gets a random machine ID. The Machines tab can export this PC's status and import unverified snapshots from other PCs for an offline fleet view.

## Build and run

Requirements: Arch Linux host with `archiso`, QEMU, Python 3, and enough disk space for an ISO and a 4 GiB sparse state image. KVM is used by the included runner.

```bash
./scripts/create-eduos-state-disk.sh
sudo mkarchiso -v -w "$(mktemp -d /tmp/eduos-build.XXXXXX)" \
  -o "$PWD/out-admin" "$PWD/code/archiso/profile"
./scripts/run-eduos.sh
```

The first command does not overwrite an existing state image. The runner selects the newest ISO in `out-admin/` or `out/` and uses `runtime/eduos-state.img`. To select an ISO explicitly, run `./scripts/run-eduos.sh /absolute/path/to/eduos.iso`.

The SDDM login screen asks for a user ID and password directly, without a user picker. The built-in demonstration administrator is `eduos` with password `eduos`. The five initial students are `student1000` through `student1004`, with passwords `user1` through `user5`. Change these credentials before using real student data. A new machine needs its own state disk; copying a disk also copies its machine ID and accounts.

## Back up and restore state

Shut down the VM cleanly before backing up. A backup includes account password hashes and student code, so store it privately.

```bash
./scripts/backup-eduos-state.sh /path/to/eduos-state-backup.img
```

The command creates an image and a `.sha256` checksum file. It refuses an existing destination or a state image held by the included VM runner. To restore, stop the VM and move the current image aside first, then run:

```bash
mv runtime/eduos-state.img runtime/eduos-state.before-restore.img
./scripts/restore-eduos-state.sh /path/to/eduos-state-backup.img
```

Restore verifies the checksum before and after copying, and refuses to overwrite an existing state image. Keep the moved image until you have booted and checked the restored state. See [state and account behavior](code/archiso/ADMIN-DASHBOARD.md).

## Offline fleet view

On each EduOS PC, choose **Machines → Export This PC** or save `eduos-agent export` output as JSON. Transfer the file to the administrator's PC and choose **Machines → Import PC Status**. The list shows the last imported check for each machine. Imports are unverified snapshots, not live network telemetry; refresh and export again to update them. Only the local machine can be synchronized from the dashboard. See [machine management](code/archiso/MACHINE-MANAGEMENT.md).

## Test and release check

```bash
python3 -B -m unittest discover -s code/archiso/tests -v
```

The evaluator tests need a host that permits unprivileged user namespaces. Before distributing an ISO, follow the [VM smoke checklist](code/archiso/RELEASE-CHECKLIST.md), including BIOS and UEFI boot, student submission after reboot, backup restoration, assessment review, and machine status import.

## Current limits

EduOS is a live OS: ordinary home files and XFCE settings are temporary. Managed coursework and assessment data persist on the state disk. Timed assessment fullscreen controls are a best-effort X11 kiosk layer. Bubblewrap isolation improves code execution safety, but this prototype has not been security audited for adversarial exams. Fleet snapshots are manually transferred and are not authenticated.

EduOS is a UCS503P software engineering project. The project source is under `code/archiso/`; documentation for individual features is alongside it.
