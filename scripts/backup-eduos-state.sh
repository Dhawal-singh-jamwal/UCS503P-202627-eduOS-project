#!/usr/bin/env bash
# Run on the host after shutting down the EduOS VM.
set -euo pipefail
if (( $# != 1 )); then
    echo "Usage: $0 /path/to/backup.img" >&2
    exit 2
fi
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source_image="$project_root/runtime/eduos-state.img"
backup_image="$1"
backup_dir="$(dirname -- "$backup_image")"
if [[ ! -f "$source_image" || -L "$source_image" || ! -d "$backup_dir" ]]; then
    echo 'State image or backup directory is unavailable.' >&2
    exit 1
fi
if [[ "$(blkid -p -s LABEL -o value -- "$source_image")" != EDUOS_STATE ]]; then
    echo 'Source is not an EDUOS_STATE filesystem image.' >&2
    exit 1
fi
if [[ -e "$backup_image" || -L "$backup_image" || -e "$backup_image.sha256" || -L "$backup_image.sha256" ]]; then
    echo 'Backup destination already exists.' >&2
    exit 1
fi
exec 9>"$project_root/runtime/.state-use.lock"
if ! flock -n 9; then
    echo 'EduOS VM is using the state image; shut it down first.' >&2
    exit 1
fi
if command -v fuser >/dev/null && fuser -s -- "$source_image"; then
    echo 'A process has the state image open; shut it down first.' >&2
    exit 1
fi
umask 077
temporary="$(mktemp "$backup_dir/.eduos-backup.XXXXXXXX")"
trap 'rm -f -- "$temporary" "$temporary.sha256"' EXIT
cp --reflink=auto --sparse=always -- "$source_image" "$temporary"
sync -f "$temporary"
digest="$(sha256sum -- "$temporary")"
printf '%s\n' "${digest%% *}" > "$temporary.sha256"
sync -f "$temporary.sha256"
ln -- "$temporary" "$backup_image"
if ! ln -- "$temporary.sha256" "$backup_image.sha256"; then
    rm -f -- "$backup_image"
    exit 1
fi
sync -f "$backup_dir"
echo "Backup created: $backup_image"
