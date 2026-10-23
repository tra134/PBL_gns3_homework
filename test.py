#!/usr/bin/env python3
"""Verify the PBL3 FRR lab: RIPv2 routes, VPCS connectivity and DHCP."""
import re
import subprocess
import sys
from pathlib import Path

import pexpect

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from common import load_state, log_path  # noqa: E402


def console_host(node):
    host = node.get("console_host") or "127.0.0.1"
    if host in ("0.0.0.0", "::", "[::]"):
        host = "127.0.0.1"
    return host


def run_console(node, commands, filename, prompt=r">", timeout=15):
    host = console_host(node)
    port = node.get("console")
    if not port:
        raise RuntimeError(f"{node.get('name')} không có console.")
    child = pexpect.spawn(
        "telnet",
        [str(host), str(port)],
        encoding="utf-8",
        timeout=timeout,
        codec_errors="ignore",
    )
    output = []
    try:
        # VPCS only prints its prompt after the first input on a reconnected
        # console; a newline is harmless on the very first connection.
        child.send("\r\n")
        child.expect(prompt, timeout=30)
        for command in commands:
            child.sendline(command)
            child.expect(prompt, timeout=timeout)
            output.append(child.before)
    finally:
        child.close(force=True)
    text = "\n".join(output)
    log_path(filename).write_text(text, encoding="utf-8")
    return text


def frr_show(node, commands, filename):
    """Run vtysh inside the router's Docker container."""
    container = node.get("container")
    if not container:
        raise RuntimeError(f"{node.get('name')} không phải Docker container.")
    output = []
    for command in commands:
        result = subprocess.run(
            ["docker", "exec", container, "vtysh", "-c", command],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"docker exec {container} vtysh -c '{command}' lỗi: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        output.append(f"### {command}\n{result.stdout}")
    text = "\n".join(output)
    log_path(filename).write_text(text, encoding="utf-8")
    return text


def ping_ok(output):
    text = output.lower()
    replies = len(re.findall(r"bytes from", text))
    lost = len(re.findall(r"no reply|request timed out|100% packet loss", text))
    return replies > 0 and lost == 0


def main():
    state = load_state()
    nodes = state["nodes"]
    results = []

    print("=== Router checks (FRR) ===")
    for router in ["R0", "R1", "R2", "R3"]:
        try:
            out = frr_show(
                nodes[router],
                ["show ip route", "show ip rip status"],
                f"{router}-test.log",
            )
            has_rip = bool(re.search(r'Routing Protocol is\s+"?rip', out, re.I))
            learned_routes = re.findall(
                r"(?m)^\s*R>\*?\s+(\d+\.\d+\.\d+\.\d+)/\d+", out
            )
            lan_routes = {
                "R0": {"192.168.20.0", "192.168.30.0", "192.168.40.0"},
                "R1": {"192.168.10.0", "192.168.30.0", "192.168.40.0"},
                "R2": {"192.168.10.0", "192.168.20.0", "192.168.40.0"},
                "R3": {"192.168.10.0", "192.168.20.0", "192.168.30.0"},
            }[router]
            missing = lan_routes.difference(learned_routes)
            ok = has_rip and not missing
            results.append((f"{router}: RIP + learned LAN routes", ok))
            status = "PASS" if ok else "CHECK"
            extra = "" if ok else f" (RIP={has_rip}, thiếu route={sorted(missing)})"
            print(f"[{status}] {router}: xem logs/{router}-test.log{extra}")
        except Exception as exc:
            results.append((f"{router}: docker/vtysh", False))
            print(f"[FAIL] {router}: {exc}")

    print("\n=== VPCS ping checks ===")
    checks = [
        ("PC_A", ["ping 192.168.10.1", "ping 192.168.20.1", "ping 192.168.30.1", "ping 192.168.40.1"]),
        ("PC_B", ["ping 192.168.20.1", "ping 192.168.10.1"]),
        ("PC_C", ["ping 192.168.30.1", "ping 192.168.10.1"]),
        ("PC_D", ["ping 192.168.40.1", "ping 192.168.10.1"]),
    ]
    for pc, commands in checks:
        try:
            out = run_console(
                nodes[pc], commands, f"{pc}-ping.log", prompt=r">", timeout=20
            )
            ok = ping_ok(out)
            results.append((f"{pc}: ping", ok))
            print(f"[{'PASS' if ok else 'CHECK'}] {pc}: xem logs/{pc}-ping.log")
        except Exception as exc:
            results.append((f"{pc}: ping", False))
            print(f"[FAIL] {pc}: {exc}")

    print("\n=== DHCP client check ===")
    try:
        out = run_console(
            nodes["PC_A"], ["show ip"], "PC_A-dhcp-check.log", prompt=r">", timeout=15
        )
        addresses = [
            int(octet)
            for octet in re.findall(r"192\.168\.10\.(\d+)", out)
        ]
        dhcp_ok = any(10 <= octet <= 100 for octet in addresses)
        results.append(("PC_A received DHCP address", dhcp_ok))
        print(f"[{'PASS' if dhcp_ok else 'CHECK'}] PC_A DHCP trong 192.168.10.10-100")
    except Exception as exc:
        results.append(("PC_A DHCP", False))
        print(f"[FAIL] DHCP check: {exc}")

    print("\n=== Summary ===")
    for name, ok in results:
        print(f"[{'PASS' if ok else 'CHECK/FAIL'}] {name}")
    failures = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failures)}/{len(results)} checks marked PASS.")
    if failures:
        print(
            "Nếu có CHECK/FAIL, xem logs/ và kiểm tra: docker ps, logs router FRR, "
            "RIP convergence, DHCP container."
        )
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Test error: {exc}", file=sys.stderr)
        sys.exit(1)
