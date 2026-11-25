# EduOS

EduOS is an Arch Linux based live environment for programming laboratories.
It provides student coursework, timed C++ assessments, an administrator
dashboard, and local machine management. A separate `EDUOS_STATE` disk keeps
managed data after reboot.

Build the ISO from the repository root:

```bash
./scripts/create-eduos-state-disk.sh
sudo mkarchiso -v -w "$(mktemp -d /tmp/eduos-build.XXXXXX)" \
  -o "$PWD/out-admin" "$PWD/code/archiso/profile"
./scripts/run-eduos.sh
```

The repository `README.md` has the full build, backup, and restore guide.
Feature and release checklists are in `code/archiso/`.
