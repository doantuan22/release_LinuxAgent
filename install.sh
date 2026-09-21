#!/usr/bin/env bash
# Linux Agent — cài đặt một lệnh.
#
#   curl -sSL https://raw.githubusercontent.com/doantuan22/release_LinuxAgent/main/install.sh | bash
#
# KHÔNG chạy toàn bộ script bằng root/`sudo bash`: script tự gọi sudo đúng ở dòng cần
# quyền hệ thống (cài pipx bằng package manager). Chạy lại nhiều lần an toàn (idempotent):
# đã cài rồi thì chuyển sang `pipx upgrade`.
#
# Mã thoát: 0 xong | 10 không phải Linux | 11 môi trường ngoài phạm vi hỗ trợ
#           12 thiếu/quá cũ Python | 13 không cài được pipx | 14 cài/nâng cấp thất bại
#           15 cài xong nhưng `agent --help` không chạy được

set -uo pipefail

# ─── Cấu hình: đổi Git ↔ PyPI ở đây, không sửa logic bên dưới (spec mục 4.3) ─────────
PACKAGE_NAME="linux-agent"
# Giai đoạn demo (chưa publish PyPI).
# Sau khi publish PyPI: PACKAGE_SOURCE="${PACKAGE_NAME}". Có thể ghi đè bằng biến môi trường.
PACKAGE_SOURCE="${PACKAGE_SOURCE:-git+https://github.com/doantuan22/release_LinuxAgent.git@v0.1.0}"
# Phải bằng `requires-python` trong pyproject.toml.
MIN_PYTHON="3.11"
# ──────────────────────────────────────────────────────────────────────────────────────

TOTAL_STEPS=6

log()  { printf '[install.sh] %s\n' "$*"; }
step() { printf '\n[install.sh] Bước %s/%s: %s\n' "$1" "$TOTAL_STEPS" "$2"; }
warn() { printf '[install.sh] CẢNH BÁO: %s\n' "$*" >&2; }
die() {
    local code="$1"; shift
    printf '\n[install.sh] LỖI: %s\n' "$*" >&2
    exit "$code"
}

have() { command -v "$1" >/dev/null 2>&1; }

# Các lệnh ngoài (ngoài builtin của bash) mà script gọi. Thiếu thì báo rõ ngay, thay vì để một pipeline
# lỗi âm thầm rồi đi nhầm nhánh.
require_tools() {
    local tool
    for tool in uname id grep cat; do
        have "$tool" || die 11 "Thiếu lệnh '$tool' mà install.sh cần (môi trường quá tối giản)."
    done
}

# Chạy lệnh cần quyền hệ thống: root thì chạy thẳng, còn lại dùng sudo (OS tự hỏi mật khẩu).
run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@" </dev/null
    elif have sudo; then
        sudo "$@" </dev/null    # mật khẩu sudo đọc từ /dev/tty, không phải stdin (tránh nuốt script khi `curl | bash`)
    else
        die 13 "Cần quyền root để chạy: $* — hãy cài sudo hoặc chạy lại bằng tài khoản root."
    fi
}

detect_package_manager() {
    # Theo lệnh thực sự có mặt, không đoán theo tên distro.
    local pm
    for pm in apt-get dnf pacman zypper; do
        if have "$pm"; then printf '%s' "$pm"; return 0; fi
    done
    return 1
}

in_container() {
    [ -f /.dockerenv ] || [ -f /run/.containerenv ] \
        || grep -qsE 'docker|lxc|containerd|kubepods|libpod' /proc/1/cgroup
}

# Mục 5 của spec: NixOS, Silverblue/Kinoite, non-systemd — báo rõ "không hỗ trợ" thay vì lỗi mập mờ.
check_supported_environment() {
    if [ -e /etc/NIXOS ] || grep -qsE '^ID="?nixos"?$' /etc/os-release; then
        die 11 "NixOS nằm ngoài phạm vi hỗ trợ (mô hình cài đặt khai báo/immutable, không tương thích)."
    fi
    if have rpm-ostree && [ -e /run/ostree-booted ]; then
        die 11 "Fedora Silverblue/Kinoite (immutable, rpm-ostree) nằm ngoài phạm vi hỗ trợ."
    fi
    # Container không chạy systemd làm PID 1 nhưng vẫn là môi trường test hợp lệ.
    if ! in_container && [ "$(cat /proc/1/comm 2>/dev/null)" != "systemd" ]; then
        die 11 "Không phát hiện systemd (PID 1 là '$(cat /proc/1/comm 2>/dev/null)'). Distro non-systemd nằm ngoài phạm vi hỗ trợ."
    fi
}

