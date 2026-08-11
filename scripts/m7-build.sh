#!/usr/bin/env bash
set -euo pipefail

readonly BUILDROOT_VERSION="2025.02.16"
readonly BUILDROOT_SHA256="15305e3d366eeaf4a5ecaf2ed42f685fd6af7fe5dbf1f62e1de5f46ee83225e2"
readonly BUILDROOT_URL="https://buildroot.org/downloads/buildroot-${BUILDROOT_VERSION}.tar.xz"
readonly LINUX_VERSION="6.12.27"

readonly REPO_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly WORK_DIR="${MICRONUX_M7_WORKDIR:-${HOME}/.cache/micronux/m7}"
readonly DOWNLOAD_DIR="${MICRONUX_DOWNLOAD_DIR:-${HOME}/.cache/micronux/m1/downloads}"
readonly ARCHIVE="${DOWNLOAD_DIR}/buildroot-${BUILDROOT_VERSION}.tar.xz"
readonly SOURCE_DIR="${WORK_DIR}/src/buildroot-${BUILDROOT_VERSION}"
readonly OUTPUT_DIR="${WORK_DIR}/output-${BUILDROOT_VERSION}"
readonly EXTERNAL_DIR="${REPO_DIR}/buildroot-external"
readonly DEFCONFIG="micronux_esp32p4_isolation_defconfig"
readonly ARTIFACT_DIR="${REPO_DIR}/out/m7"
readonly JOBS="${MICRONUX_JOBS:-$(nproc)}"

export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

required_tools=(awk bash bc bison cpio file find flex g++ gcc git gzip make patch perl python3 rsync sed sha256sum tar unzip wget xz)
missing_tools=()
for tool in "${required_tools[@]}"; do
	if ! command -v "${tool}" >/dev/null 2>&1; then
		missing_tools+=("${tool}")
	fi
