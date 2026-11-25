#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
umask 077
mkdir -p -- "$project_root/runtime"
state_image="$project_root/runtime/eduos-state.img"
exec 9>"$project_root/runtime/.state-create.lock"
flock -x 9
if [[ -e "$state_image" || -L "$state_image" ]]; then
    echo "State image already exists; leaving it unchanged: $state_image"
    exit 0
fi
command -v mkfs.ext4 >/dev/null
# Format only a newly allocated temporary regular file, never an existing disk.
temporary="$(mktemp "$project_root/runtime/.eduos-state.XXXXXX")"
trap 'rm -f -- "$temporary"' EXIT
truncate -s 4G -- "$temporary"
mkfs.ext4 -q -F -L EDUOS_STATE "$temporary"
sync -f "$temporary"
# No-clobber publication also protects against a concurrent external creator.
ln -- "$temporary" "$state_image"
sync -f "$project_root/runtime"
echo "Created persistent state disk: $state_image"
