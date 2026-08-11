#!/usr/bin/env bash
set -euo pipefail

readonly BUILDROOT_VERSION="2025.02.16"
readonly BUILDROOT_SHA256="15305e3d366eeaf4a5ecaf2ed42f685fd6af7fe5dbf1f62e1de5f46ee83225e2"
readonly BUILDROOT_URL="https://buildroot.org/downloads/buildroot-${BUILDROOT_VERSION}.tar.xz"
readonly LINUX_VERSION="6.12.27"

readonly REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly WORK_DIR="${MICRONUX_M9_WORKDIR:-${HOME}/.cache/micronux/m9}"
readonly DOWNLOAD_DIR="${MICRONUX_DOWNLOAD_DIR:-${HOME}/.cache/micronux/m1/downloads}"
readonly ARCHIVE="${DOWNLOAD_DIR}/buildroot-${BUILDROOT_VERSION}.tar.xz"
readonly SOURCE_DIR="${WORK_DIR}/src/buildroot-${BUILDROOT_VERSION}"
readonly OUTPUT_DIR="${WORK_DIR}/output-${BUILDROOT_VERSION}"
readonly EXTERNAL_DIR="${REPO_DIR}/buildroot-external"
readonly DEFCONFIG="micronux_esp32p4_gui_foundation_defconfig"
readonly ARTIFACT_DIR="${REPO_DIR}/out/m9"
readonly JOBS="${MICRONUX_JOBS:-$(nproc)}"
readonly BUILD_CONTRACT_FILE="${OUTPUT_DIR}/.micronux-m9-linux-contract"
readonly ARTIFACT_CONTRACT_FILE="${ARTIFACT_DIR}/SOURCE-CONTRACT"
readonly LINUX_PARTITION_SIZE=$((0x600000))

