# Local EduOS machine management proof of concept

The machine layer is local to one EduOS installation. It does not contact a
server. The root-owned profile JSON in `/usr/share/eduos/profiles/` is the
desired state. The persistent root-owned state disk stores `machine.json` and
`status.json` in `/var/lib/eduos/`. A rebuilt ISO reuses the same machine ID
when booted with the same `EDUOS_STATE` disk.

## Format

The profile has exactly these fields: `profile_id` (safe stable identifier),
`name` (display text), `version` (positive integer), `required_packages`
(unique Arch package names), `required_services` (unique `.service` unit
names), `environment` (`{"cpp_standard":"c++17"}` in this prototype), and
`managed_files` (empty in this prototype). Each required service must be
enabled and active. Environment and managed files are reserved metadata and
are not enforced yet. Version 2 can be a separate validated JSON file with
its own ID and explicit assignment.

The registration has exactly `machine_id`, `hostname`, `assigned_profile`,
and `registered: true`. First boot creates `LAB-PC-001`, reads the hostname,
and assigns `CSE-LAB-CPP-v1`. Existing valid registration is preserved.
The machine ID is a fixed first-machine prototype ID, so use a separate
state disk before using more than one VM.

The latest status has `checked_at` (UTC ISO 8601), `machine_id`, `profile_id`,
`profile_version`, `compliant`, `packages` (`name`, `state`), `services`
(`name`, `enabled`, `active`), and `issues`. It is a cache for display only;
Refresh Status always runs a new inspection. A cached status for another
profile ID or version is ignored.

## Agent and privileges

`eduos-agent machine-info` and `eduos-agent status` read registration and the
cached status. `eduos-agent check` runs `pacman -Qq -- NAME` and
`systemctl is-enabled/is-active -- UNIT` for every validated requirement,
then prints structured JSON. It works without root and returns exit 1 for
drift. `eduos-agent sync` requires root, installs only missing packages with
`pacman -S --needed --noconfirm -- NAME`, enables/starts only required services,
and checks again. `eduos-agent register` is root-only and is used during setup.
No packages are removed. Package installation needs a reachable Arch mirror
or a usable local package cache.

The graphical dashboard remains unprivileged. Its Machines tab uses only the
existing fixed `sudo -n /usr/local/bin/eduos-admin-helper` rule. The helper
accepts `machine_info`, `machine_check`, `machine_assign` (a validated existing
profile ID), `machine_sync`, and the fixed terminal demo action
`machine_demo_drift`; package/service names are never accepted
from the GUI. The helper checks that sudo originated from `eduos`, and its
existing lock serializes machine changes with other admin operations.
Students cannot use that sudo rule. Profiles and state files are root-owned;
all writes use atomic replacement.

## Safe drift demonstration

The profile requires `gcc`, `gdb`, `git`, and `pv`. `gdb` and `git` are now
included in the ISO package list. `pv` was already in that list. It is a small
terminal pipeline progress utility with no role in SDDM, XFCE, networking,
Python/Tk, pacman, or the C++ evaluator. Arch's package page lists `glibc`
as its only dependency and `mkinitcpio-archiso` as an optional consumer:
<https://archlinux.org/packages/extra/x86_64/pv/>. Plain `pacman -R pv` will
refuse removal if another installed package requires it; never force removal
or use recursive removal options.

1. Boot the rebuilt ISO with its writable state disk, log in as `eduos`,
   open Admin Dashboard → Machines, and press **Refresh Status**. It should
   show `LAB-PC-001`, `CSE-LAB-CPP-v1`, and **COMPLIANT**.
2. In an `eduos` terminal, run the following fixed helper request. It removes
   only `pv` with normal pacman dependency checks; no package name is accepted
   from stdin. Do not use `-Rdd`, `-Rs`, or remove any other profile package.

   ```sh
   printf '%s\n' '{"action":"machine_demo_drift"}' | sudo -n /usr/local/bin/eduos-admin-helper
   ```
3. Press **Refresh Status**. It should show **DRIFT DETECTED** and `pv MISSING`.
4. Press **Synchronize** and confirm. The helper reinstalls `pv` from the
   configured Arch mirror/cache and rechecks. Press **Refresh Status** again
   to show **COMPLIANT**.

## Build and run

From the project root, create the state disk once (leave any existing one
intact), build with a fresh work directory, and run the latest ISO:

```sh
./scripts/create-eduos-state-disk.sh
sudo mkarchiso -v -w "$(mktemp -d /tmp/eduos-machine-build.XXXXXX)" \
  -o "$PWD/out-admin" "$PWD/code/archiso/profile"
./scripts/run-eduos.sh
```

The equivalent QEMU invocation, with `ISO` set to the new ISO path, is:

```sh
qemu-system-x86_64 -enable-kvm -m 4096 -smp 4 -boot order=d \
  -cdrom "$ISO" -drive "file=$PWD/runtime/eduos-state.img,format=raw,if=virtio"
```

The live ISO root filesystem is temporary. Removing `pv` is a drift demo for
the current boot; rebooting restores the ISO's package set. Registration and
last status persist on the state disk. Sync uses the local Arch package
database and configured mirrors; an unavailable mirror causes a reported
sync error. There is no timer and no automatic repair in this prototype.

## Confirm the running build

The dashboard subtitle and `/usr/share/eduos/build-info.txt` should both include
`machine-management-v2`. The runner prints the ISO it is about to boot. From
the project root, its default selection can be checked before starting QEMU:

```sh
ISO="$(ls -t "$PWD"/out-admin/*.iso | head -n 1)"
echo "$ISO"
```

Inside the VM, these commands distinguish an old image from a registration
problem:

```sh
cat /usr/share/eduos/build-info.txt
grep -n 'notebook.add(machines_tab' /usr/local/bin/eduos-admin-app
ls -l /usr/local/bin/eduos-agent /var/lib/eduos/machine.json
eduos-agent machine-info
eduos-agent check
eduos-agent status
journalctl -b -u eduos-setup.service --no-pager -n 30
```

The Machines tab is always created, with an **Open Machines** button in the
dashboard header. A failed machine check shows its sanitized error in that
tab; it does not remove the tab or the Students and Lab Assignments screens.
Setup creates `machine.json` on the mounted state disk before student-account
restoration. This ordering lets machine management remain available if an
existing account registry prevents student restoration. If boot registration
was missed, the first admin Refresh Status safely registers the machine; an
existing malformed record is never overwritten. Inspect the setup journal
to diagnose a restore or registration failure. A failed account restore still
needs separate repair for the affected student accounts.
