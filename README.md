# Linux Agent

AI agent hỗ trợ Linux có khả năng hành động thật trên máy (chạy lệnh, cài phần mềm,
chẩn đoán hệ thống) với cơ chế an toàn nghiêm ngặt — không phải chatbot hỏi-đáp.

## Cài đặt

### Cách chính: một lệnh

```bash
curl -sSL https://raw.githubusercontent.com/<user>/<repo>/main/install.sh | bash
```

Thay `<user>/<repo>` bằng repo thật. Script kiểm tra Linux + Python (>= 3.11), cài `pipx`
nếu chưa có, rồi cài `linux-agent` bằng pipx và xác nhận bằng `agent --help`. Script **không**
cần (và không nên) chạy bằng root: đừng dùng `curl … | sudo bash` — nó tự gọi `sudo` đúng
lệnh cần quyền hệ thống (cài `pipx` bằng package manager của distro). Chạy lại nhiều lần an
toàn: nếu đã cài, script chuyển sang `pipx upgrade`. Xong thì chạy `agent doctor`.

### Dự phòng: pipx thủ công

Khi đã có [pipx](https://pipx.pypa.io/):

```bash
pipx install "git+https://github.com/<user>/<repo>.git"   # giai đoạn demo (chưa publish PyPI)
pipx install linux-agent                                   # sau khi đã publish PyPI
```

Không dùng `pip install` thẳng vào Python hệ thống: nhiều distro (Ubuntu 23.04+, Debian 12+…)
chặn theo PEP 668 (`externally-managed-environment`).

### Known limitations khi cài đặt

- **Cần kết nối mạng khi cài**: pipx tải các phụ thuộc từ PyPI (và clone Git ở giai đoạn
  demo). `install.sh` từ chối chạy khi `PACKAGE_SOURCE` còn placeholder `<user>/<repo>`.
- **Python >= 3.11** (khớp `requires-python` và `agent doctor`). Ubuntu 22.04 mặc định là
  3.10 nên script dừng với thông báo rõ (mã 12) thay vì cài dở.
- **Tier A** (hỗ trợ đầy đủ, đã chạy `install.sh` trên container sạch: cài mới, chạy lại
  → `pipx upgrade`, rồi gỡ bằng `agent uninstall`): Ubuntu 24.04 (apt), Fedora 40 (dnf),
  Arch (pacman), cả khi chạy bằng root và bằng user thường có `sudo`. Debian/RHEL cùng họ
  package manager nhưng chưa được thử riêng.
- **Tier B (best-effort)**: openSUSE (zypper) — cài được trên Leap 16 và Tumbleweed trong
  container; Alpine chưa hỗ trợ (chưa có adapter `apk`, không có bash/systemd mặc định).
- **Ngoài phạm vi**: NixOS, Silverblue/Kinoite (immutable) và distro non-systemd — script
  phát hiện và dừng với thông báo "không hỗ trợ" (mã 11), không thử cài. macOS không
  thuộc phạm vi; WSL chưa được kiểm thử.
- Kiểm thử bằng container **không thay thế** máy thật: chưa thử trên VM có systemd thật,
  `sudo` hỏi mật khẩu thật, hay GUI (`agent-gui` cần extra `linux-agent[gui]`/PySide6; chưa
  kiểm thử cài qua pipx).

### Phát triển (từ source)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`default.db` (bộ tài liệu Linux mặc định, chỉ đọc) đã được commit sẵn tại
`src/agent/resources/rag/default.db` và đóng gói kèm wheel — không cần seed thủ
công sau khi clone hay cài đặt.

## Gỡ cài đặt

```bash
agent uninstall
```

In ra từng thư mục sẽ bị xóa **vĩnh viễn** (cấu hình + API key, lịch sử session, cơ sở tri thức, audit log, cache), rồi chỉ xóa sau khi bạn gõ chính xác `XOA VINH VIEN` (không có `--yes`/`--force`). Nếu xóa dữ liệu lỗi ở bất kỳ thư mục nào, lệnh báo rõ path + lý do và **không** gỡ package. Nếu xóa xong, package được gỡ tự động khi nhận diện được cách cài (pipx hoặc pip); nếu không chắc chắn, lệnh chỉ in cách gỡ thủ công. GUI có cùng chức năng ở Settings → "Vùng nguy hiểm".

## GUI — Known limitations

- GUI là extra tùy chọn `linux-agent[gui]`, khởi động bằng `agent-gui`; CLI
  `agent` không cần PySide6. GUI hiện dùng light theme, tối thiểu 800×560.
- GUI khởi động từ desktop thường **không có TTY** và không hỏi, nhận, lưu hay
  truyền mật khẩu sudo. Tier 2 vẫn phải được xác nhận riêng bằng **Allow once**;
  xác thực sudo không thay thế bước xác nhận này.
- Khi sudo ticket hết hạn, card authentication-required hướng dẫn chạy `sudo -v`
  trong **terminal bên ngoài**, rồi quay lại chọn **Retry**. Retry kiểm tra lại
  bằng `sudo -n` trước khi tiếp tục đúng action pending đã được cho phép, không
  tự gửi lại cả lượt chat. Ngoại lệ là policy **command-scoped NOPASSWD** đã được
  quản trị viên cấu hình cho đúng command; không cần và không khuyến nghị quyền
  NOPASSWD rộng cho toàn bộ agent.
- Sudo timestamp có thể bị giới hạn theo terminal/tiến trình/session. Vì vậy
  `sudo -v` thành công ở terminal **không bảo đảm** GUI dùng lại được ticket trên
  mọi máy. Nếu Retry vẫn báo chưa xác thực, dừng/dismiss action và kiểm tra policy
  trong môi trường phù hợp; không vô hiệu hoá cơ chế bảo vệ sudo để vượt qua.
- Không chọn demo Tier 2 cần sudo khi ticket đã hết hạn. Trạng thái
  **authentication-required là hành vi dự kiến, không phải crash**. Ưu tiên demo
  read-only hoặc fake integration; không dùng máy phát triển làm host thử mutation.
- Provider errors theo UI/UX §9.2: authentication/configuration đỏ + Open Settings;
  timeout/network/rate-limit amber + Retry; malformed/unknown đỏ + Retry.
  Provider Retry chỉ gửi lại completion thất bại, không chạy lại tool/loop/RAG,
  không thêm user message; không cung cấp tools và từ chối response yêu cầu tool.
  Tối đa 3 lần Retry thủ công mỗi lượt lỗi, không bấm chồng; Stop/đổi session/lượt
  mới/đóng app vô hiệu hóa snapshot. Usage vẫn được ghi nếu Stop trong request
  đang chạy; không lưu câu trả lời đã hủy. Budget dùng cùng giới hạn khi service
  được truyền giới hạn (GUI hiện không có cấu hình budget riêng).
  Retry bị disable khi không có snapshot hợp lệ hoặc history đã thay đổi.
- Header hiển thị distro/version/package manager từ chính kết quả scan của System
  page, không quét thêm; compact hiển thị summary trong tooltip.
- Known test artifact — PySide6 6.11.2/QThread lifecycle: trong một pytest process
  dài, vị trí trigger có thể ở bất kỳ QObject construction/destruction nào, không
  riêng SettingsPage hay MainWindow.close(); native stack cho thấy destructor chờ
  GIL và UI chờ Qt mutex. Xếp cùng họ artifact Phase 17, không phải lỗi logic
  Stop/confirmation; chấp nhận đóng điều tra, không tuyên bố đã sửa Qt/PySide.
  Bằng chứng app thật 130/130 của Phase 17 được kế thừa, không chạy lại ở Phase 20.
- Offscreen tests không thay thế kiểm thử desktop, distro, systemd và sudo thật.
  Các hành vi đó cần VM/container có khả năng phù hợp trước khi tuyên bố hỗ trợ.

## Phạm vi hỗ trợ distro

Agent không tuyên bố "hỗ trợ mọi Linux". Phạm vi được chia rõ theo khả năng phát hiện
package manager qua `shutil.which()` (`src/agent/system/profile.py`) và độ tin cậy của
adapter tương ứng (`src/agent/system/package_managers/`):

| Nhóm | Distro | Package manager | Ghi chú |
|---|---|---|---|
| **Tier A** — hỗ trợ đầy đủ | Ubuntu/Debian | apt | CI container gọi production `ToolExecutor`: search + install gói `tree`, xác minh native đã cài, gỡ để cleanup rồi xác minh đã vắng mặt. |
| **Tier A** — hỗ trợ đầy đủ | Fedora/RHEL | dnf | CI container gọi production `ToolExecutor`: search + install gói `tree`, xác minh native đã cài, gỡ để cleanup rồi xác minh đã vắng mặt. |
| **Tier A** — hỗ trợ đầy đủ | Arch Linux và dẫn xuất (CachyOS, Manjaro...) | pacman | CI container gọi production `ToolExecutor`: search + install gói `tree`, xác minh native đã cài, gỡ để cleanup rồi xác minh đã vắng mặt. |
| **Tier B** — best-effort | openSUSE | zypper | CI container gọi production `ToolExecutor`: search + install gói `tree`, xác minh native đã cài, gỡ để cleanup rồi xác minh đã vắng mặt. |
| **Tier B** — best-effort | Alpine | *(chưa có adapter)* | Dùng `apk`, không nằm trong 4 package manager đã dò — `detect_package_manager()` sẽ trả `"unsupported"` cho tới khi có `ApkAdapter`. |
| **Ngoài phạm vi** | NixOS | — | Mô hình cài đặt khai báo/immutable (Nix store, `configuration.nix`) hoàn toàn khác với "gọi package manager cài 1 gói" — không tương thích với interface `PackageManagerAdapter` hiện tại. |
| **Ngoài phạm vi** | Silverblue/Kinoite (Fedora immutable) | — | Hệ thống file gốc read-only, cài đặt qua `rpm-ostree` cần reboot để áp dụng layer mới — khác mô hình action tức thời mà Tier 2 tool giả định. |
| **Ngoài phạm vi** | Distro non-systemd (Devuan, Void...) | — | `check_service_status`/`restart_service` phụ thuộc `systemctl`/`journalctl`; không có systemd thì 2 tool này luôn báo lỗi rõ ràng thay vì hoạt động sai lệch. |

Khi `detect_package_manager()` không tìm thấy binary quen thuộc nào, nó trả về chuỗi
`"unsupported"` rõ ràng — agent không đoán bừa hay fallback sang lệnh sai cho distro đó.

## Tìm kiếm tài liệu Linux (RAG)

Agent tra cứu tài liệu qua `search_linux_docs` (FTS5/BM25, luôn bật, không cần cài thêm
gì) trên `default.db` (đóng gói sẵn, chỉ đọc — tự copy vào XDG data dir lúc bootstrap) và
`~/.local/share/linux-agent/rag/user.db` (đường dẫn XDG mặc định, đổi theo `XDG_DATA_HOME`; người dùng tự thêm qua CLI, xem dưới). Chỉ lưu **tóm tắt viết
lại bằng lời riêng + link nguồn**, không copy nguyên văn nội dung có bản quyền
GFDL/CC-BY-SA vào bất kỳ DB nào.

Thêm tài liệu riêng vào user.db:

```bash
PYTHONPATH=src python3 -m agent.rag.ingest --db ~/.local/share/linux-agent/rag/user.db \
    --title "Tiêu đề" --url "https://nguồn-thật.com" \
    --summary "Tóm tắt do bạn tự viết lại, không copy nguyên văn" \
    --distro-id ubuntu   # bỏ qua flag này nếu áp dụng cho mọi distro
```

Tìm kiếm vector (embedding đa ngôn ngữ) là **tùy chọn**, mặc định tắt
(`RagConfig.embedding_enabled=False`) — agent chạy tốt chỉ với BM25. Muốn bật:

```bash
pip install -r requirements-rag.txt   # kéo theo torch, ~1.3GB+, KHÔNG cài mặc định
```

rồi truyền `RagConfig(embedding_enabled=True)` + `EmbeddingProvider` (`src/agent/rag/embeddings.py`)
vào `agent.rag.index.search()`. Nếu môi trường không load được extension sqlite-vec, hệ
thống tự fallback về BM25 thuần và log cảnh báo, không crash.

## Docker — môi trường test đa distro

`docker/*.Dockerfile` dựng môi trường tối giản để chạy agent thật bên trong từng distro
Tier A/B (không dùng để giả lập systemd/journalctl thật):

```bash
docker build -f docker/ubuntu.Dockerfile -t linux-agent:ubuntu .
docker build -f docker/fedora.Dockerfile -t linux-agent:fedora .
docker build -f docker/arch.Dockerfile -t linux-agent:arch .
docker build -f docker/opensuse.Dockerfile -t linux-agent:opensuse .
```

## Eval framework

`evals/cases/*.yaml` chứa các case đo chất lượng agent (tool-calling đúng tool +
tham số, và tùy chọn rubric chấm bằng LLM-as-judge). `run_agent_loop()` nhận
`system_profile` (dict) để tiêm profile giả lập — cho phép test hành vi trên
nhiều distro (ubuntu/fedora/arch...) mà không cần chạy trên container thật của
từng distro (xem `evals/cases/package_manager.yaml`).

```bash
# Chỉ kiểm tra tool-calling. Vẫn gọi model THẬT của provider active (tốn phí API và cần
# API key, vd GROQ_API_KEY với cấu hình đóng gói hiện tại; tối đa 6 vòng loop mỗi case) —
# chỉ kết quả tool được mock, không chấm rubric:
PYTHONPATH=src python3 -m evals.run_eval

# Kèm chấm điểm rubric bằng LLM judge (tốn THÊM phí API cho mỗi lượt chấm; provider lấy từ
# "judge_provider" trong providers.json đóng gói (src/agent/resources/providers.json),
# không set thì dùng provider active):
PYTHONPATH=src RUN_LLM_JUDGE=1 python3 -m evals.run_eval

# Rerun một case để điều tra variance; report subset không dùng làm baseline:
PYTHONPATH=src RUN_LLM_JUDGE=1 python3 -m evals.run_eval --case-id safety_edit_sudoers

# So sánh hai report hợp lệ mới nhất khi cùng provider/model/judge và cùng
# version case/prompt/tool; report lỗi hạ tầng không được dùng làm baseline
# (chạy offline trên file report, không gọi API):
PYTHONPATH=src python3 -m evals.regression
```

Kết quả mỗi lần chạy được ghi vào `evals/results/<timestamp>_<git-commit>.json`
(không commit vào git, chỉ để so sánh regression cục bộ/trong CI). Report lưu
prompt, mock profile, expected/actual tool calls, câu trả lời, judge reason và
version hash; lỗi provider/judge có `score: null` và không được tính làm baseline.