python_install_hint() {
    case "$(detect_package_manager || true)" in
        apt-get) echo "  sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip" ;;
        dnf)     echo "  sudo dnf install -y python3 python3-pip" ;;
        pacman)  echo "  sudo pacman -S --needed python python-pip" ;;
        zypper)  echo "  sudo zypper install python311 python311-pip   (hoặc bản Python >= ${MIN_PYTHON} mới hơn của distro)" ;;
        *)       echo "  Cài Python >= ${MIN_PYTHON} bằng package manager của distro, hoặc từ https://www.python.org/downloads/" ;;
    esac
}

step_check_os() {
    step 1 "Kiểm tra hệ điều hành"
    local kernel
    kernel="$(uname -s)"
    [ "$kernel" = "Linux" ] || die 10 "Chỉ hỗ trợ Linux (phát hiện: ${kernel}). macOS/WSL không thuộc phạm vi demo."
    check_supported_environment
    log "Linux — OK."
}

step_check_python() {
    step 2 "Kiểm tra Python (cần >= ${MIN_PYTHON})"
    if ! have python3; then
        die 12 "Không tìm thấy python3. Hãy cài Python >= ${MIN_PYTHON} rồi chạy lại:
$(python_install_hint)"
    fi
    local major="${MIN_PYTHON%%.*}" minor="${MIN_PYTHON#*.}"
    if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (${major}, ${minor}) else 1)" </dev/null; then
        die 12 "python3 hiện tại là $(python3 --version 2>&1), cần >= ${MIN_PYTHON}. Hãy cài bản mới hơn rồi chạy lại:
$(python_install_hint)"
    fi
    log "$(python3 --version 2>&1) — OK."
}

# pipx chưa nằm trong PATH ngay sau `pip install --user` → dùng `python3 -m pipx` cho tới khi PATH cập nhật.
resolve_pipx() {
    if have pipx; then PIPX=(pipx); else PIPX=(python3 -m pipx); fi
}

install_pipx_with_package_manager() {
    case "$(detect_package_manager || true)" in
        apt-get)
            # `apt-get update` bắt buộc: máy/container mới chưa có danh sách gói.
            run_root env DEBIAN_FRONTEND=noninteractive apt-get update \
                && run_root env DEBIAN_FRONTEND=noninteractive apt-get install -y pipx ;;
        dnf)    run_root dnf install -y pipx ;;
        pacman) run_root pacman -S --needed --noconfirm python-pipx ;;
        zypper) run_root zypper --non-interactive install -y python3-pipx ;;
        *)      return 1 ;;
    esac
}

install_pipx_with_pip() {
    have python3 && python3 -m pip --version >/dev/null 2>&1 \
        || die 13 "Không cài được pipx bằng package manager và python3 chưa có pip. Hãy cài python3-pip rồi chạy lại:
$(python_install_hint)"
    local output
    # PEP 668 (externally-managed-environment): cờ này bắt buộc và được ghi tường minh, không nuốt lỗi.
    if output="$(python3 -m pip install --user pipx --break-system-packages 2>&1 </dev/null)"; then
        printf '%s\n' "$output"
    elif grep -qi 'no such option' <<<"$output"; then
        warn "pip quá cũ, chưa có --break-system-packages (cũng chưa áp dụng PEP 668) — thử lại không có cờ này."
        python3 -m pip install --user pipx </dev/null || die 13 "pip install --user pipx thất bại."
    else
        printf '%s\n' "$output" >&2
        die 13 "pip install --user pipx --break-system-packages thất bại (chi tiết ở trên)."
    fi
}

