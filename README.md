# OpenVMM on Proxmox

Run VMs with Microsoft's Hyper-V compatible device model on Proxmox/KVM using [OpenVMM](https://github.com/microsoft/openvmm).

OpenVMM is a Type-2 VMM written in Rust by Microsoft. On Linux it runs on top of KVM (`/dev/kvm`), using Hyper-V devices (VMBus, storvsp, netvsp) instead of QEMU's virtio devices.

> **Note**: Microsoft warns that OpenVMM on Linux is not yet ready for end-user workloads. 
> Expect rapid changes -- always keep a backup of your openvmm executable before updating it.

## What's Included

| File                       | Description                                                    |
|----------------------------|----------------------------------------------------------------|
| `openvmm`                  | Patched openvmm binary (required for noVNC support)            |
| `MSVM.fd`                  | UEFI firmware for openvmm                                      |
| `ovm`                      | CLI tool for managing openvmm VMs                              |
| `ovm-web/app.py`           | Web UI (FastAPI) on port 8008                                  |
| `systemd/openvmm@.service` | Systemd template for openvmm VMs                               |
| `systemd/ovm-web.service`  | Systemd service for the web UI                                 |
| `openvmm-guard.sh`         | Hookscript to prevent accidental QEMU start                    |
| `example-vm.conf`          | Documented example Proxmox VM config with all openvmm flags    |
| `alpine.md`                | Guide for running Alpine Linux under openvmm                   |

## Quick Start

### 1. Install on Proxmox host

