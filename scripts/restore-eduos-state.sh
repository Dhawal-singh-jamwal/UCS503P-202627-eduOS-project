#!/usr/bin/env bash
# Restores a verified host backup only when no active state image exists.
set -euo pipefail
if (( $# != 1 )); then
    echo "Usage: $0 /path/to/backup.img" >&2
    exit 2
fi
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
backup_image="$1"
state_dir="$project_root/runtime"
state_image="$state_dir/eduos-state.img"
if [[ ! -f "$backup_image" || -L "$backup_image" || ! -f "$backup_image.sha256" || -L "$backup_image.sha256" ]]; then
    echo 'Backup image or checksum is missing or unsafe.' >&2
    exit 1
fi
if (( $(stat -c %s -- "$backup_image.sha256") > 128 )); then
    echo 'Backup checksum file is too large.' >&2
    exit 1
fi
if [[ ! -d "$state_dir" || -e "$state_image" || -L "$state_image" ]]; then
    echo 'Restore requires runtime/ to exist and the current state image to be absent.' >&2
    exit 1
fi
exec 9>"$state_dir/.state-use.lock"
if ! flock -n 9; then
    echo 'EduOS VM is using the state image; shut it down first.' >&2
    exit 1
fi
expected="$(cat -- "$backup_image.sha256")"
if [[ ! "$expected" =~ ^[0-9a-f]{64}$ ]]; then
    echo 'Backup checksum file is invalid.' >&2
    exit 1
fi
actual="$(sha256sum -- "$backup_image")"
if [[ "${actual%% *}" != "$expected" ]]; then
    echo 'Backup checksum mismatch; restore refused.' >&2
    exit 1
fi
if [[ "$(blkid -p -s LABEL -o value -- "$backup_image")" != EDUOS_STATE ]]; then
    echo 'Backup is not an EDUOS_STATE filesystem image.' >&2
    exit 1
fi
umask 077
temporary="$(mktemp "$state_dir/.eduos-restore.XXXXXXXX")"
trap 'rm -f -- "$temporary"' EXIT
cp --reflink=auto --sparse=always -- "$backup_image" "$temporary"
sync -f "$temporary"
copied="$(sha256sum -- "$temporary")"
if [[ "${copied%% *}" != "$expected" ]]; then
    echo 'Restored copy checksum mismatch.' >&2
    exit 1
fi
ln -- "$temporary" "$state_image"
sync -f "$state_dir"
echo "State image restored: $state_image"
