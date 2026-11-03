#!/usr/bin/env bash
# shellcheck disable=SC2034

iso_name="archlinux"
iso_label="ARCH_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="Arch Linux <https://archlinux.org>"
iso_application="Arch Linux Live/Rescue DVD"
iso_version="$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d)"
install_dir="arch"
buildmodes=('iso')
bootmodes=('bios.syslinux'
           'uefi.systemd-boot')
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')
bootstrap_tarball_compression=('zstd' '-c' '-T0' '--auto-threads=logical' '--long' '-19')
file_permissions=(
  ["/etc/shadow"]="0:0:400"
  ["/root"]="0:0:750"
  ["/root/.automated_script.sh"]="0:0:755"
  ["/root/.gnupg"]="0:0:700"
  ["/usr/local/bin/choose-mirror"]="0:0:755"
  ["/usr/local/bin/Installation_guide"]="0:0:755"
  ["/usr/local/bin/livecd-sound"]="0:0:755"
  ["/usr/local/bin/eduos-state-mount"]="0:0:755"
  ["/usr/local/bin/eduos-state-restore"]="0:0:755"
  ["/etc/systemd/system/eduos-state.service"]="0:0:644"
  ["/usr/local/bin/eduos-setup"]="0:0:755"
  ["/usr/local/bin/eduos-evaluator"]="0:0:755"
  ["/usr/share/eduos/eduos_submissions.py"]="0:0:644"
  ["/usr/share/eduos/eduos_evaluator.py"]="0:0:644"
  ["/usr/local/bin/eduos-student-app"]="0:0:755"
  ["/usr/local/bin/eduos-admin-app"]="0:0:755"
  ["/usr/local/bin/eduos-admin-helper"]="0:0:755"
  ["/usr/local/bin/eduos-agent"]="0:0:755"
  ["/etc/sudoers.d"]="0:0:750"
  ["/etc/sudoers.d/eduos-admin"]="0:0:440"
  ["/usr/share/eduos"]="0:0:755"
  ["/usr/share/eduos/eduos_machine.py"]="0:0:644"
  ["/usr/share/eduos/build-info.txt"]="0:0:644"
  ["/usr/share/eduos/profiles"]="0:0:755"
  ["/usr/share/eduos/profiles/CSE-LAB-CPP-v1.json"]="0:0:644"
  ["/usr/share/eduos/assignments.json"]="0:0:644"
  ["/usr/share/eduos/eduos_assignments.py"]="0:0:644"
  ["/usr/share/eduos/student-data.json"]="0:0:644"
  ["/usr/share/eduos/eduos-admin.desktop"]="0:0:644"
  ["/etc/systemd/system/eduos-admin-launcher.service"]="0:0:644"
  ["/etc/xdg/autostart/eduos-admin-app.desktop"]="0:0:644"
  ["/etc/tmpfiles.d/eduos-admin.conf"]="0:0:644"
)
