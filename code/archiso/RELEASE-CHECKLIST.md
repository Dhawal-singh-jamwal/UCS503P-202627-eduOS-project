# EduOS release smoke checklist

Use a disposable copy of the state image for release checks. The automated unit
tests do not verify boot firmware, graphical login, or persistence after a real
VM restart.

## Build and identify the image

```bash
python3 -B -m unittest discover -s code/archiso/tests -v
bash -n scripts/*.sh code/archiso/profile/profiledef.sh
sudo mkarchiso -v -w "$(mktemp -d /tmp/eduos-release.XXXXXX)" \
  -o "$PWD/out-admin" "$PWD/code/archiso/profile"
```

Record the ISO name and SHA-256 digest. It should be `eduos-*.iso`, and the boot
menu should say **EduOS live environment**.

## BIOS and UEFI

1. Boot the ISO with `./scripts/run-eduos.sh` (BIOS). Check the EduOS splash,
   SDDM user ID and password fields (without a user picker), XFCE desktop, and
   both dashboards. Check that Machines shows Refresh Status and Synchronize.
2. Shut it down. For UEFI, copy `/usr/share/OVMF/x64/OVMF_VARS.4m.fd` to a private
   writable file and boot with QEMU's read-only `OVMF_CODE.4m.fd` and writable
   VARS file. Check the same login and desktop path.
3. In the guest, check `systemctl --failed`, `systemctl status eduos-state.service
   eduos-setup.service eduos-edulab.socket`, and `cat /usr/share/eduos/build-info.txt`.
   Investigate any service failures before release.

## State and teaching workflow

1. Create a new student and a C++ lab problem. Submit code as the student and
   view it as admin. Reboot with the same state disk. Verify the login, lab,
   evaluated source, and result are still present.
2. Create and activate a timed EduLab assessment. Submit it, leave instructor
   feedback and a final score, then verify the student's result and CSV export.
3. Export a machine status snapshot. Import a second PC's snapshot into the
   Machines tab and verify its ID and status appear as **unverified**.
4. Shut down, back up the state image, restore into a disposable project copy,
   and boot that copy. Verify accounts and coursework. Corrupting a copied
   backup should make restore fail its checksum check.

Capture BIOS boot menu, admin overview, student submission, and EduLab result
screenshots for the release report. Keep student names and passwords out of any
public screenshots.
