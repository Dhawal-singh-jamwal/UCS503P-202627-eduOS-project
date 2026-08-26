# EduOS Development Journal - Week 1

**Project:** EduOS - Academic Computing Platform  
**Week:** Week 1  
**Period:** Starting 23 August 2026

## Overview

During the first week of the EduOS project, we focused on setting up a safe development environment for building and testing our Arch Linux-based operating system. Our main goals were to configure Archiso, set up QEMU/KVM for testing, build a bootable ISO, and create the initial project diagrams.

An important requirement was that EduOS development should not affect the existing Arch Linux installation. Therefore, all operating system testing was performed inside QEMU.

The development workflow established was:

```text
EduOS Repository
       ↓
Archiso Profile
       ↓
Build ISO
       ↓
QEMU/KVM
       ↓
Virtual Machine Testing
```

## System and Virtualization Verification

We first checked the system architecture:

```bash
uname -m
```

The result confirmed:

```text
x86_64
```

We then checked CPU virtualization support:

```bash
lscpu | grep -E 'Architecture|Virtualization'
```

The system confirmed:

```text
Architecture: x86_64
Virtualization: AMD-V
```

This confirmed that the system supports AMD-V hardware virtualization.

We also checked the availability of the required development tools:

```bash
for cmd in git python node npm podman qemu-system-x86_64 mkarchiso; do
    printf "%-20s" "$cmd"
    command -v "$cmd" || echo "NOT INSTALLED"
done
```

Archiso was verified using:

```bash
mkarchiso -h
```

QEMU was verified using:

```bash
qemu-system-x86_64 --version
```

UEFI firmware files were also checked:

```bash
ls /usr/share/edk2/x64/
```

## EduOS Repository Setup

A dedicated project directory was created:

```bash
mkdir -p ~/edu-os/os/archiso
cd ~/edu-os
```

A Git repository was initialized:

```bash
git init
git branch -m main
```

This created the initial project structure:

```text
edu-os/
└── os/
    └── archiso/
```

## Archiso Configuration

Instead of modifying the system Archiso configuration, we copied the official Archiso `releng` profile into our project repository:

```bash
cp -r /usr/share/archiso/configs/releng ~/edu-os/os/archiso/profile
```

This allowed us to create our own EduOS configuration while keeping the system Archiso installation unchanged.

The profile contains important files such as:

```text
packages.x86_64
pacman.conf
profiledef.sh
grub/
syslinux/
```

## Building the Initial ISO

Before making EduOS-specific modifications, we first tested whether our copied Archiso profile could successfully build a bootable ISO.

The output directory was created:

```bash
mkdir -p ~/edu-os/build/os
```

The ISO was built using:

```bash
sudo mkarchiso -v -o ~/edu-os/build/os/ ~/edu-os/os/archiso/profile
```

The generated ISO was checked using:

```bash
ls -lh ~/edu-os/build/os/
```

The final ISO was approximately 1.5 GB.

We verified that it was a valid bootable ISO:

```bash
file ~/edu-os/build/os/*.iso
```

The build successfully produced a bootable Arch Linux ISO.

Because the build was performed using `sudo`, ownership of the generated files was corrected:

```bash
sudo chown -R "$USER:$USER" ~/edu-os/build
```

## QEMU/KVM Testing

Before launching the ISO, we verified KVM availability:

```bash
ls -l /dev/kvm
```

The presence of `/dev/kvm` confirmed that KVM hardware acceleration was available.

The generated ISO was launched using QEMU:

```bash
qemu-system-x86_64 \
  -enable-kvm \
  -m 2048 \
  -cdrom ~/edu-os/build/os/archlinux-2026.08.19-x86_64.iso
```

The Arch Linux live environment successfully booted inside QEMU.

Inside the virtual machine, we tested the system using:

```bash
uname -a
lsblk
ip addr
```

The results confirmed that the virtual machine was running independently from the host system. The host's physical storage devices were not attached to the virtual machine.

This confirmed our safe development model:

```text
Arch Linux Host
       ↓
   QEMU/KVM
       ↓
    EduOS VM

Host disk not directly attached to VM
```

## Gantt Chart

A Gantt chart was created to plan the EduOS development timeline from 23 August until the beginning of December.

The project was divided into four major phases:

1. EduOS Base
2. Machine Management
3. EduLab Development
4. Integration and Finalization

Mermaid was used to create the Gantt chart programmatically.

## Use Case Diagram

A Use Case Diagram was created using Mermaid to identify the main actors and system functions.

The main actors are:

- Administrator
- Student
- EduOS Agent
- Evaluation Worker

The main system functions include:

- Managing machines
- Managing laboratory profiles
- Monitoring machine status
- Managing tasks
- Viewing laboratory tasks
- Submitting work
- Viewing results
- Synchronizing machine configurations
- Evaluating submissions
- Executing test cases
- Generating results

## Conclusion

During the first week, we successfully established the initial development environment for EduOS.

Completed work includes:

- Verified x86_64 architecture and AMD-V virtualization
- Verified Archiso and QEMU
- Created the EduOS Git repository
- Copied the Archiso profile into the project
- Built a bootable ISO
- Successfully tested the ISO in QEMU
- Verified virtual machine isolation
- Created the Gantt chart
- Created the Use Case Diagram

The next stage of the project will focus on customizing the Arch Linux image and gradually converting it into EduOS.