done
if ((${#missing_tools[@]})); then
	printf 'Missing M7 host tools: %s\n' "${missing_tools[*]}" >&2
	exit 1
fi

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

# bFLT's link layout is part of the M7 hardware-protection contract.  Refresh
# every executable-producing package so no binary can retain a pre-W^X link.
make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	busybox-rebuild
make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	micronux-selftest-rebuild
make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	micronux-netctl-rebuild
make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	micronux-device-service-rebuild
make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	micronux-job-supervisor-rebuild
make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	micronux-isolation-test-rebuild

make -C "${SOURCE_DIR}" \
	O="${OUTPUT_DIR}" \
	BR2_EXTERNAL="${EXTERNAL_DIR}" \
	BR2_DL_DIR="${DOWNLOAD_DIR}/buildroot-dl" \
	-j"${JOBS}"

readonly IMAGE_DIR="${OUTPUT_DIR}/images"
readonly KERNEL_DIR="${OUTPUT_DIR}/build/linux-${LINUX_VERSION}"
readonly DTB="${IMAGE_DIR}/esp32p4-micronux.dtb"
readonly LINUX_PARTITION_SIZE=$((0x600000))

# Buildroot does not always invalidate an already-configured kernel when an
# out-of-tree DTS changes. Refresh the canonical board source and build its
# exact target explicitly so incremental builds cannot package a stale DTB.
install -m 0644 \
	"${EXTERNAL_DIR}/board/micronux/dts-m7/espressif/esp32p4-micronux.dts" \
	"${KERNEL_DIR}/arch/riscv/boot/dts/espressif/esp32p4-micronux.dts"
make -C "${KERNEL_DIR}" \
	ARCH=riscv \
	CROSS_COMPILE="${OUTPUT_DIR}/host/bin/riscv32-buildroot-linux-uclibc-" \
	-j"${JOBS}" \
	espressif/esp32p4-micronux.dtb
install -m 0644 \
	"${KERNEL_DIR}/arch/riscv/boot/dts/espressif/esp32p4-micronux.dtb" \
	"${DTB}"

readonly image_size="$(stat --format='%s' "${IMAGE_DIR}/Image")"
if ((image_size > LINUX_PARTITION_SIZE)); then
	printf 'M7 Image is too large for the Linux partition: %s > %s bytes\n' \
		"${image_size}" "${LINUX_PARTITION_SIZE}" >&2
	exit 1
fi

if ! grep -qx 'CONFIG_MICRONUX_ESP32P4_ISOLATION=y' "${KERNEL_DIR}/.config"; then
	printf 'M7 kernel did not retain CONFIG_MICRONUX_ESP32P4_ISOLATION.\n' >&2
	exit 1
fi
if ! grep -q 'console=ttyGS0,115200' "${KERNEL_DIR}/.config"; then
	printf 'M7 kernel did not retain the USB recovery console.\n' >&2
	exit 1
fi
if ! grep -q 'MICRONUX:M7:FB-CONSOLE state=ready' "${OUTPUT_DIR}/target/init" ||
	! grep -q 'boot_ready' "${OUTPUT_DIR}/target/init"; then
	printf 'M7 rootfs is missing the framebuffer status console.\n' >&2
	exit 1
fi
if ! grep -q 'DSI_HOST_FRAME_BTA_ACK_EN' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
	printf 'M7 kernel is missing the continuous-video frame-ACK policy.\n' >&2
	exit 1
fi
if ! grep -q 'DSI_HOST_LPCLK_CTRL' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'DSI_HOST_VID_MODE_CFG_ACT' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
	printf 'M7 kernel is missing the continuous-high-speed DSI policy.\n' >&2
	exit 1
fi
if ! grep -q 'MICRONUX_BACKLIGHT_OFF_SETTLE_MS' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c" ||
	! grep -q 'DEVICE_ATTR_WO(vpg_test_ms)' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
	printf 'M7 kernel is missing the dark-settle or bounded VPG contract.\n' >&2
	exit 1
fi
if ! grep -aq 'micronux,esp32p4-user-pool' "${DTB}"; then
	printf 'M7 DTB is missing the dedicated user-pool contract.\n' >&2
	exit 1
fi
if ! "${OUTPUT_DIR}/host/bin/dtc" -I dtb -O dts "${DTB}" 2>/dev/null |
	sed -n '/display@500a0000 {/,/};/p' |
	grep -q 'interrupts = <0x12>;'; then
	printf 'M7 DTB did not route the display GDMA interrupt to CLIC 18.\n' >&2
	exit 1
fi

python3 "${REPO_DIR}/scripts/m3-pack.py" \
	--image "${IMAGE_DIR}/Image" \
	--dtb "${DTB}" \
	--output "${ARTIFACT_DIR}/metadata.bin"

install -m 0644 "${IMAGE_DIR}/Image" "${ARTIFACT_DIR}/Image"
install -m 0644 "${DTB}" "${ARTIFACT_DIR}/esp32p4-micronux.dtb"
install -m 0644 "${IMAGE_DIR}/rootfs.cpio" "${ARTIFACT_DIR}/rootfs.cpio"
install -m 0644 "${KERNEL_DIR}/.config" "${ARTIFACT_DIR}/linux.config"
install -m 0644 "${KERNEL_DIR}/vmlinux" "${ARTIFACT_DIR}/vmlinux"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-selftest" \
	"${ARTIFACT_DIR}/micronux-selftest"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-exec-child" \
	"${ARTIFACT_DIR}/micronux-exec-child"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-netctl" \
	"${ARTIFACT_DIR}/micronux-netctl"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-device" \
	"${ARTIFACT_DIR}/micronux-device"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-device-native" \
	"${ARTIFACT_DIR}/micronux-device-native"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-device-selftest" \
	"${ARTIFACT_DIR}/micronux-device-selftest"
install -m 0755 "${OUTPUT_DIR}/target/usr/sbin/micronux-deviced" \
	"${ARTIFACT_DIR}/micronux-deviced"
install -m 0755 "${OUTPUT_DIR}/target/usr/sbin/micronux-run" \
	"${ARTIFACT_DIR}/micronux-run"
install -m 0755 "${OUTPUT_DIR}/target/usr/libexec/micronux-job-exec" \
	"${ARTIFACT_DIR}/micronux-job-exec"
install -m 0755 "${OUTPUT_DIR}/target/usr/libexec/micronux-job-test" \
	"${ARTIFACT_DIR}/micronux-job-test"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-storage-test" \
	"${ARTIFACT_DIR}/micronux-storage-test"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-isolation-probe" \
	"${ARTIFACT_DIR}/micronux-isolation-probe"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-isolation-fault" \
	"${ARTIFACT_DIR}/micronux-isolation-fault"
install -m 0755 "${OUTPUT_DIR}/target/usr/bin/micronux-arena-test" \
	"${ARTIFACT_DIR}/micronux-arena-test"

python3 "${REPO_DIR}/scripts/check-bflt-wx.py" \
	--tree "${OUTPUT_DIR}/target"

(
	cd "${ARTIFACT_DIR}"
	sha256sum Image esp32p4-micronux.dtb metadata.bin rootfs.cpio \
		micronux-selftest micronux-exec-child micronux-netctl \
		micronux-device micronux-device-native micronux-device-selftest \
		micronux-deviced micronux-run micronux-job-exec micronux-job-test \
		micronux-storage-test micronux-isolation-probe \
		micronux-isolation-fault micronux-arena-test \
		> SHA256SUMS
)

printf 'M7 isolated Linux build complete: %s\n' "${ARTIFACT_DIR}"