This repo includes a patched openvmm binary with an improved VNC server (RFB 3.8, tile-based dirty detection, zlib compression, cursor support). This is **required** for noVNC and the Proxmox web console to work. The stock upstream openvmm only supports RFB 3.3 which is incompatible with noVNC. The patch has been submitted as [microsoft/openvmm#3197](https://github.com/microsoft/openvmm/pull/3197) -- until it is merged upstream, use the binary from this repo.

```bash
# Install patched openvmm binary from this repo
cp openvmm /usr/local/bin/openvmm
chmod +x /usr/local/bin/openvmm

# Copy MSVM UEFI firmware
mkdir -p /etc/openvmm
cp MSVM.fd /etc/openvmm/MSVM.fd

# Install ovm tool (from this repo)
cp ovm /usr/local/bin/ovm
chmod +x /usr/local/bin/ovm

# Install systemd services
cp systemd/openvmm@.service /etc/systemd/system/
cp systemd/ovm-web.service /etc/systemd/system/
systemctl daemon-reload

# Install hookscript
cp openvmm-guard.sh /var/lib/vz/snippets/
chmod +x /var/lib/vz/snippets/openvmm-guard.sh

# Install web UI
mkdir -p /opt/ovm-web
cp ovm-web/app.py /opt/ovm-web/
python3 -m venv /opt/ovm-web/venv
/opt/ovm-web/venv/bin/pip install fastapi uvicorn

# Enable and start web UI
systemctl enable --now ovm-web

# Create runtime directories
mkdir -p /var/run/openvmm /var/log/openvmm
```

### 2. Create a VM

Copy `example-vm.conf` to `/etc/pve/qemu-server/<VMID>.conf` and adjust:
- `cores`, `sockets`, `memory` -- as needed
- `net0` -- set bridge name
- `args:` -- set disk paths, VNC port, and openvmm flags

See `example-vm.conf` for all available flags with documentation.

### 3. Prepare disk image

openvmm cannot use ZFS zvols directly (16KB sector size causes SCSI errors). Convert to a file-based raw image:

```bash
# Create storage directory
mkdir -p /zpool-nvme/images-openvmm/<VMID>

# Convert from existing QEMU VM
qemu-img convert -p -f raw -O raw /dev/zvol/zpool-nvme/vm-XXXXX-disk-N \
    /zpool-nvme/images-openvmm/<VMID>/vm-<VMID>-disk-0.raw

# Or create blank image for fresh install
truncate -s 500G /zpool-nvme/images-openvmm/<VMID>/vm-<VMID>-disk-0.raw
```

### 4. Converting a Windows VM from QEMU

Before converting, boot the Windows VM in QEMU and install Hyper-V drivers:

```powershell
Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All
```

Verify boot-start drivers (must be `Start = 0`):

```powershell
Get-ItemProperty "HKLM:\SYSTEM\ControlSet001\Services\storvsc" | Select Start
Get-ItemProperty "HKLM:\SYSTEM\ControlSet001\Services\vmbus" | Select Start
```

After first boot under openvmm, fix UDP/DNS (openvmm netvsp checksum bug):

```powershell
Set-NetAdapterAdvancedProperty -Name "Ethernet" -RegistryKeyword "*UDPChecksumOffloadIPv4" -RegistryValue 0
Set-NetAdapterAdvancedProperty -Name "Ethernet" -RegistryKeyword "*UDPChecksumOffloadIPv6" -RegistryValue 0
```

The `--gfx` flag is **required** for Windows to boot under openvmm.

### 5. Register and start

```bash
# Register VM (sets hookscript to prevent accidental QEMU start)
ovm register <VMID>

# Start
ovm start <VMID>

# Check status
ovm status

# Stop
ovm stop <VMID>
```

### 6. Connect via VNC

`ovm start` automatically sets up a socat port forwarder so VNC is accessible directly (no SSH tunnel needed). The external VNC port is the internal port + 10000 (e.g. VNC 5999 -> external 15999).

Connect your VNC client (TigerVNC, RealVNC, etc.) to:
```
<PROXMOX_IP>:<VNC_PORT + 10000>
```

If direct access doesn't work, use an SSH tunnel as fallback:
```bash
ssh -4 -N -L <VNC_PORT>:127.0.0.1:<VNC_PORT> root@<PROXMOX_IP>
```
Then connect to `localhost:<VNC_PORT>`.

The web UI at `https://<PROXMOX_IP>:8008` provides a dashboard with Start/Stop buttons and shows the VNC connection info for each VM.

**Note**: The patched openvmm binary included in this repo is **required** for noVNC to work. The stock upstream openvmm VNC server (RFB 3.3, no tiling, no compression) is incompatible with noVNC and very slow. The patched version adds RFB 3.8, tile-based dirty detection, zlib compression, and cursor support. See [microsoft/openvmm#3197](https://github.com/microsoft/openvmm/pull/3197) for the upstream PR.

## ovm Commands

```
ovm start <vmid>       Start an openvmm VM
ovm stop <vmid>        Stop an openvmm VM
ovm status [vmid]      Show VM status (all if no vmid)
ovm config <vmid>      Show generated openvmm command (dry run)
ovm register <vmid>    Register VM (set guard hookscript)
ovm unregister <vmid>  Unregister VM (remove hookscript)
ovm vnc <vmid>         Show VNC connection info
ovm console <vmid>     Attach to serial console log
```

## Architecture

```
Browser ---HTTPS---> ovm-web (port 8008)    VM dashboard, start/stop
VNC client ---------> socat (port+10000) -> openvmm VNC (localhost)

ovm start ---> systemctl start openvmm@VMID
               |-- ExecStartPre: create tap device
               |-- ExecStart: run openvmm (via script for PTY)
               +-- ExecStopPost: remove tap + VNC forwarder

VNC forwarding: systemd-run socat on port+10000 -> localhost:VNC_PORT
```

Config translation:
- `memory`, `cores` x `sockets`, `net0` -- auto-translated from Proxmox config
- Everything else (disks, firmware, performance flags) -- specified in `args:` field
- Auto-translated values can be overridden by args

## Known Limitations

- **Proxmox UI**: VMs show as "stopped" (can't fix without patching Proxmox)
- **noVNC**: Requires the patched openvmm binary from this repo ([PR #3197](https://github.com/microsoft/openvmm/pull/3197)). Stock upstream openvmm only works with native VNC clients (RealVNC, TigerVNC) and is very slow.
- **MAC address**: openvmm always assigns a Hyper-V MAC (00:15:5d:xx:xx:xx). Use static IP or DHCP reservation.
- **ZFS zvols**: Must convert to file-based raw images (16KB sector issue)
- **UDP checksums**: Windows guests need UDP checksum offload disabled (openvmm netvsp bug)
- **Backup**: vzdump doesn't work (no QMP). Use ZFS snapshots.
- **HA**: Not supported. Remove openvmm VMs from HA groups.
- **CPU performance**: Comparable to QEMU `cpu=host`, slower than QEMU with Hyper-V enlightenment flags. might depend on host CPU type, tested on ancient Xenon E5-2697-v2
- **VNC fragility**: openvmm's VNC server can only handle one client and may stop responding after disconnect.

## Performance Tuning

Flags that help (add to `args:` in VM config):
- `--private-memory --thp` -- transparent huge pages
- `--prefetch` -- pre-fault guest RAM
- `--scsi-sub-channels 4` -- parallel SCSI I/O
- Multi-process mode (don't add `--single-process`)

Flags tested but made performance worse:
- `--x2apic on`
- `--smt force`

## VM Guides

- [Alpine Linux VM](alpine.md)

## Building from Source

If you want to build openvmm yourself instead of using the included binary:

```bash
# In a container (to avoid polluting the host)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env
apt install -y git build-essential protobuf-compiler pkg-config libssl-dev clang

# Use the patched fork (required for noVNC until PR #3197 is merged upstream)
git clone https://github.com/bitranox/openvmm.git
cd openvmm
git checkout vnc-novnc-compat
cargo xflowey restore-packages
cargo build --release -p openvmm
strip target/release/openvmm

# Once PR #3197 is merged, you can build from upstream instead:
# git clone https://github.com/microsoft/openvmm.git
# cd openvmm
# cargo xflowey restore-packages
# cargo build --release -p openvmm
# strip target/release/openvmm
```

The binary will be at `target/release/openvmm`. Copy it to `/usr/local/bin/openvmm` on the Proxmox host.

The UEFI firmware is at `.packages/hyperv.uefi.mscoreuefi.x64.RELEASE/MsvmX64/RELEASE_VS2022/FV/MSVM.fd` -- copy it to `/etc/openvmm/MSVM.fd`.

## Resources

- https://github.com/microsoft/openvmm
- https://openvmm.dev/guide/
- https://openvmm.dev/guide/user_guide/openvmm.html
