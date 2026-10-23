#!/usr/bin/env python3
"""Build the PBL3 RIPv2 + DHCP lab in GNS3 using FRRouting (Docker).

The four routers are FRR containers instead of Cisco IOSv VMs, so no licensed
IOS image is required. Addressing, topology and RIPv2 behaviour are identical
to the report.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pexpect

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from common import (  # noqa: E402
    BASE,
    API_VERSION,
    ROOT,
    api,
    log_path,
    save_state,
)

PROJECT_NAME = os.environ.get("GNS3_PROJECT_NAME", "PBL3-RIPv2-DHCP-FRR")
COMPUTE_ID = os.environ.get("GNS3_COMPUTE_ID", "local")
ROUTER_BOOT_WAIT = int(os.environ.get("ROUTER_BOOT_WAIT", "10"))
RIP_WAIT = int(os.environ.get("RIP_WAIT", "15"))
FRR_IMAGE = os.environ.get("PBL3_FRR_IMAGE", "frrouting/frr:latest")
DHCP_IMAGE = os.environ.get("PBL3_DHCP_IMAGE", "alpine:3.20")

ROUTER_CONFIGS = {
    name: ROOT / "configs-frr" / f"{name}.conf" for name in ("R0", "R1", "R2", "R3")
}

# GNS3's built-in Ethernet switch is implemented by Dynamips, and Docker nodes
# need uBridge plus a static busybox. Turn those errors into actionable hints.
START_HINTS = (
    ("no path to a vpcs executable", "Cài VPCS (sudo apt install vpcs) hoặc build VPCS >= 0.6.1 và thêm vào PATH."),
    ("vpcs executable version", "VPCS quá cũ. Debian 13 chỉ có 0.5b2; build VPCS 0.8.5 từ github.com/GNS3/vpcs rồi thay binary (xem README)."),
    ("could not find dynamips", "Cài Dynamips (sudo apt install dynamips) hoặc build github.com/GNS3/dynamips rồi thêm vào PATH."),
    ("busybox", "Cài busybox-static (sudo apt install busybox-static). GNS3 Docker cần busybox tĩnh."),
    ("ubridge requires root access or the capability", "Cấp quyền cho ubridge: sudo setcap cap_net_admin,cap_net_raw+ep $(command -v ubridge)"),
    ("ubridge", "Cài ubridge (github.com/GNS3/ubridge) và thêm vào PATH."),
    ("could not pull", "Kiểm tra Docker daemon/kết nối mạng rồi pull image trước: docker pull frrouting/frr:latest"),
    ("frr", "Pull FRR image: docker pull frrouting/frr:latest"),
    ("docker", "Kiểm tra Docker daemon đang chạy và tài khoản chạy GNS3 có quyền dùng Docker."),
)


def explain_start_error(exc):
    text = str(exc).lower()
    for needle, hint in START_HINTS:
        if needle in text:
            return hint
    return "Mở GNS3 GUI, kiểm tra image/template và thử start node thủ công để xem lỗi đầy đủ."


def choose_template(templates, predicate):
    matches = [t for t in templates if predicate(t)]
    return matches[0] if matches else None


def describe_templates(templates):
    return ", ".join(
        f"{t.get('name')} ({t.get('template_type')})" for t in templates
    ) or "(không có template nào)"


def safe_node_name(name, template):
    # Docker container names only allow [a-zA-Z0-9][a-zA-Z0-9_.-].
    if template.get("template_type") == "docker":
        return name.replace("_", "-")
    return name


def create_node(project_id, template_id, name, x, y):
    node = api(
        "POST",
        f"/projects/{project_id}/templates/{template_id}",
        json={"name": name, "x": x, "y": y, "compute_id": COMPUTE_ID},
    )
    # GNS3 3.x ignores "name" in the template usage payload.
    if node.get("name") != name:
        node = api(
            "PUT",
            f"/projects/{project_id}/nodes/{node['node_id']}",
            json={"name": name},
        )
    if not node.get("ports"):
        node = api("GET", f"/projects/{project_id}/nodes/{node['node_id']}")
    return node


def frr_startup_script(config_text=""):
    script = [
        "set -e",
        "echo 1 > /proc/sys/net/ipv4/ip_forward",
        "sed -i 's/^zebra=no/zebra=yes/' /etc/frr/daemons",
        "sed -i 's/^ripd=no/ripd=yes/' /etc/frr/daemons",
        "sed -i 's/^staticd=no/staticd=yes/' /etc/frr/daemons",
        "grep -q '^service integrated-vtysh-config' /etc/frr/vtysh.conf 2>/dev/null || "
        "echo 'service integrated-vtysh-config' >> /etc/frr/vtysh.conf",
    ]
    if config_text.strip():
        script.append("cat > /etc/frr/frr.conf <<'FRR_EOF'")
        script.append(config_text.strip())
        script.append("FRR_EOF")
        script.append(
            "chown frr:frr /etc/frr/frr.conf /etc/frr/vtysh.conf 2>/dev/null || true"
        )
    script.append("/usr/lib/frr/frrinit.sh start")
    script.append("exec tail -f /dev/null")
    # shlex.join() makes a string that GNS3 can feed back through shlex.split().
    return shlex.join(["/bin/bash", "-c", "\n".join(script) + "\n"])


def ensure_frr_template(templates):
    desired = {
        "name": "PBL3 FRR Router",
        "template_type": "docker",
        "compute_id": COMPUTE_ID,
        "image": FRR_IMAGE,
        "adapters": 4,
        "category": "router",
        # Routers are configured per node through start_command; no console is
        # needed (FRR image has no telnetd).
        "console_type": "none",
        "console_auto_start": False,
        "start_command": frr_startup_script(),
    }

    existing = choose_template(
        templates,
        lambda t: t.get("template_type") == "docker"
        and t.get("name") == desired["name"],
    )
    if existing:
        template_id = existing["template_id"]
        api(
            "PUT",
            f"/templates/{template_id}",
            json={k: v for k, v in desired.items() if k != "template_type"},
        )
        return api("GET", f"/templates/{template_id}"), False

    return api("POST", "/templates", json=desired), True


def ensure_dhcp_template(templates):
    desired = {
        "name": "PBL3 DHCP Alpine",
        "template_type": "docker",
        "compute_id": COMPUTE_ID,
        "image": DHCP_IMAGE,
        "adapters": 1,
        "category": "guest",
        # The script never opens the DHCP console; "none" also avoids needing
        # telnetd inside the container.
        "console_type": "none",
        "console_auto_start": False,
        # Alpine's busybox does not ship udhcpd, but GNS3 always injects the
        # host's static busybox at /gns3/bin/busybox. Use that, so no apk/internet
        # access is required inside the lab network.
        "start_command": (
            "/bin/sh -c '"
            "ip link set eth0 up; "
            "ip addr flush dev eth0; "
            "ip addr add 192.168.10.2/24 dev eth0; "
            'printf "start 192.168.10.10\\nend 192.168.10.100\\n'
            "interface eth0\\nopt router 192.168.10.1\\nopt dns 192.168.10.1\\n"
            'opt subnet 255.255.255.0\\nlease_file /tmp/udhcpd.leases\\n" '
            "> /etc/udhcpd.conf; "
            "exec /gns3/bin/busybox udhcpd -f /etc/udhcpd.conf'"
        ),
    }

    existing = choose_template(
        templates,
        lambda t: t.get("template_type") == "docker"
        and t.get("name") == desired["name"],
    )
    if existing:
        template_id = existing["template_id"]
        api(
            "PUT",
            f"/templates/{template_id}",
            json={k: v for k, v in desired.items() if k != "template_type"},
        )
        return api("GET", f"/templates/{template_id}"), False

    return api("POST", "/templates", json=desired), True


def _normalize_port_name(name):
    value = re.sub(r"\s+", "", str(name).lower())
    value = value.replace("gigabitethernet", "gi")
    value = value.replace("fastethernet", "fa")
    value = value.replace("ethernet", "e")
    return value


def resolve_port(node, spec):
    """Return (adapter_number, port_number, printable_name) for a node.

    FRR/VPCS use eth0/eth1... on adapter 0/port N, while the built-in switch
    uses Ethernet0... on adapter 0/port N. Resolve against node["ports"] so we
    never hard-code a port number.
    """
    ports = node.get("ports") or []
    if not ports:
        raise RuntimeError(
            f"Node {node.get('name')} không trả về danh sách ports; "
            "không thể tạo link an toàn."
        )

    if isinstance(spec, int):
        if 0 <= spec < len(ports):
            port = ports[spec]
            return port["adapter_number"], port["port_number"], port["name"]
        raise RuntimeError(
            f"Node {node.get('name')} chỉ có {len(ports)} port, không có port #{spec}."
        )

    wanted = _normalize_port_name(spec)
    for port in ports:
        if _normalize_port_name(port.get("name")) == wanted:
            return port["adapter_number"], port["port_number"], port["name"]

    # e.g. spec "eth2" but template calls it "Ethernet2".
    suffix = re.search(r"(\d+)$", spec)
    if suffix:
        for port in ports:
            if _normalize_port_name(port.get("name")).endswith(suffix.group(1)):
                return port["adapter_number"], port["port_number"], port["name"]

    # Last resort: use the numeric index.
    index = re.search(r"(\d+)$", spec)
    if index:
        idx = int(index.group(1))
        if idx < len(ports):
            port = ports[idx]
            return port["adapter_number"], port["port_number"], port["name"]

    raise RuntimeError(
        f"Không tìm thấy interface {spec} trên {node.get('name')}. "
        f"Template cung cấp: {', '.join(p.get('name', '?') for p in ports)}"
    )


def connect(project_id, node_a, spec_a, node_b, spec_b):
    a_adapter, a_port, a_name = resolve_port(node_a, spec_a)
    b_adapter, b_port, b_name = resolve_port(node_b, spec_b)
    body = {
        "nodes": [
            {
                "node_id": node_a["node_id"],
                "adapter_number": a_adapter,
                "port_number": a_port,
            },
            {
                "node_id": node_b["node_id"],
                "adapter_number": b_adapter,
                "port_number": b_port,
            },
        ]
    }
    link = api("POST", f"/projects/{project_id}/links", json=body)
    return link, a_name, b_name


def console_host(node):
    host = node.get("console_host") or "127.0.0.1"
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    return host


def vpcs_commands(node, commands, logfile_name, timeout=15):
    host = console_host(node)
    port = node.get("console")
    if not port:
        raise RuntimeError(f"Node {node.get('name')} không có console port.")

    last_error = None
    for attempt in range(1, 3):
        child = pexpect.spawn(
            "telnet",
            [str(host), str(port)],
            encoding="utf-8",
            timeout=timeout,
            codec_errors="ignore",
        )
        with open(log_path(logfile_name), "w", encoding="utf-8") as log:
            child.logfile_read = log
            try:
                # VPCS only prints its prompt after the first input when a
                # console is reopened; a bare newline is harmless on a fresh one.
                child.send("\r\n")
                child.expect(r">", timeout=30)
                output = []
                for command in commands:
                    child.sendline(command)
                    time.sleep(1)
                    child.expect(r">", timeout=timeout)
                    output.append(child.before or "")
                return "\n".join(output)
            except (pexpect.TIMEOUT, pexpect.EOF) as exc:
                last_error = exc
                print(
                    f"  Console {node.get('name')} chưa sẵn sàng "
                    f"(lần {attempt}/2: {exc.__class__.__name__}), chờ 5s..."
                )
                time.sleep(5)
            finally:
                child.close(force=True)

    raise RuntimeError(
        f"Không cấu hình được {node.get('name')} qua VPCS console: {last_error}. "
        f"Xem logs/{logfile_name}."
    )


def config_interfaces(config_path):
    text = config_path.read_text(encoding="utf-8")
    return re.findall(r"^\s*interface\s+(\S+)", text, re.MULTILINE)


def validate_router_interfaces(name, node, config_path):
    port_names = [p.get("name", "") for p in node.get("ports", [])]
    missing = [
        interface
        for interface in config_interfaces(config_path)
        if interface not in port_names
    ]
    if missing:
        raise RuntimeError(
            f"{name}: cấu hình dùng interface {', '.join(missing)} nhưng template "
            f"chỉ có {', '.join(port_names)}."
        )


def docker_exec(container, args, timeout=30):
    return subprocess.run(
        ["docker", "exec", container, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def wait_for_frr(project_id, state):
    """Wait until every FRR router answers vtysh, if Docker is local.

    Without the Docker CLI (e.g. remote GNS3 server) fall back to a fixed wait.
    """
    if not shutil.which("docker"):
        print(
            f"Không thấy docker CLI; chờ {ROUTER_BOOT_WAIT}s cho router khởi động."
        )
        time.sleep(ROUTER_BOOT_WAIT)
        return

    deadline = time.time() + ROUTER_BOOT_WAIT + 50
    pending = set(ROUTER_CONFIGS)
    while pending and time.time() < deadline:
        for name in list(pending):
            container = state["nodes"][name]["container"]
            result = docker_exec(
                container, ["vtysh", "-c", "show ip rip status"], timeout=20
            )
            if result.returncode == 0 and "Routing Protocol" in result.stdout:
                pending.discard(name)
        if pending:
            time.sleep(3)

    if pending:
        raise RuntimeError(
            "Các router FRR chưa chạy ripd: " + ", ".join(sorted(pending)) + ". "
            "Kiểm tra log Docker (docker logs <container>) hoặc chạy ./doctor.sh."
        )
    print("  Tất cả router FRR đã chạy ripd.")


def configure_pc_a(node):
    """Ask PC_A for a DHCP lease, retrying while the server starts."""
    last = ""
    for attempt in range(1, 7):
        last = vpcs_commands(node, ["ip dhcp", "show ip"], "PC_A.log")
        found = [
            int(octet)
            for octet in re.findall(r"192\.168\.10\.(\d+)", last or "")
        ]
        if any(10 <= octet <= 100 for octet in found):
            return last
        print(f"  PC_A chưa nhận DHCP (lần {attempt}/6), chờ 5s...")
        time.sleep(5)
    raise RuntimeError(
        "PC_A không nhận được DHCP trong 192.168.10.10-100. "
        "Kiểm tra DHCP_Server và cấu hình udhcpd (logs/PC_A.log).\n" + last
    )


def unique_project_name(base):
    existing = {p.get("name") for p in api("GET", "/projects")}
    if base not in existing:
        return base
    suffix = time.strftime("%Y%m%d-%H%M%S")
    candidate = f"{base}-{suffix}"
    print(
        f"Project '{base}' đã tồn tại; dùng tên mới '{candidate}' "
        "(chạy ./cleanup.sh để dọn project cũ)."
    )
    return candidate


def main():
    print(f"GNS3 API: {BASE} (v{API_VERSION})")
    print(f"GNS3 server: {api('GET', '/version')}")

    if not shutil.which("docker"):
        print(
            "[WARN] Không thấy docker CLI trên máy này. Nếu GNS3 server ở máy khác, "
            "hãy bỏ qua cảnh báo này."
        )

    templates = api("GET", "/templates")
    vpcs_template = choose_template(
        templates, lambda t: t.get("template_type") == "vpcs"
    )
    switch_template = choose_template(
        templates, lambda t: t.get("template_type") == "ethernet_switch"
    )
    if not vpcs_template or not switch_template:
        missing = []
        if not vpcs_template:
            missing.append("VPCS")
        if not switch_template:
            missing.append("Ethernet switch")
        raise RuntimeError(
            "Thiếu template: " + ", ".join(missing) + ".\n"
            "Trong GNS3: Preferences > Templates > New, hoặc cài các appliance "
            "mặc định (VPCS, Ethernet switch)."
        )

    frr_template, created_frr_template = ensure_frr_template(templates)
    docker_template, created_docker_template = ensure_dhcp_template(templates)

    project_name = unique_project_name(PROJECT_NAME)
    project = api("POST", "/projects", json={"name": project_name})
    project_id = project["project_id"]
    state = {
        "project_id": project_id,
        "project_name": project_name,
        "api_base": BASE,
        "api_version": API_VERSION,
        "router_backend": "frr-docker",
        "frr_template_id": frr_template["template_id"],
        "docker_template_id": docker_template["template_id"],
        "created_frr_template": created_frr_template,
        "created_docker_template": created_docker_template,
        "nodes": {},
        "links": [],
    }
    save_state(state)
    print(f"Đã tạo project: {project_name} ({project_id})")

    positions = {
        "R0": (0, 0),
        "R1": (350, -220),
        "R2": (700, 0),
        "R3": (350, 220),
        "SW_A": (-180, 0),
        "SW_B": (350, -400),
        "SW_C": (880, 0),
        "SW_D": (350, 400),
        "PC_A": (-400, -80),
        "PC_B": (160, -460),
        "PC_C": (1060, -80),
        "PC_D": (160, 460),
        "DHCP_Server": (-400, 80),
    }
    template_by_name = {
        "R0": frr_template,
        "R1": frr_template,
        "R2": frr_template,
        "R3": frr_template,
        "SW_A": switch_template,
        "SW_B": switch_template,
        "SW_C": switch_template,
        "SW_D": switch_template,
        "PC_A": vpcs_template,
        "PC_B": vpcs_template,
        "PC_C": vpcs_template,
        "PC_D": vpcs_template,
        "DHCP_Server": docker_template,
    }
    nodes = {}
    for name, template in template_by_name.items():
        x, y = positions[name]
        node_name = safe_node_name(name, template)
        try:
            node = create_node(project_id, template["template_id"], node_name, x, y)
            if name in ROUTER_CONFIGS:
                config_text = ROUTER_CONFIGS[name].read_text(encoding="utf-8")
                # Compute-specific settings must travel in "properties"; the
                # controller drops unknown top-level fields such as start_command.
                node = api(
                    "PUT",
                    f"/projects/{project_id}/nodes/{node['node_id']}",
                    json={"properties": {"start_command": frr_startup_script(config_text)}},
                )
                if not node.get("ports"):
                    node = api(
                        "GET", f"/projects/{project_id}/nodes/{node['node_id']}"
                    )
        except RuntimeError as exc:
            raise RuntimeError(
                f"Không tạo được node {name}: {exc}\n"
                f"Gợi ý: {explain_start_error(exc)}"
            ) from exc
        nodes[name] = node
        state["nodes"][name] = {
            "node_id": node["node_id"],
            "name": node.get("name", node_name),
            "container": (
                f"GNS3.{node.get('name', node_name)}.{project_id}"
                if template.get("template_type") == "docker"
                else None
            ),
            "console": node.get("console"),
            "console_host": node.get("console_host", "127.0.0.1"),
            "node_type": node.get("node_type"),
            "ports": [
                {
                    "name": p.get("name"),
                    "adapter_number": p.get("adapter_number"),
                    "port_number": p.get("port_number"),
                }
                for p in node.get("ports", [])
            ],
        }
        save_state(state)
        print(f"  + {name} ({node.get('node_type')})")

    for name, cfg in ROUTER_CONFIGS.items():
        validate_router_interfaces(name, nodes[name], cfg)

    # Ports on the FRR template are eth0, eth1, eth2 (adapter 0..2, port 0).
    links = [
        ("R0", "eth1", "R1", "eth2"),  # 10.0.12.0/30
        ("R1", "eth0", "R2", "eth0"),  # 10.0.23.0/24
        ("R2", "eth2", "R3", "eth2"),  # 10.0.34.0/24
        ("R3", "eth0", "R0", "eth2"),  # 10.0.41.0/24
        ("R0", "eth0", "SW_A", 0),
        ("R1", "eth1", "SW_B", 0),
        ("R2", "eth1", "SW_C", 0),
        ("R3", "eth1", "SW_D", 0),
        ("SW_A", 1, "PC_A", 0),
        ("SW_A", 2, "DHCP_Server", 0),
        ("SW_B", 1, "PC_B", 0),
        ("SW_C", 1, "PC_C", 0),
        ("SW_D", 1, "PC_D", 0),
    ]

    used_ports = {}
    for a, spec_a, b, spec_b in links:
        a_key = (a, *resolve_port(nodes[a], spec_a)[:2])
        b_key = (b, *resolve_port(nodes[b], spec_b)[:2])
        for key in (a_key, b_key):
            if key in used_ports:
                raise RuntimeError(
                    f"Port bị dùng hai lần: {key[0]} adapter {key[1]} port {key[2]}"
                )
            used_ports[key] = True
        link, a_name, b_name = connect(
            project_id, nodes[a], spec_a, nodes[b], spec_b
        )
        state["links"].append(link.get("link_id"))
        save_state(state)
        print(f"  link {a}:{a_name} <-> {b}:{b_name}")

    api("POST", f"/projects/{project_id}/open", json={})

    start_order = [
        "R0",
        "R1",
        "R2",
        "R3",
        "SW_A",
        "SW_B",
        "SW_C",
        "SW_D",
        "DHCP_Server",
        "PC_A",
        "PC_B",
        "PC_C",
        "PC_D",
    ]
    failures = []
    for name in start_order:
        node_id = nodes[name]["node_id"]
        try:
            api("POST", f"/projects/{project_id}/nodes/{node_id}/start", json={})
        except RuntimeError as exc:
            failures.append((name, str(exc)))
        time.sleep(1)

    if failures:
        details = "\n".join(
            f"  - {name}: {exc}\n    Gợi ý: {explain_start_error(exc)}"
            for name, exc in failures
        )
        raise RuntimeError(
            "Không start được các node sau:\n" + details +
            "\nSửa môi trường GNS3 rồi chạy lại ./cleanup.sh và ./setup.sh."
        )

    wait_for_frr(project_id, state)

    print("Cấu hình client VPCS...")
    configure_pc_a(state["nodes"]["PC_A"])
    for name, ip in (
        ("PC_B", "192.168.20.10/24 192.168.20.1"),
        ("PC_C", "192.168.30.10/24 192.168.30.1"),
        ("PC_D", "192.168.40.10/24 192.168.40.1"),
    ):
        node = state["nodes"][name]
        vpcs_commands(node, [f"ip {ip}", "show ip"], f"{name}.log")

    print(f"Đợi RIP và DHCP hội tụ ({RIP_WAIT}s)...")
    time.sleep(RIP_WAIT)
    print("Chạy kiểm thử tự động...")
    result = subprocess.run([sys.executable, str(ROOT / "test.py")], check=False)

    print(f"\nHoàn tất. Project ID: {project_id}")
    print(f"Mở GNS3 project: {project_name}")
    print(f"Log nằm trong: {ROOT / 'logs'}")
    return result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        if (ROOT / "state.json").exists():
            print(
                "Project có thể đã được tạo một phần. Kiểm tra state.json rồi chạy "
                "./cleanup.sh nếu muốn dọn.",
                file=sys.stderr,
            )
        sys.exit(1)
