# OpenVMM Alpine Linux VM on Proxmox

## Prerequisites

- Proxmox host with KVM support
- `openvmm` binary at `/usr/local/bin/openvmm`
- MSVM UEFI firmware at `/zpool-nvme/openvmm-alpine/MSVM.fd` (see below)
- `tmux` installed (`apt-get install -y tmux`)

## Files

| File                                                           | Purpose                                 |
|----------------------------------------------------------------|-----------------------------------------|
| `/zpool-nvme/openvmm-alpine/alpine.img`                        | VM disk image (50GB sparse, ZFS subvol) |
| `/zpool-nvme/openvmm-alpine/MSVM.fd`                           | MSVM UEFI firmware (from openvmm build) |
| `/mnt/clusterstore/template/iso/alpine-virt-3.21.3-x86_64.iso` | Alpine Linux ISO                        |

## 1. Create disk image (once)

Create a ZFS subvolume and a sparse disk image:

```bash
zfs create zpool-nvme/openvmm-alpine
truncate -s 50G /zpool-nvme/openvmm-alpine/alpine.img
```

The image is sparse — it only consumes actual space on disk as data is written. Adjust the size as needed.


## 2. Obtaining the MSVM UEFI firmware
There is no standalone download for `MSVM.fd`. It is fetched as part of the OpenVMM build tooling, we copied it to /etc/openvmm earlier

```bash
cp /etc/openvmm/MSVM.fd /zpool-nvme/openvmm-alpine/MSVM.fd
```

## 3. Create Startscript

```bash
#!/bin/bash
openvmm \
  --hypervisor kvm \
  --memory 1GB \
  --processors 2 \
  --disk /zpool-nvme/openvmm-alpine/alpine.img \
  --disk /mnt/clusterstore/template/iso/alpine-virt-3.21.3-x86_64.iso,ro,dvd \
  --net tap:tap-openvmm \
  --com1 console \
  --uefi \
  --uefi-firmware /zpool-nvme/openvmm-alpine/MSVM.fd
echo "openvmm exited with code $?"
sleep 999
```


## 4. Start the VM

```bash
tmux new-session -d -s openvmm /zpool-nvme/openvmm-alpine/start-tap.sh
```

Or manually:

```bash
tmux new-session -s openvmm 'openvmm \
  --hypervisor kvm \
  --memory 1GB \
  --processors 2 \
  --disk /zpool-nvme/openvmm-alpine/alpine.img \
  --disk /mnt/clusterstore/template/iso/alpine-virt-3.21.3-x86_64.iso,ro,dvd \
  --net tap:tap-openvmm \
  --com1 console \
  --uefi \
  --uefi-firmware /zpool-nvme/openvmm-alpine/MSVM.fd'
```

## 5. Connect to the VM

### Serial console via tmux

Attach to the running session:

```bash
tmux attach -t openvmm
```

| Action                    | Keys                                                                   |
|---------------------------|------------------------------------------------------------------------|
| Detach (VM keeps running) | `Ctrl+B` then `D`                                                      |
| Scroll up                 | `Ctrl+B` then `[` (use arrow keys/PgUp, press `q` to exit scroll mode) |
| List sessions             | `tmux ls`                                                              |
| Kill session (stops VM)   | `tmux kill-session -t openvmm`                                         |

### VNC (optional, for graphical output)

Add `--vnc --vnc-port 5901` to the openvmm command to enable VNC. Note: with MSVM firmware on KVM, VNC currently shows a black screen. It is documented here for completeness in case future firmware versions fix this.

**From a remote workstation**, first create an SSH tunnel (VNC listens on localhost only):

```bash
ssh -L 5901:127.0.0.1:5901 root@<PROXMOX_HOST_IP>
```

Then connect a VNC client to `localhost:5901`:

- **Windows**: TightVNC, RealVNC, or TigerVNC viewer → `localhost:5901`
- **macOS**: `open vnc://localhost:5901` or use any VNC app
- **Linux**: `vncviewer localhost:5901` or Remmina → New → VNC → `localhost:5901`

To disconnect VNC, simply close the VNC client window. The VM continues running.

## 6. First-time guest setup

After attaching to the console, run these commands inside the VM:

### Networking

```sh
ip link set eth0 up
udhcpc -i eth0
```

### Set root password

```sh
passwd root
```

### Enable SSH

```sh
apk add openssh
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
service sshd start    # use 'service sshd restart' if already running
```

### Verify IP address

```sh
ip addr show eth0
```

## 7. SSH from another machine

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no root@<VM_IP>
```

The `-o PubkeyAuthentication=no` flag prevents "Too many authentication failures" when the client has many SSH keys loaded.

Once you have copied your public key to the VM (`ssh-copy-id`), you can SSH normally.

## 8. Install Alpine to disk (optional)

To make the installation persistent (survives reboots without the ISO):

```sh
setup-alpine
```

Follow the prompts. When asked about the disk, select `sda`. After installation, you can remove the `--disk ...iso,ro,dvd` line from the start command.

## 9. Stop the VM

From the guest:

```sh
poweroff
```

Or from the host:

```bash
pkill -f openvmm
tmux kill-session -t openvmm
```

## Notes

- **Serial console only**: VNC shows a black screen with MSVM firmware on KVM. Use `--com1 console` inside tmux.
- **tmux is required**: openvmm needs an interactive TTY for `--com1 console`. It panics without one.
- **Direct kernel boot does not work**: `--kernel`/`--initrd`/`--cmdline` with MSVM firmware produces a black screen on KVM.
- **PCAT boot does not work**: `--pcat` expects WSL paths, not native Linux.
- **Networking**: `consomme` = NAT (no inbound), `tap:<name>` = bridged (full LAN access).
- **Disk image is sparse**: 50GB virtual size but only uses actual space on ZFS as data is written.
