#!/usr/bin/env python3
"""OpenVMM Web UI - VM management for openvmm VMs on Proxmox."""

import asyncio
import re
import socket
import subprocess
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="OpenVMM Web UI")

NOVNC_DIR = Path("/opt/ovm-web/novnc")
app.mount("/novnc-static", StaticFiles(directory=str(NOVNC_DIR)), name="novnc-static")

PVE_CONF_DIR = "/etc/pve/qemu-server"
OVM_BIN = "/usr/local/bin/ovm"


def parse_conf(vmid: str) -> dict:
    conf_path = Path(PVE_CONF_DIR) / f"{vmid}.conf"
    if not conf_path.exists():
        return {}
    result = {}
    for line in conf_path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def is_openvmm_vm(vmid: str, conf: dict) -> bool:
    args = conf.get("args", "")
    hookscript = conf.get("hookscript", "")
    return "openvmm-guard" in hookscript or "--uefi-firmware" in args or "--hypervisor" in args


def get_vnc_port(conf: dict) -> int | None:
    args = conf.get("args", "")
    match = re.search(r"--vnc-port\s+(\d+)", args)
    return int(match.group(1)) if match else None


def is_running(vmid: str) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", f"openvmm@{vmid}"],
        capture_output=True, text=True
    )
    return result.stdout.strip() == "active"


def get_all_vms() -> list[dict]:
    vms = []
    conf_dir = Path(PVE_CONF_DIR)
    for conf_file in sorted(conf_dir.glob("*.conf")):
        vmid = conf_file.stem
        conf = parse_conf(vmid)
        if not is_openvmm_vm(vmid, conf):
            continue
        vms.append({
            "vmid": vmid,
            "name": conf.get("name", "unnamed"),
            "memory": conf.get("memory", "?"),
            "cores": conf.get("cores", "?"),
            "sockets": conf.get("sockets", "1"),
            "ostype": conf.get("ostype", "?"),
            "status": "running" if is_running(vmid) else "stopped",
            "vnc_port": get_vnc_port(conf),
        })
    return vms


def get_host_ip() -> str:
    result = subprocess.run(["hostname", "-I"], capture_output=True, text=True)
    return result.stdout.split()[0] if result.stdout.strip() else "HOST_IP"


@app.get("/api/vms")
async def list_vms():
    return get_all_vms()


@app.post("/api/vms/{vmid}/start")
async def start_vm(vmid: str):
    if is_running(vmid):
        return {"status": "already running"}
    result = subprocess.run([OVM_BIN, "start", vmid], capture_output=True, text=True, timeout=30)
    return {"status": "started" if result.returncode == 0 else "error", "output": result.stdout + result.stderr}


@app.post("/api/vms/{vmid}/stop")
async def stop_vm(vmid: str):
    if not is_running(vmid):
        return {"status": "already stopped"}
    result = subprocess.run([OVM_BIN, "stop", vmid], capture_output=True, text=True, timeout=30)
    return {"status": "stopped" if result.returncode == 0 else "error", "output": result.stdout + result.stderr}


