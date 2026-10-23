#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from common import STATE, api, load_state  # noqa: E402


def delete_template(template_id, label):
    try:
        api("DELETE", f"/templates/{template_id}")
        print(f"Đã xóa template {label} do script tạo.")
    except Exception as exc:
        if " 404 " not in f" {exc} ":
            print(f"Cảnh báo: không xóa được template {label}: {exc}")


def main():
    state = load_state()
    project_id = state["project_id"]
    project_name = state.get("project_name", project_id)
    if "--yes" not in sys.argv:
        answer = input(
            f"Xóa project GNS3 '{project_name}' ({project_id}) và các node bên trong? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            print("Đã hủy.")
            return 0

    exit_code = 0
    try:
        api("DELETE", f"/projects/{project_id}")
        print(f"Đã xóa project {project_name}.")
    except Exception as exc:
        if " 404 " in f" {exc} ":
            print(f"Project {project_name} không còn trên GNS3, bỏ qua.")
        else:
            print(f"Không xóa được project: {exc}")
            exit_code = 1

    if state.get("created_frr_template") and state.get("frr_template_id"):
        delete_template(state["frr_template_id"], "router FRR")

    if state.get("created_docker_template") and state.get("docker_template_id"):
        delete_template(state["docker_template_id"], "DHCP Docker")

    STATE.unlink(missing_ok=True)
    print("Cleanup hoàn tất.")
    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Cleanup error: {exc}", file=sys.stderr)
        sys.exit(1)
