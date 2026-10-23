#!/usr/bin/env python3
"""Preflight check for the PBL3 FRR lab.

Run ./doctor.sh before ./setup.sh. It only reads data from GNS3 and the local
machine; it does not create or modify a project.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

OK = "PASS"
WARN = "WARN"
FAIL = "FAIL"


def result(status, message):
    print(f"[{status}] {message}")
    return status == OK


def check_command(name, extra_candidates=()):
    for candidate in (name, *extra_candidates):
        path = shutil.which(candidate)
        if path:
            return path
    return None


def busybox_is_static(path):
    try:
        output = subprocess.run(
            ["ldd", path], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "not a dynamic executable" in output.stdout + output.stderr


def vpcs_version(path):
    """Return the parsed VPCS version tuple, or None if it cannot be read."""
    try:
        output = subprocess.run(
            [path, "-v"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(
        r"version\s+(\d+)\.(\d+)(?:\.(\d+))?",
        output.stdout + output.stderr,
        re.I,
    )
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def ubridge_privileges(path):
    """Explain whether ubridge can open network adapters.

    GNS3 needs uBridge to create TAP/raw sockets. It must either be setuid root
    or carry cap_net_admin,cap_net_raw. Returns (ok, detail).
    """
    try:
        mode = os.stat(path).st_mode
    except OSError as exc:
        return False, str(exc)
    if mode & stat.S_ISUID:
        return True, "setuid"

    getcap = shutil.which("getcap")
    if not getcap:
        return False, "không đọc được capability (thiếu getcap, cần cài libcap2-bin)"
    try:
        output = subprocess.run(
            [getcap, str(path)], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    caps = (output.stdout + output.stderr).strip()
    lowered = caps.lower()
    if "cap_net_admin" in lowered and "cap_net_raw" in lowered:
        return True, caps.split("=", 1)[-1].strip()
    return False, caps or "không có capability nào"


def docker_image_present(image):
    try:
        checked = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return checked.returncode == 0


def main():
    try:
        from common import API_VERSION, BASE, api
    except Exception as exc:
        print(f"[{FAIL}] Không kết nối/đăng nhập được GNS3 API: {exc}")
        return 1

    ok = True
    print(f"[{OK}] GNS3 API: {BASE} (v{API_VERSION})")
    try:
        version = api("GET", "/version")
        print(f"[{OK}] GNS3 server: {version}")
    except Exception as exc:
        print(f"[{FAIL}] /version: {exc}")
        return 1

    try:
        templates = api("GET", "/templates")
    except Exception as exc:
        print(f"[{FAIL}] Không đọc được templates: {exc}")
        return 1

    for label, wanted in (("VPCS", "vpcs"), ("Ethernet switch", "ethernet_switch")):
        found = any(t.get("template_type") == wanted for t in templates)
        ok = result(OK if found else FAIL, f"Template {label}") and ok

    try:
        capabilities = api("GET", "/computes/local/capabilities")
        node_types = set(capabilities.get("node_types", []))
        for node_type in ("docker", "vpcs", "ethernet_switch"):
            found = node_type in node_types
            ok = result(
                OK if found else FAIL,
                f"Compute 'local' hỗ trợ node type '{node_type}'",
            ) and ok
        print(
            "[INFO] Router dùng FRR trong Docker — không cần image Cisco IOSv."
        )
    except Exception as exc:
        print(f"[{WARN}] Không đọc được capabilities của compute local: {exc}")

    print("\n--- Kiểm tra binary trên máy chạy GNS3 (bỏ qua nếu server ở xa) ---")
    vpcs = check_command("vpcs")
    if vpcs:
        version = vpcs_version(vpcs)
        if version is None:
            ok = False
            print(f"[{FAIL}] VPCS: {vpcs} (không đọc được phiên bản bằng 'vpcs -v')")
        elif version < (0, 6, 1):
            ok = False
            print(
                f"[{FAIL}] VPCS: {vpcs} version "
                f"{'.'.join(map(str, version))} — GNS3 cần >= 0.6.1. "
                "Debian 13 chỉ có 0.5b2 trong apt; build 0.8.5 từ "
                "github.com/GNS3/vpcs (xem README)."
            )
        else:
            print(f"[{OK}] VPCS: {vpcs} version {'.'.join(map(str, version))}")
    else:
        ok = False
        print("[FAIL] VPCS: không tìm thấy. Cài vpcs hoặc build 0.8.5.")

    dynamips = check_command("dynamips")
    ok = result(OK if dynamips else FAIL, f"Dynamips: {dynamips or 'không tìm thấy'}") and ok

    ubridge = check_command("ubridge")
    if ubridge:
        privileged, detail = ubridge_privileges(ubridge)
        if privileged:
            print(f"[{OK}] ubridge: {ubridge} ({detail})")
        else:
            ok = False
            print(
                f"[{FAIL}] ubridge: {ubridge} không có quyền mở network adapter "
                f"({detail}).\n"
                f"       Chạy: sudo setcap cap_net_admin,cap_net_raw+ep {ubridge}\n"
                "       (hoặc: sudo chown root:root + sudo chmod u+s trên binary)"
            )
    else:
        ok = False
        print("[FAIL] ubridge: không tìm thấy.")

    busybox = check_command("busybox-static", ("busybox.static", "busybox"))
    if busybox and busybox_is_static(busybox):
        print(f"[{OK}] busybox tĩnh: {busybox}")
    else:
        ok = False
        print(
            f"[{FAIL}] busybox tĩnh: {busybox or 'không tìm thấy'} "
            "(cài busybox-static; busybox động không dùng được cho Docker)"
        )

    docker = shutil.which("docker")
    if docker:
        print(f"[{OK}] docker CLI: {docker}")
        for image, env_name in (
            ("frrouting/frr:latest", "PBL3_FRR_IMAGE"),
            ("alpine:3.20", "PBL3_DHCP_IMAGE"),
        ):
            wanted = os.environ.get(env_name, image)
            present = docker_image_present(wanted)
            if present:
                print(f"[{OK}] Docker image {wanted} đã có sẵn")
            elif present is False:
                print(
                    f"[{WARN}] Docker image {wanted} chưa có; GNS3 sẽ pull khi start "
                    "(cần mạng)."
                )
            else:
                print(f"[{WARN}] Không kiểm tra được Docker image {wanted}.")
    else:
        print(
            f"[{WARN}] Không thấy docker CLI (có thể GNS3 server ở máy khác). "
            "Router FRR và DHCP đều dùng Docker."
        )

    print(
        f"\nKết quả: {'sẵn sàng chạy ./setup.sh' if ok else 'còn thiếu điều kiện ở trên'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
