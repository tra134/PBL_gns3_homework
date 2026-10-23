# PBL3 — RIPv2 + DHCP trên GNS3 (FRRouting)

Bộ lab tái tạo topology trong báo cáo PBL3: 4 router nối vòng, 4 LAN, DHCP server ở Network A và client ở cả bốn LAN.

Router dùng **FRRouting (FRR) trong Docker** thay cho Cisco IOSv, nên **không cần image Cisco có bản quyền**. Địa chỉ IP, topology và hành vi RIPv2 giữ nguyên như báo cáo.

Script dùng GNS3 Controller API để tạo topology. Hỗ trợ **GNS3 3.x (API `/v3`, có đăng nhập)** và **GNS3 2.x (API `/v2`)**. Với GNS3 3.x, script tự đăng nhập bằng `GNS3_USER`/`GNS3_PASSWORD` (mặc định `admin`/`admin`).

## Kiến trúc lab

| Thành phần | Công nghệ | Số lượng |
|---|---|---|
| Router R0–R3 | FRR Docker (`frrouting/frr:latest`), 4 adapter | 4 |
| Switch LAN A–D | Ethernet switch có sẵn của GNS3 (Dynamips) | 4 |
| Client PC_A–PC_D | VPCS | 4 |
| DHCP server | Alpine Docker + BusyBox `udhcpd` | 1 |

Mỗi router FRR nhận cấu hình qua `start_command` riêng (GNS3 `properties`), gồm IP các interface `eth0..eth2` và `router rip` / `version 2`. Cấu hình nằm trong `configs-frr/R0.conf` … `R3.conf`.

Thư mục `configs/R0.cfg` … `R3.cfg` vẫn giữ cấu hình Cisco IOS tương đương để tham chiếu trong báo cáo; script không dùng các file này.

## Yêu cầu trước khi chạy

Máy chạy GNS3 cần **đủ** các thành phần sau. Chạy `./doctor.sh` để kiểm tra nhanh.

- GNS3 GUI + GNS3 server đang chạy, API mặc định `http://127.0.0.1:3080`.
- **Docker** hoạt động; image `frrouting/frr:latest` và `alpine:3.20`. GNS3 sẽ tự pull nếu thiếu, nhưng nên pull trước:
  ```bash
  docker pull frrouting/frr:latest
  docker pull alpine:3.20
  ```
- **VPCS** (binary `vpcs`) cho các client.
- **Dynamips** cho node Ethernet switch có sẵn của GNS3.
- **ubridge** cho link của VPCS và Docker node.
- **busybox-static** cho Docker node (busybox động không dùng được).
- Template `VPCS` và `Ethernet switch` có sẵn trong GNS3 (mặc định đã có).

Trên Debian 13, gói `vpcs` trong apt chỉ là **0.5b2** — GNS3 cần **>= 0.6.1**, nên phải build VPCS 0.8.5 từ GitHub. `dynamips` và `ubridge` cũng cần build:

```bash
sudo apt update
sudo apt install -y python3 python3-requests python3-pexpect telnet \
                    busybox-static libcap2-bin \
                    build-essential cmake git libpcap-dev libelf-dev

# VPCS 0.8.5 (bắt buộc: bản apt 0.5b2 quá cũ với GNS3 3.x)
git clone --depth 1 --branch v0.8.5 https://github.com/GNS3/vpcs.git
cd vpcs && make -j"$(nproc)" && sudo make install
cd ..

# Dynamips (Ethernet switch)
git clone --depth 1 --branch v0.2.25 https://github.com/GNS3/dynamips.git
cd dynamips && mkdir -p build && cd build
cmake .. && make -j"$(nproc)" && sudo make install
cd ../..

# ubridge (link VPCS / Docker)
git clone --depth 1 --branch v1.3.1 https://github.com/GNS3/ubridge.git
cd ubridge && make -j"$(nproc)" && sudo make install

# ubridge BẮT BUỘC có quyền mở network adapter, nếu không node VPCS/Docker
# sẽ báo "uBridge requires root access or the capability to interact
# with network adapters"
sudo setcap cap_net_admin,cap_net_raw+ep "$(command -v ubridge)"
getcap "$(command -v ubridge)"   # phải thấy cap_net_admin,cap_net_raw=ep

# Khởi động lại GNS3 server/GUI sau khi cài để nhận binary mới trong PATH.
```