@app.websocket("/ws/vnc/{vmid}")
async def vnc_websocket_proxy(ws: WebSocket, vmid: str):
    """Proxy WebSocket traffic to the VM's VNC server."""
    import logging
    log = logging.getLogger("vnc-proxy")
    conf = parse_conf(vmid)
    vnc_port = get_vnc_port(conf)
    if vnc_port is None:
        await ws.close(code=1008, reason="No VNC port configured")
        return
    await ws.accept(subprotocol="binary")
    log.warning("WS accepted for vmid=%s vnc_port=%s", vmid, vnc_port)
    reader, writer = await asyncio.open_connection("127.0.0.1", vnc_port)
    log.warning("TCP connected to VNC")

    async def ws_to_vnc():
        try:
            while True:
                data = await ws.receive_bytes()
                writer.write(data)
                await writer.drain()
        except WebSocketDisconnect:
            log.warning("WS client disconnected")
        except Exception as e:
            log.warning("ws_to_vnc error: %s", e)
        finally:
            writer.close()

    async def vnc_to_ws():
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    log.warning("VNC server closed connection")
                    break
                await ws.send_bytes(data)
        except Exception as e:
            log.warning("vnc_to_ws error: %s", e)

    done, pending = await asyncio.wait(
        [asyncio.create_task(ws_to_vnc()), asyncio.create_task(vnc_to_ws())],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    writer.close()
    try:
        await ws.close()
    except Exception:
        pass


@app.get("/novnc/{vmid}", response_class=HTMLResponse)
async def novnc_console(vmid: str):
    conf = parse_conf(vmid)
    vnc_port = get_vnc_port(conf)
    vm_name = conf.get("name", vmid)
    if vnc_port is None:
        return HTMLResponse("<h2>No VNC port configured for this VM</h2>", status_code=404)
    if not is_running(vmid):
        return HTMLResponse("<h2>VM is not running</h2>", status_code=400)
    host_ip = get_host_ip()
    return NOVNC_HTML.replace("{{HOST_IP}}", host_ip).replace("{{VMID}}", vmid).replace("{{VM_NAME}}", f"{vm_name} ({vmid})")


@app.get("/", response_class=HTMLResponse)
async def index():
    host_ip = get_host_ip()
    return PAGE_HTML.replace("{{HOST_IP}}", host_ip)


PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>OpenVMM Manager</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1a1a2e; color: #e0e0e0; }
        .header { background: #16213e; padding: 20px 30px; border-bottom: 2px solid #0f3460; }
        .header h1 { font-size: 22px; color: #e94560; }
        .header p { color: #888; font-size: 13px; margin-top: 4px; }
        .container { max-width: 1200px; margin: 30px auto; padding: 0 20px; }
        table { width: 100%; border-collapse: collapse; background: #16213e; border-radius: 8px; overflow: hidden; }
        th { background: #0f3460; padding: 12px 16px; text-align: left; font-size: 13px; text-transform: uppercase; color: #888; }
        td { padding: 12px 16px; border-bottom: 1px solid #1a1a2e; }
        tr:hover td { background: #1a2340; }
        .status { padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; display: inline-block; }
        .status-running { background: #1b4332; color: #52b788; }
        .status-stopped { background: #3d0000; color: #e94560; }
        .btn { padding: 6px 16px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; font-weight: 500; }
        .btn-start { background: #52b788; color: #000; }
        .btn-stop { background: #e94560; color: #fff; }
        .btn-vnc { background: #0f3460; color: #e0e0e0; border: 1px solid #333; }
        .btn:hover { opacity: 0.85; }
        .btn:disabled { opacity: 0.4; cursor: not-allowed; }
        .actions { display: flex; gap: 8px; }
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 100; align-items: center; justify-content: center; }
        .modal-overlay.active { display: flex; }
        .modal { background: #16213e; border: 1px solid #0f3460; border-radius: 8px; padding: 24px; max-width: 600px; width: 90%; }
        .modal h3 { color: #e94560; margin-bottom: 16px; }
        .modal pre { background: #0a0a1a; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 13px; color: #52b788; margin: 8px 0; user-select: all; }
        .modal p { color: #aaa; font-size: 13px; margin: 8px 0; }
        .modal .close { float: right; background: none; border: none; color: #888; font-size: 20px; cursor: pointer; }
        .modal .close:hover { color: #e94560; }
        .modal ol { color: #aaa; font-size: 13px; padding-left: 20px; }
        .modal li { margin: 6px 0; }
    </style>
</head>
<body>
    <div class="header">
        <h1>OpenVMM Manager</h1>
        <p>openvmm virtual machines on Proxmox</p>
    </div>
    <div class="container">
        <div id="vm-list"><div style="color:#888;text-align:center;padding:40px;">Loading...</div></div>
    </div>

    <div class="modal-overlay" id="vnc-modal">
        <div class="modal">
            <button class="close" onclick="closeModal()">&times;</button>
            <h3>VNC Client Connection</h3>
            <p>Connect your VNC client (TigerVNC, RealVNC, etc.) directly to:</p>
            <pre id="vnc-addr"></pre>
            <p style="color:#666; font-size:11px; margin-top:12px;">If direct VNC fails, use SSH tunnel instead:</p>
            <pre id="ssh-cmd" style="font-size:11px; color:#888;"></pre>
        </div>
    </div>

    <script>
        const HOST_IP = '{{HOST_IP}}';

        async function loadVMs() {
            const res = await fetch('/api/vms');
            const vms = await res.json();
            const el = document.getElementById('vm-list');
            if (!vms.length) { el.innerHTML = '<p style="color:#888;text-align:center;padding:40px;">No openvmm VMs found</p>'; return; }
            let html = '<table><tr><th>VMID</th><th>Name</th><th>OS</th><th>CPU</th><th>Memory</th><th>Status</th><th>Actions</th></tr>';
            for (const vm of vms) {
                const running = vm.status === 'running';
                const cores = (parseInt(vm.cores)||1) * (parseInt(vm.sockets)||1);
                const memGB = ((parseInt(vm.memory)||0) / 1024).toFixed(1);
                html += '<tr><td>'+vm.vmid+'</td><td>'+vm.name+'</td><td>'+vm.ostype+'</td><td>'+cores+' vCPU</td><td>'+memGB+' GB</td>';
                html += '<td><span class="status '+(running?'status-running':'status-stopped')+'">'+vm.status+'</span></td>';
                html += '<td class="actions">';
                if (running) {
                    html += '<button class="btn btn-vnc" onclick="openNoVNC(&quot;'+vm.vmid+'&quot;)">noVNC</button> ';
                    html += '<button class="btn btn-vnc" onclick="showVNC('+vm.vnc_port+')">VNC Info</button> ';
                    html += '<button class="btn btn-stop" onclick="doAction(&quot;'+vm.vmid+'&quot;,&quot;stop&quot;)">Stop</button>';
                } else {
                    html += '<button class="btn btn-start" onclick="doAction(&quot;'+vm.vmid+'&quot;,&quot;start&quot;)">Start</button>';
                }
                html += '</td></tr>';
            }
            el.innerHTML = html + '</table>';
        }

        async function doAction(vmid, action) {
            event.target.disabled = true;
            event.target.textContent = action === 'start' ? 'Starting...' : 'Stopping...';
            await fetch('/api/vms/'+vmid+'/'+action, {method:'POST'});
            setTimeout(loadVMs, 3000);
        }

        function openNoVNC(vmid) {
            window.open('/novnc/'+vmid, 'novnc_'+vmid, 'width=1280,height=800,menubar=no,toolbar=no,location=no');
        }

        function showVNC(port) {
            var extPort = port + 10000;
            document.getElementById('vnc-addr').textContent = HOST_IP + ':' + extPort;
            document.getElementById('ssh-cmd').textContent = 'ssh -4 -N -L '+port+':127.0.0.1:'+port+' root@'+HOST_IP + '  then connect to localhost:'+port;
            document.getElementById('vnc-modal').classList.add('active');
        }

        function closeModal() {
            document.getElementById('vnc-modal').classList.remove('active');
        }

        document.getElementById('vnc-modal').addEventListener('click', function(e) {
            if (e.target === this) closeModal();
        });

        loadVMs();
        setInterval(loadVMs, 10000);
    </script>
</body>
</html>"""


NOVNC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>{{VM_NAME}} - noVNC Console</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html, body { height: 100%; background: #1a1a2e; overflow: hidden; }
        #top_bar {
            background: #16213e; color: #e0e0e0; font: 600 13px -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            padding: 8px 16px; display: flex; align-items: center; justify-content: space-between;
            border-bottom: 2px solid #0f3460; height: 40px;
        }
        #top_bar .title { color: #e94560; }
        #status { color: #888; font-weight: 400; }
        .bar-btn {
            background: #0f3460; color: #e0e0e0; border: 1px solid #333; padding: 4px 12px;
            border-radius: 4px; cursor: pointer; font-size: 12px; margin-left: 8px;
        }
        .bar-btn:hover { opacity: 0.85; }
        #screen { height: calc(100vh - 40px); }
    </style>
</head>
<body>
    <div id="top_bar">
        <span><span class="title">{{VM_NAME}}</span> &mdash; <span id="status">Connecting...</span></span>
        <span>
            <button class="bar-btn" id="sendCAD">Ctrl+Alt+Del</button>
            <button class="bar-btn" id="fullscreen">Fullscreen</button>
        </span>
    </div>
    <div id="screen"></div>
    <script type="module">
        import RFB from '/novnc-static/core/rfb.js';

        const wsProto = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const url = wsProto + '://' + window.location.host + '/ws/vnc/{{VMID}}';
        console.log('noVNC connecting to:', url);

        const rfb = new RFB(document.getElementById('screen'), url,
            { wsProtocols: ['binary'] });

        rfb.scaleViewport = true;
        rfb.resizeSession = false;

        rfb.addEventListener("connect", () => {
            console.log('noVNC connected');
            document.getElementById('status').textContent = 'Connected';
        });
        rfb.addEventListener("disconnect", (e) => {
            console.log('noVNC disconnected, clean:', e.detail.clean);
            document.getElementById('status').textContent = e.detail.clean ? 'Disconnected' : 'Connection lost';
        });
        rfb.addEventListener("desktopname", (e) => {
            document.title = e.detail.name + ' - noVNC';
        });

        document.getElementById('sendCAD').onclick = () => rfb.sendCtrlAltDel();
        document.getElementById('fullscreen').onclick = () => {
            if (document.fullscreenElement) document.exitFullscreen();
            else document.documentElement.requestFullscreen();
        };
    </script>
</body>
</html>"""
