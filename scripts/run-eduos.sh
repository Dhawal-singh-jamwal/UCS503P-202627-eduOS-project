#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
state_image="$project_root/runtime/eduos-state.img"
if [[ ! -f "$state_image" || -L "$state_image" ]]; then
    echo "Create the state disk first: $project_root/scripts/create-eduos-state-disk.sh" >&2
    exit 1
fi
if (( $# > 1 )); then
    echo "Usage: $0 [path/to/eduos.iso]" >&2
    exit 2
fi
iso="${1:-}"
if [[ -z "$iso" ]]; then
    shopt -s nullglob
    for candidate in "$project_root"/out-admin/*.iso "$project_root"/out/*.iso; do
        if [[ -z "$iso" || "$candidate" -nt "$iso" ]]; then iso="$candidate"; fi
    done
fi
if [[ -z "$iso" || ! -f "$iso" ]]; then
    echo 'No ISO found. Supply an ISO path or build into out-admin/.' >&2
    exit 1
fi
printf 'Booting EduOS ISO: %s\n' "$iso" >&2
# QEMU also locks the image; never use -snapshot for this state drive.
exec qemu-system-x86_64 -enable-kvm -m 4096 -smp 4 -boot order=d \
    -cdrom "$iso" -drive "file=$state_image,format=raw,if=virtio"