print_contract_only=false
if (($# > 1)); then
	printf 'Usage: %s [--print-contract]\n' "$0" >&2
	exit 2
fi
if (($# == 1)); then
	if [[ "$1" != "--print-contract" ]]; then
		printf 'Unknown M9 build option: %s\n' "$1" >&2
		exit 2
	fi
	print_contract_only=true
fi

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export LC_ALL=C

required_tools=(awk bash bc bison cpio file find flex g++ gcc git gzip make patch perl python3 rsync sed sha256sum sort tar unzip wget xargs xz)
missing_tools=()
for tool in "${required_tools[@]}"; do
	if ! command -v "${tool}" >/dev/null 2>&1; then
		missing_tools+=("${tool}")
	fi
done
if ((${#missing_tools[@]})); then
	printf 'Missing M9 host tools: %s\n' "${missing_tools[*]}" >&2
	exit 1
fi

patch_evidence="$(python3 "${REPO_DIR}/scripts/check-linux-patch-series.py")"
build_contract="$({
	printf '%s\n' "${patch_evidence}"
	(
		cd "${REPO_DIR}"
		find buildroot-external -type f -print0 |
			sort -z |
			xargs -0 sha256sum
		sha256sum scripts/m3-pack.py scripts/m9-build.sh
	)
} | sha256sum | awk '{print $1}')"
if ${print_contract_only}; then
	printf 'MICRONUX:M9:SOURCE-CONTRACT sha256=%s\n' "${build_contract}"
	exit 0
fi
printf '%s\n' "${patch_evidence}"

mkdir -p "${DOWNLOAD_DIR}" "${WORK_DIR}/src" "${OUTPUT_DIR}" "${ARTIFACT_DIR}"
if [[ ! -f "${ARCHIVE}" ]]; then
	wget --no-verbose --output-document="${ARCHIVE}.part" "${BUILDROOT_URL}"
	mv "${ARCHIVE}.part" "${ARCHIVE}"
fi

archive_hash="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${archive_hash}" != "${BUILDROOT_SHA256}" ]]; then
	printf 'Buildroot archive checksum mismatch.\nExpected: %s\nActual:   %s\n' \
		"${BUILDROOT_SHA256}" "${archive_hash}" >&2
	exit 1
fi

if [[ ! -d "${SOURCE_DIR}" ]]; then
	tar --extract --xz --directory="${WORK_DIR}/src" --file="${ARCHIVE}"
fi
if [[ ! -f "${SOURCE_DIR}/Makefile" ]] ||
	! grep -q "BR2_VERSION := ${BUILDROOT_VERSION}" "${SOURCE_DIR}/Makefile"; then
	printf 'Buildroot source directory is incomplete: %s\n' "${SOURCE_DIR}" >&2
	exit 1
fi

make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	"${DEFCONFIG}"

if [[ -f "${OUTPUT_DIR}/build/linux-${LINUX_VERSION}/.stamp_patched" ]] &&
	[[ ! -f "${BUILD_CONTRACT_FILE}" ||
	   "$(cat "${BUILD_CONTRACT_FILE}")" != "${build_contract}" ]]; then
	make -C "${SOURCE_DIR}" \
		O="${OUTPUT_DIR}" \
		BR2_EXTERNAL="${EXTERNAL_DIR}" \
		BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
		linux-dirclean
fi

# Force executable-producing local packages through the current bFLT W^X
# linker contract on every incremental M9 build.
for package in \
	busybox \
	micronux-selftest \
	micronux-netctl \
	micronux-device-service \
	micronux-job-supervisor \
	micronux-isolation-test \
	micronux-display-test; do
	make -C "${SOURCE_DIR}" \
		O="${OUTPUT_DIR}" \
		BR2_EXTERNAL="${EXTERNAL_DIR}" \
		BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
		"${package}-rebuild"
done

make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	-j"${JOBS}"

readonly IMAGE_DIR="${OUTPUT_DIR}/images"
readonly KERNEL_DIR="${OUTPUT_DIR}/build/linux-${LINUX_VERSION}"
readonly DTB="${IMAGE_DIR}/esp32p4-micronux.dtb"

# Buildroot does not always invalidate an already-configured kernel when an
# out-of-tree DTS changes. Refresh and compile the exact board target.
install -m 0644 \
	"${EXTERNAL_DIR}/board/micronux/dts-m9/espressif/esp32p4-micronux.dts" \
	"${KERNEL_DIR}/arch/riscv/boot/dts/espressif/esp32p4-micronux.dts"
make -C "${KERNEL_DIR}" \
	ARCH=riscv \
	CROSS_COMPILE="${OUTPUT_DIR}/host/bin/riscv32-buildroot-linux-uclibc-" \
	-j"${JOBS}" \
	espressif/esp32p4-micronux.dtb
install -m 0644 \
	"${KERNEL_DIR}/arch/riscv/boot/dts/espressif/esp32p4-micronux.dtb" \
	"${DTB}"

image_size="$(stat --format='%s' "${IMAGE_DIR}/Image")"
if ((image_size > LINUX_PARTITION_SIZE)); then
	printf 'M9 Image is too large for the Linux partition: %s > %s bytes\n' \
		"${image_size}" "${LINUX_PARTITION_SIZE}" >&2
	exit 1
fi

for required_config in \
	CONFIG_MICRONUX_ESP32P4_ISOLATION=y \
	CONFIG_INPUT=y \
	CONFIG_INPUT_EVDEV=y; do
	if ! grep -qx "${required_config}" "${KERNEL_DIR}/.config"; then
		printf 'M9 kernel is missing %s.\n' "${required_config}" >&2
		exit 1
	fi
done
if ! grep -q 'console=ttyGS0,115200' "${KERNEL_DIR}/.config"; then
	printf 'M9 kernel did not retain the USB recovery console.\n' >&2
	exit 1
fi
if ! grep -q 'reason=status-render' "${OUTPUT_DIR}/target/init" ||
	! grep -q 'boot_ready' "${OUTPUT_DIR}/target/init"; then
	printf 'M9 rootfs is missing the display reveal gate.\n' >&2
	exit 1
fi
if ! grep -q 'DSI_HOST_FRAME_BTA_ACK_EN' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
	printf 'M9 kernel is missing the continuous-video frame-ACK policy.\n' >&2
	exit 1
fi
if ! grep -q 'DSI_HOST_LPCLK_CTRL' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'DSI_HOST_VID_MODE_CFG_ACT' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
	printf 'M9 kernel is missing the continuous-high-speed DSI policy.\n' >&2
	exit 1
fi
if ! grep -q 'MICRONUX_BACKLIGHT_OFF_SETTLE_MS' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'DEVICE_ATTR_WO(vpg_test_ms)' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
	printf 'M9 kernel is missing the dark-settle or bounded VPG contract.\n' >&2
	exit 1
fi
if ! grep -q 'micronux_visible_source_valid' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'MICRONUX:M9:DISPLAY-FAULT state=contained' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'DSI_HOST_INT_STATUS1_FATAL' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'DSI_HOST_INT_STATUS1_DPI_PATH' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'DSI_HOST_INT_STATUS1_FB_STARTUP_GRACE' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'vpg_dpi_status1' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'MICRONUX_FAULT_STALE_FRAME' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'MICRONUX_STALE_POLL_LIMIT' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'watchdog_sar' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'health_poll_ms=50' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'micronux_discard_host_status' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
	printf 'M9 kernel is missing fail-dark display fault containment.\n' >&2
	exit 1
fi
for transition_marker in \
	'transition=dark-switched-revealed' \
	'transition=dark-switched-primed-revealed' \
	'source=restore-requested'; do
	if ! grep -q "${transition_marker}" \
		"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
		printf 'M9 kernel is missing display transition marker: %s\n' \
			"${transition_marker}" >&2
		exit 1
	fi
done
if [[ ! -x "${OUTPUT_DIR}/target/usr/bin/micronux-display-test" ]]; then
	printf 'M9 rootfs is missing micronux-display-test.\n' >&2
	exit 1
fi
if grep -Eq '^BR2_PACKAGE_LVGL[^=]*=y$' "${OUTPUT_DIR}/.config" ||
	find "${OUTPUT_DIR}/target" -iname '*lvgl*' -print -quit | grep -q .; then
	printf 'M9.1 foundation unexpectedly contains an LVGL artifact.\n' >&2
	exit 1
fi

dt_source="$(${OUTPUT_DIR}/host/bin/dtc -I dtb -O dts "${DTB}" 2>/dev/null)"
display_node="$(sed -n '/display@500a0000 {/,/};/p' <<<"${dt_source}")"
for property in \
	'espressif,touch-address = <0x5d>;' \
	'espressif,touch-poll-ms = <0x0a>;' \
	'touchscreen-size-x = <0x320>;' \
	'touchscreen-size-y = <0x500>;'; do
	if ! grep -q "${property}" <<<"${display_node}"; then
		printf 'M9 DTB display node is missing: %s\n' "${property}" >&2
		exit 1
	fi
done

python3 "${REPO_DIR}/scripts/m3-pack.py" \
	--image "${IMAGE_DIR}/Image" \
	--dtb "${DTB}" \
	--output "${ARTIFACT_DIR}/metadata.bin"

install -m 0644 "${IMAGE_DIR}/Image" "${ARTIFACT_DIR}/Image"
install -m 0644 "${DTB}" "${ARTIFACT_DIR}/esp32p4-micronux.dtb"
install -m 0644 "${IMAGE_DIR}/rootfs.cpio" "${ARTIFACT_DIR}/rootfs.cpio"
install -m 0644 "${KERNEL_DIR}/.config" "${ARTIFACT_DIR}/linux.config"
install -m 0644 "${KERNEL_DIR}/vmlinux" "${ARTIFACT_DIR}/vmlinux"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-display-test" \
	"${ARTIFACT_DIR}/micronux-display-test"

python3 "${REPO_DIR}/scripts/check-bflt-wx.py" \
	--tree "${OUTPUT_DIR}/target"

(
	cd "${ARTIFACT_DIR}"
	sha256sum Image esp32p4-micronux.dtb metadata.bin rootfs.cpio \
		linux.config vmlinux micronux-display-test > SHA256SUMS
)
printf '%s\n' "${build_contract}" > "${BUILD_CONTRACT_FILE}"
printf '%s\n' "${build_contract}" > "${ARTIFACT_CONTRACT_FILE}"

printf 'MICRONUX:M9:BUILD state=pass image=%s limit=%s lvgl=absent contract=%s artifacts=%s\n' \
	"${image_size}" "${LINUX_PARTITION_SIZE}" "${build_contract}" "${ARTIFACT_DIR}"