step_ensure_pipx() {
    step 3 "Kiểm tra pipx"
    if have pipx; then
        log "pipx đã có — bỏ qua bước cài."
        step 4 "Cài pipx"; log "Bỏ qua (đã có pipx)."
        resolve_pipx
        return
    fi
    step 4 "Cài pipx"
    if install_pipx_with_package_manager; then
        log "Đã cài pipx bằng package manager."
    else
        warn "Không cài được pipx bằng package manager (không có gói hoặc không nhận diện được) — dùng pip --user."
        install_pipx_with_pip
    fi
    resolve_pipx
    "${PIPX[@]}" --version </dev/null >/dev/null 2>&1 || die 13 "Đã cài pipx nhưng không chạy được '${PIPX[*]} --version'."
    "${PIPX[@]}" ensurepath </dev/null || warn "pipx ensurepath báo lỗi; bạn có thể cần tự thêm thư mục bin của pipx vào PATH."
}

pipx_bin_dir() {
    local dir
    dir="$("${PIPX[@]}" environment --value PIPX_BIN_DIR 2>/dev/null </dev/null)" || dir=""
    printf '%s' "${dir:-${PIPX_BIN_DIR:-$HOME/.local/bin}}"
}

package_is_installed() {
    # Thuần bash, KHÔNG dùng awk/tr: image tối giản (vd openSUSE Tumbleweed) không có awk, và một pipeline
    # lỗi vì thiếu lệnh sẽ bị hiểu nhầm là "chưa cài" → đi nhầm nhánh install thay vì upgrade.
    local listing name rest
    listing="$("${PIPX[@]}" list --short </dev/null 2>/dev/null)" || return 1
    while read -r name rest; do
        name="${name,,}"; name="${name//_/-}"    # chuẩn hoá PEP 503: chữ thường, '_' → '-'
        [ "$name" = "$PACKAGE_NAME" ] && return 0
    done <<<"$listing"
    return 1
}

step_install_package() {
    step 5 "Cài / nâng cấp ${PACKAGE_NAME}"
    if package_is_installed; then
        log "${PACKAGE_NAME} đã được cài — nâng cấp."
        "${PIPX[@]}" upgrade "$PACKAGE_NAME" </dev/null \
            || die 14 "'${PIPX[*]} upgrade ${PACKAGE_NAME}' thất bại (chi tiết ở trên)."
    else
        log "Cài từ: ${PACKAGE_SOURCE}"
        "${PIPX[@]}" install "$PACKAGE_SOURCE" </dev/null \
            || die 14 "'${PIPX[*]} install ${PACKAGE_SOURCE}' thất bại (chi tiết ở trên)."
    fi
}

step_verify() {
    step 6 "Xác nhận cài đặt"
    local bin_dir agent_bin
    bin_dir="$(pipx_bin_dir)"
    if have agent; then agent_bin="$(command -v agent)"; else agent_bin="${bin_dir}/agent"; fi
    if ! "$agent_bin" --help </dev/null >/dev/null 2>&1; then
        die 15 "Đã cài nhưng '${agent_bin} --help' không chạy được. Thử: ${PIPX[*]} list  và  ${PIPX[*]} reinstall ${PACKAGE_NAME}"
    fi
    log "Cài đặt hoàn tất: ${agent_bin}"
    if ! have agent; then
        log "'agent' chưa có trong PATH của terminal này. Mở lại terminal, hoặc chạy:  export PATH=\"${bin_dir}:\$PATH\""
    fi
    log "Bước tiếp theo:  agent doctor"
}

main() {
    if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
        warn "Đang chạy bằng sudo: pipx sẽ cài cho root chứ không phải '${SUDO_USER}'. Nên chạy lại KHÔNG dùng sudo."
    fi
    case "$PACKAGE_SOURCE" in
        *'<user>'*|*'<repo>'*)
            die 14 "PACKAGE_SOURCE vẫn còn placeholder <user>/<repo>: '${PACKAGE_SOURCE}'.
Hãy điền URL Git thật ở đầu install.sh, hoặc chạy:  PACKAGE_SOURCE=<nguồn> bash install.sh" ;;
        git+*)
            have git || die 14 "PACKAGE_SOURCE là Git nhưng chưa cài git. Cài git rồi chạy lại." ;;
    esac
    require_tools
    step_check_os
    step_check_python
    step_ensure_pipx
    step_install_package
    step_verify
}

# Đặt ở dòng cuối: bash phải đọc trọn vẹn script (kể cả khi chạy qua `curl | bash`) trước khi
# thực thi bất kỳ lệnh nào, nên tải dở dang không thể chạy nửa chừng.
main "$@"