Không chạy script bằng `sudo`: GNS3 API thường chạy dưới tài khoản người dùng. Chỉ lệnh cài package cần `sudo`.

Nếu không muốn/có thể dùng `sudo`, có thể giải nén gói `.deb` và đặt binary vào `~/.local/bin` (thư mục này thường có trong `PATH`). Riêng VPCS vẫn phải build 0.8.5 vì gói apt quá cũ:

```bash
mkdir -p ~/.local/bin /tmp/pbl3-deps
cd /tmp/pbl3-deps
apt-get download busybox-static libcap2-bin
for f in busybox-static_*.deb libcap2-bin_*.deb; do dpkg-deb -x "$f" extracted; done
cp extracted/bin/busybox ~/.local/bin/busybox-static
chmod +x ~/.local/bin/busybox-static

# VPCS 0.8.5 build không cần cài hệ thống
git clone --depth 1 --branch v0.8.5 https://github.com/GNS3/vpcs.git
cd vpcs && make -j"$(nproc)"
cp src/vpcs ~/.local/bin/vpcs

# ubridge/dynamips: build rồi copy binary vào ~/.local/bin
# Sau đó vẫn cần setcap (cần sudo) cho ubridge:
sudo setcap cap_net_admin,cap_net_raw+ep ~/.local/bin/ubridge
```

Nếu `setcap` báo lỗi filesystem không hỗ trợ capability, dùng setuid thay thế:

```bash
sudo chown root:root ~/.local/bin/ubridge
sudo chmod u+s ~/.local/bin/ubridge
```

## 1. Kiểm tra GNS3 và đăng nhập API

Mở GNS3 và để local server hoạt động. Kiểm tra API:

```bash
curl -s http://127.0.0.1:3080/v3/version
curl -s -X POST http://127.0.0.1:3080/v3/access/users/authenticate \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}'
```

Nếu API không ở `127.0.0.1:3080`, đặt biến môi trường trước khi chạy:

```bash
export GNS3_URL=http://127.0.0.1:3080       # script tự thêm /v3 hoặc /v2
export GNS3_USER=admin
export GNS3_PASSWORD=admin
```

Nếu dùng GNS3 2.x có API key, đặt thêm `GNS3_API_KEY` (script gửi header `Authorization: Bearer ...`).

## 2. Preflight và setup

```bash
chmod +x setup.sh cleanup.sh test.sh doctor.sh
./doctor.sh      # kiểm tra API, template, Docker, binary
./setup.sh
```

`./doctor.sh` chỉ đọc thông tin, không tạo project. Kết quả cuối phải là `sẵn sàng chạy ./setup.sh`.

Script sẽ:
1. Đăng nhập API (GNS3 3.x), kiểm tra template VPCS và Ethernet switch.
2. Tạo (hoặc cập nhật) template FRR router và DHCP Alpine.
3. Tạo project, 4 router FRR, 4 switch LAN, 4 client VPCS và DHCP server.
4. Kết nối 13 link và map đúng adapter/port của từng node.
5. Gán cấu hình FRR riêng cho từng router qua `start_command`, khởi động node.
6. Đặt PC_A dùng DHCP, PC_B/C/D dùng IP tĩnh.
7. Chạy kiểm thử tự động và ghi log vào `logs/`.

Lần chạy đầu có thể mất vài phút do Docker tải image (nếu chưa pull) và router khởi động.

Nếu project cùng tên đã tồn tại, script tự dùng tên có hậu tố thời gian thay vì ghi đè. Chạy `./cleanup.sh` để dọn project cũ.

## 3. Kiểm thử

```bash
./test.sh
```

Script kiểm tra:
- Trong từng router FRR (`docker exec ... vtysh`): `show ip route` có route `R>*` tới 3 LAN còn lại, và `show ip rip status` báo RIP đang chạy.
- Từ PC_A ping gateway cả 4 LAN; PC_B/C/D ping gateway LAN mình và LAN A.
- PC_A nhận IP DHCP trong dải `192.168.10.10–192.168.10.100`, gateway `192.168.10.1`.

Kết quả mong đợi: `9/9 checks marked PASS`.

Kiểm tra thủ công trong router FRR:

```bash
# Tên container có dạng GNS3.R0.<project_id>
docker ps --format '{{.Names}}'
docker exec -it GNS3.R0.<project_id> vtysh
```

Trong `vtysh`:

```text
show ip interface brief
show ip rip status
show ip route
```

## 4. Mở project và quan sát

Mở project `PBL3-RIPv2-DHCP-FRR` (hoặc tên có hậu tố thời gian) trong GNS3 GUI. Router FRR không có console telnet (image không chạy telnetd); cấu hình và kiểm tra qua `docker exec ... vtysh` như trên. Console của VPCS vẫn mở được bình thường.

## 5. Cleanup

```bash
./cleanup.sh
```

Script yêu cầu xác nhận trước khi xóa project do nó tạo. Dùng `./cleanup.sh --yes` để xác nhận tự động. Lệnh này **xóa project lab và các node trong đó**, đồng thời xóa template FRR/DHCP nếu chính script tạo ra. Không xóa Docker image hay dữ liệu GNS3 khác.

## Địa chỉ IP giữ theo báo cáo

| Node | Interface / vai trò | IP / mask |
|---|---|---|
| R0 | LAN A (`eth0`) | `192.168.10.1/24` |
| R0 | R0–R1 (`eth1`) | `10.0.12.1/30` |
| R0 | R0–R3 (`eth2`) | `10.0.41.2/24` |
| R1 | R1–R2 (`eth0`) | `10.0.23.1/24` |
| R1 | LAN B (`eth1`) | `192.168.20.1/24` |
| R1 | R1–R0 (`eth2`) | `10.0.12.2/30` |
| R2 | R2–R1 (`eth0`) | `10.0.23.2/24` |
| R2 | LAN C (`eth1`) | `192.168.30.1/24` |
| R2 | R2–R3 (`eth2`) | `10.0.34.1/24` |
| R3 | R3–R0 (`eth0`) | `10.0.41.1/24` |
| R3 | LAN D (`eth1`) | `192.168.40.1/24` |
| R3 | R3–R2 (`eth2`) | `10.0.34.2/24` |
| DHCP server | Network A | `192.168.10.2/24`, gateway `192.168.10.1` |

## Limitations / troubleshooting

- Router FRR được cấu hình và kiểm tra qua **Docker CLI trên máy chạy GNS3**. Nếu GNS3 server ở máy khác, script vẫn tạo được topology nhưng bước cấu hình/kiểm tra router cần chạy trên máy đó (hoặc cấu hình thủ công bằng `vtysh` trong container).
- DHCP server là Alpine Docker chạy BusyBox `udhcpd` (qua busybox tĩnh do GNS3 inject), không phải dnsmasq/Packet Tracer. Không cần `apk`/internet trong lab network.
- Nếu `./doctor.sh` báo thiếu template `VPCS`/`Ethernet switch`: tạo lại trong GNS3 Preferences > Templates (hoặc cài appliance mặc định).
- Nếu start node báo `No path to a VPCS executable`, `Could not find Dynamips`, `busybox`, hoặc `ubridge`: cài thành phần tương ứng ở trên rồi chạy lại `./cleanup.sh` và `./setup.sh`.
- `uBridge requires root access or the capability to interact with network adapters`: chạy `sudo setcap cap_net_admin,cap_net_raw+ep "$(command -v ubridge)"`, kiểm tra bằng `getcap "$(command -v ubridge)"`, rồi khởi động lại GNS3 server.
- `VPCS executable version must be >= 0.6.1 but not a 0.8`: thông báo này thực chất nghĩa là VPCS **cũ hơn 0.6.1** (bản apt Debian 13 là 0.5b2). Build VPCS 0.8.5 như trên và đảm bảo `vpcs -v` in ra `version 0.8.5`, sau đó khởi động lại GNS3 server.
- Nếu `docker exec ... vtysh` báo không tìm thấy container: kiểm tra project đã start trong GNS3 và `docker ps` có container `GNS3.R0.<project_id>`.
- Nếu router chưa có route RIP: đợi thêm 20–30 giây (RIP cập nhật mỗi 30 giây), sau đó chạy lại `./test.sh`.
- Các subnet WAN `/24` được giữ đúng như yêu cầu. Với RIPv2, FRR quảng bá theo prefix nên không có auto-summary classful như Cisco IOS.
