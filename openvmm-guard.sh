#!/bin/bash
# Guard hookscript: prevents accidental QEMU start for openvmm-managed VMs
VMID="$1"
PHASE="$2"

if [ "$PHASE" = "pre-start" ]; then
    echo "ERROR: VM $VMID is managed by openvmm. Use 'ovm start $VMID' instead." >&2
    exit 1
fi
