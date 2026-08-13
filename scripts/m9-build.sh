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
readonly KERNEL_LOAD_ADDRESS=$((0x48400000))
readonly DISPLAY_POOL_ADDRESS=$((0x49300000))

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

required_tools=(awk bash bc bison cmp cpio file find flex g++ gcc git gzip make patch perl python3 rsync sed sha256sum sort tar unzip wget xargs xz)
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
model_evidence="$(python3 "${REPO_DIR}/scripts/m9-2-display-contract-test.py")"
build_contract="$({
	printf '%s\n' "${patch_evidence}"
	printf '%s\n' "${model_evidence}"
	(
		cd "${REPO_DIR}"
		git ls-files -z --cached --others --exclude-standard -- \
			buildroot-external |
			sort -z |
			xargs -0 sha256sum
		sha256sum scripts/m3-pack.py scripts/m9-build.sh \
			scripts/m9-2-display-contract-test.py \
			scripts/check-linux-patch-series.py
	)
} | sha256sum | awk '{print $1}')"
if ${print_contract_only}; then
	printf 'MICRONUX:M9:SOURCE-CONTRACT sha256=%s\n' "${build_contract}"
	exit 0
fi
printf '%s\n' "${patch_evidence}"
printf '%s\n' "${model_evidence}"

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

linux_build_dir="${OUTPUT_DIR}/build/linux-${LINUX_VERSION}"
if [[ -d "${linux_build_dir}" ]] &&
	{ [[ ! -f "${linux_build_dir}/.stamp_patched" ]] ||
	  [[ ! -f "${BUILD_CONTRACT_FILE}" ||
	     "$(cat "${BUILD_CONTRACT_FILE}")" != "${build_contract}" ]]; }; then
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
	micronux-display-test \
	micronux-jd9365-firmware; do
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
if ((KERNEL_LOAD_ADDRESS + image_size > DISPLAY_POOL_ADDRESS)); then
	printf 'M9 Image overlaps the protected display pool: end=0x%x pool=0x%x\n' \
		"$((KERNEL_LOAD_ADDRESS + image_size))" "${DISPLAY_POOL_ADDRESS}" >&2
	exit 1
fi

for required_config in \
	CONFIG_MICRONUX_ESP32P4_ISOLATION=y \
	CONFIG_FW_LOADER=y \
	CONFIG_INPUT=y \
	CONFIG_INPUT_EVDEV=y \
	CONFIG_FB_ESP32P4_MICRONUX_DSI=y \
	CONFIG_BACKLIGHT_CLASS_DEVICE=y \
	CONFIG_CRC32=y; do
	if ! grep -qx "${required_config}" "${KERNEL_DIR}/.config"; then
		printf 'M9 kernel is missing %s.\n' "${required_config}" >&2
		exit 1
	fi
done
if ! grep -q 'console=ttyGS0,115200' "${KERNEL_DIR}/.config"; then
	printf 'M9 kernel did not retain the USB recovery console.\n' >&2
	exit 1
fi
if ! cmp -s \
	"${EXTERNAL_DIR}/board/micronux/rootfs-m6-combined/init" \
	"${OUTPUT_DIR}/target/init"; then
	printf 'M9 installed init does not match the validated source.\n' >&2
	exit 1
fi
if ! grep -q 'MICRONUX:M9.2:COLD-BOOT state=trigger action=cold_init' \
		"${OUTPUT_DIR}/target/init" ||
	! grep -q 'SCANOUT_QUALIFIED_QUIESCENT' \
		"${OUTPUT_DIR}/target/init" ||
	! grep -q 'reason=framebuffer-device-timeout' \
		"${OUTPUT_DIR}/target/init" ||
	! grep -q 'reason=status-render' "${OUTPUT_DIR}/target/init" ||
	! grep -q 'boot_ready' "${OUTPUT_DIR}/target/init" ||
	! grep -q 'reason=display-reveal mode=headless' \
		"${OUTPUT_DIR}/target/init"; then
	printf 'M9 rootfs is missing the native cold-init/reveal gate.\n' >&2
	exit 1
fi
readonly DISPLAY_DRIVER="${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"
readonly MMC_DRIVER="${KERNEL_DIR}/drivers/mmc/host/dw_mmc.c"
readonly EARLY_USB_CONSOLE="${KERNEL_DIR}/drivers/tty/serial/earlycon-esp32p4.c"
readonly USB_CONSOLE="${KERNEL_DIR}/drivers/tty/serial/esp32_acm.c"
readonly NATIVE_DISPLAY_DRIVER_SHA256="c91efedfabd6c0db4beea4e4118b5206ea29181aaf3cbf87b34d697910858f71"
display_driver_sha256="$(sha256sum "${DISPLAY_DRIVER}" | awk '{print $1}')"
if [[ "${display_driver_sha256}" != "${NATIVE_DISPLAY_DRIVER_SHA256}" ]]; then
	printf 'M9 kernel display source does not match the audited fixed-front reload source: %s\n' \
		"${display_driver_sha256}" >&2
	exit 1
fi
early_usb_putc="$(sed -n '/^static void esp32p4_earlycon_putc(/,/^}/p' \
	"${EARLY_USB_CONSOLE}" | tr '\n\t' '  ' | tr -s ' ')"
usb_console_putc="$(sed -n '/^static void esp32s3_acm_put_char_sync(/,/^}/p' \
	"${USB_CONSOLE}" | tr '\n\t' '  ' | tr -s ' ')"
if ! grep -Fq \
	'if (!(readl_relaxed(port->membase + ESP32P4_USB_SERIAL_CONF) & ESP32P4_USB_SERIAL_DATA_FREE)) return;' \
	<<<"${early_usb_putc}" ||
	grep -Fq 'while (' <<<"${early_usb_putc}" ||
	! grep -Fq \
	'if (!esp32s3_acm_tx_fifo_free(port)) return;' \
	<<<"${usb_console_putc}" ||
	grep -Eq 'while \(|jiffies|time_after' <<<"${usb_console_putc}"; then
	printf 'M9 USB console output is not fail-open when COM14 is closed.\n' >&2
	exit 1
fi
mmc_slot_init_block="$(sed -n '/^static int dw_mci_init_slots(/,/^}/p' \
	"${MMC_DRIVER}")"
mmc_slot_init_compact="$(tr '\n\t' '  ' <<<"${mmc_slot_init_block}" | tr -s ' ')"
for mmc_isolation_marker in \
	'if (!host->num_slots || (host->num_slots != ARRAY_SIZE(host->slots) && !of_property_read_bool(host->dev->of_node, "espressif,allow-single-slot")))' \
	'ESP32-P4 dual-slot arbitration enabled with %u active slot(s)'; do
	if ! grep -Fq "${mmc_isolation_marker}" <<<"${mmc_slot_init_compact}"; then
		printf 'M9 kernel is missing isolated-slot support: %s\n' \
			"${mmc_isolation_marker}" >&2
		exit 1
	fi
done

for preparation_marker in \
	'MICRONUX:M9.2:CONTINUOUS-PREP state=VALIDATED active-scanout=one-shot hardware-writes=0' \
	'struct micronux_native_reload_plan' \
	'micronux_native_prepare_reload_plan(dsi, &reload_plan);'; do
	if ! grep -Fq "${preparation_marker}" "${DISPLAY_DRIVER}"; then
		printf 'M9 kernel is missing continuous preparation evidence: %s\n' \
			"${preparation_marker}" >&2
		exit 1
	fi
done
# These exact programmed-register component masks derive video policy
# 0x0000ff02 and LPCLK 0x00000003 in the audited ABI-v3 implementation.
# The active-mirror components remain diagnostic-only.
for native_marker in \
	'MICRONUX_DISPLAY_ABI_VERSION_V3[[:space:]]+3' \
	'MICRONUX_NATIVE_DMA_DESCRIPTOR_CTRL_HI[[:space:]]+0x40108840' \
	'MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED[[:space:]]+0xc0108840' \
	'DSI_HOST_VID_MODE_BURST[[:space:]]+2' \
	'DSI_HOST_FRAME_BTA_ACK_EN[[:space:]]+BIT\(14\)' \
	'DSI_HOST_LP_VIDEO_EN_MASK[[:space:]]+\(GENMASK\(13, 8\) \| BIT\(15\)\)' \
	'DSI_HOST_FRAME_BTA_ACK_EN_ACT[[:space:]]+BIT\(8\)' \
	'DSI_HOST_LP_VIDEO_EN_ACT_MASK[[:space:]]+\(GENMASK\(7, 2\) \| BIT\(9\)\)' \
	'DSI_HOST_PHY_TXREQUESTCLKHS[[:space:]]+BIT\(0\)' \
	'DSI_HOST_AUTO_CLKLANE_CTRL[[:space:]]+BIT\(1\)' \
	'writel\(DSI_HOST_NATIVE_VIDEO_POLICY,' \
	'readl\(dsi->host \+ DSI_HOST_VID_MODE_CFG\) ==' \
	'video-policy=0000ff02 frame-bta=enabled .*lpclk=00000003' \
	'state=SCANOUT_QUALIFIED_QUIESCENT fb=fb%d registered=yes' \
	'ctrl-hi=c0108840 host=video frame-bta=enabled lp=all lpclk=00000003' \
	'state=RUNTIME_REVEALED boot-ready=1 source=userspace-status' \
	'frames=4 same-front=yes scanout-mode=continuous-fixed-front cpu-rearm=disabled control-command=0x17-acked pwm-command=63-acked' \
	'MICRONUX_DISPLAY_STATE_FAILED_QUIESCENT' \
	'MICRONUX_DISPLAY_STATE_FAILED_UNVERIFIED' \
	'irq_data = irq_get_irq_data\(dsi->irq\)' \
	'irqd_to_hwirq\(irq_data\) != handoff->gdma_clic_irq' \
	'reason=hp-clock-reset-readback' \
	'reason=request-irq error=%d linux-virq=%d hwirq=%lu' \
	'reason=register-readback cfg=%08x cfglo=%08x cfghi=%08x' \
	'expected=00000001:0000000f:0a020001:023f7fe2:0000000f:023f7fe2:0000000f:001fff8f:001fff8f:chen\[3:0\]=0' \
	'micronux_native_fail_reveal_locked' \
	'native backlight rollback was not fully ACKed' \
	'source=preserved scanout=%s darkness=not-confirmed'; do
	if ! grep -Eq "${native_marker}" "${DISPLAY_DRIVER}"; then
		printf 'M9 kernel is missing native ABI-v3 marker: %s\n' \
		"${native_marker}" >&2
		exit 1
	fi
done
if grep -Fq 'handoff->gdma_clic_irq != dsi->irq' "${DISPLAY_DRIVER}"; then
	printf 'M9 kernel compares raw CLIC hwirq with Linux virq.\n' >&2
	exit 1
fi

# P4 rev 1.3 GDMA semantic masks are status0=0xfa3f7ffb,
# status1=0x0000000f, and common=0x001fff8f.  The enabled expectations are
# 0x023f7fe2/0x0000000f/0x001fff8f; teardown retains only
# 0x02000000/0x0000000f/0x001ffe80 on documented fields.
for gdma_mask_marker in \
	'^#define DW_GDMA_INT_STATUS0_DEFINED_MASK \\' \
	'^[[:space:]]+\(GENMASK\(1, 0\) \| GENMASK\(14, 3\) \| GENMASK\(21, 16\) \| BIT\(25\) \| \\$' \
	'^[[:space:]]+GENMASK\(31, 27\)\)$' \
	'^#define DW_GDMA_INT_STATUS1_DEFINED_MASK[[:space:]]+GENMASK\(3, 0\)$' \
	'^#define DW_GDMA_INT_COMMON_DEFINED_MASK[[:space:]]+DW_GDMA_INT_COMMON_VALID_MASK$' \
	'^#define DW_GDMA_INT_STATUS0_RO_MASK[[:space:]]+BIT\(25\)$' \
	'^#define DW_GDMA_INT_ECC_ERROR_MASK[[:space:]]+GENMASK\(3, 0\)$' \
	'^#define DW_GDMA_INT_COMMON_VALID_MASK[[:space:]]+0x001fff8f$' \
	'^#define DW_GDMA_INT_COMMON_RO_MASK[[:space:]]+0x001ffe80$'; do
	if ! grep -Eq "${gdma_mask_marker}" "${DISPLAY_DRIVER}"; then
		printf 'M9 kernel is missing GDMA semantic-mask marker: %s\n' \
			"${gdma_mask_marker}" >&2
		exit 1
	fi
done

gdma_configure_block="$(sed -n \
	'/^static int micronux_native_configure_gdma(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
gdma_configure_compact="$(tr '\n\t' '  ' <<<"${gdma_configure_block}" | tr -s ' ')"
for gdma_configure_marker in \
	'(st_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) != (DW_GDMA_INT_NATIVE_SCANOUT_MASK | DW_GDMA_INT_STATUS0_RO_MASK)' \
	'(st_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) != DW_GDMA_INT_ECC_ERROR_MASK' \
	'(sig_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) != (DW_GDMA_INT_NATIVE_SCANOUT_MASK | DW_GDMA_INT_STATUS0_RO_MASK)' \
	'(sig_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) != DW_GDMA_INT_ECC_ERROR_MASK' \
	'(common_st_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) != DW_GDMA_INT_COMMON_VALID_MASK' \
	'(common_sig_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) != DW_GDMA_INT_COMMON_VALID_MASK'; do
	if ! grep -Fq "${gdma_configure_marker}" <<<"${gdma_configure_compact}"; then
		printf 'M9 GDMA configure gate is missing semantic check: %s\n' \
			"${gdma_configure_marker}" >&2
		exit 1
	fi
done

gdma_runtime_block="$(sed -n \
	'/^static bool micronux_native_runtime_policy_valid(struct micronux_dsi \*dsi)$/,/^}/p' \
	"${DISPLAY_DRIVER}")"
gdma_runtime_compact="$(tr '\n\t' '  ' <<<"${gdma_runtime_block}" | tr -s ' ')"
for gdma_runtime_marker in \
	'(st_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) == (expected_intr | DW_GDMA_INT_STATUS0_RO_MASK)' \
	'(st_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) == DW_GDMA_INT_ECC_ERROR_MASK' \
	'(sig_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) == (expected_intr | DW_GDMA_INT_STATUS0_RO_MASK)' \
	'(sig_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) == DW_GDMA_INT_ECC_ERROR_MASK' \
	'(common_st_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) == DW_GDMA_INT_COMMON_VALID_MASK' \
	'(common_sig_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) == DW_GDMA_INT_COMMON_VALID_MASK' \
	'readl(dsi->host + DSI_HOST_VID_MODE_CFG) == DSI_HOST_NATIVE_VIDEO_POLICY'; do
	if ! grep -Fq "${gdma_runtime_marker}" <<<"${gdma_runtime_compact}"; then
		printf 'M9 GDMA runtime gate is missing semantic check: %s\n' \
			"${gdma_runtime_marker}" >&2
		exit 1
	fi
done

# Patch40 programmed-video/diagnostic-active-mirror gate.  The writable
# VID_MODE_CFG policy is authoritative; the inactive ACT mirror is evidence
# only and must not participate in native runtime acceptance.
native_dpi_bridge_block="$(sed -n \
	'/^static int micronux_native_configure_dpi_bridge(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_dpi_bridge_compact="$(tr '\n\t' '  ' \
	<<<"${native_dpi_bridge_block}" | tr -s ' ')"
for programmed_video_marker in \
	'writel(DSI_HOST_NATIVE_VIDEO_POLICY, dsi->host + DSI_HOST_VID_MODE_CFG);' \
	'readl(dsi->host + DSI_HOST_VID_MODE_CFG) != DSI_HOST_NATIVE_VIDEO_POLICY' \
	'video-policy=0000ff02 frame-bta=enabled lp=all-periods-and-commands'; do
	if ! grep -Fq "${programmed_video_marker}" \
		<<<"${native_dpi_bridge_compact}"; then
		printf 'M9 kernel is missing programmed video-policy check: %s\n' \
			"${programmed_video_marker}" >&2
		exit 1
	fi
done
if grep -Fq 'DSI_HOST_NATIVE_VIDEO_POLICY_ACT' "${DISPLAY_DRIVER}" ||
	grep -Fq 'DSI_HOST_VID_MODE_CFG_ACT' <<<"${gdma_runtime_block}"; then
	printf 'M9 native runtime acceptance still depends on the DSI active mirror.\n' >&2
	exit 1
fi
native_scanout_snapshot_block="$(sed -n \
	'/^__micronux_capture_scanout_snapshot(struct micronux_dsi \*dsi,$/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_scanout_snapshot_compact="$(tr '\n\t' '  ' \
	<<<"${native_scanout_snapshot_block}" | tr -s ' ')"
diagnostics_show_block="$(sed -n \
	'/^static ssize_t diagnostics_show(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
diagnostics_show_compact="$(tr '\n\t' '  ' \
	<<<"${diagnostics_show_block}" | tr -s ' ')"
readonly ACTIVE_SNAPSHOT_READ='snapshot->host_active = readl(dsi->host + DSI_HOST_VID_MODE_CFG_ACT);'
if ! grep -Fq "${ACTIVE_SNAPSHOT_READ}" \
	<<<"${native_scanout_snapshot_compact}" ||
	! grep -Fq 'snapshot->host_vid, snapshot->host_active,' \
		"${DISPLAY_DRIVER}"; then
	printf 'M9 kernel is missing active-mirror snapshot evidence.\n' >&2
	exit 1
fi
for active_component_marker in \
	'active = readl(dsi->host + DSI_HOST_VID_MODE_CFG_ACT);' \
	'host=%08x active=%08x lpclk=%08x' \
	'(active & DSI_HOST_FRAME_BTA_ACK_EN_ACT)' \
	'!(active & DSI_HOST_LP_VIDEO_EN_ACT_MASK)'; do
	if ! grep -Fq "${active_component_marker}" \
		<<<"${diagnostics_show_compact}"; then
		printf 'M9 kernel is missing active-mirror component diagnostic: %s\n' \
			"${active_component_marker}" >&2
		exit 1
	fi
done
# End Patch40 programmed-video/diagnostic-active-mirror gate.

gdma_teardown_block="$(sed -n \
	'/^static bool micronux_cold_runtime_teardown(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
gdma_teardown_compact="$(tr '\n\t' '  ' <<<"${gdma_teardown_block}" | tr -s ' ')"
for gdma_teardown_marker in \
	'(st_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) == DW_GDMA_INT_STATUS0_RO_MASK' \
	'(st_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) == DW_GDMA_INT_ECC_ERROR_MASK' \
	'(sig_ena0 & DW_GDMA_INT_STATUS0_DEFINED_MASK) == DW_GDMA_INT_STATUS0_RO_MASK' \
	'(sig_ena1 & DW_GDMA_INT_STATUS1_DEFINED_MASK) == DW_GDMA_INT_ECC_ERROR_MASK' \
	'(common_st_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) == DW_GDMA_INT_COMMON_RO_MASK' \
	'(common_sig_ena & DW_GDMA_INT_COMMON_DEFINED_MASK) == DW_GDMA_INT_COMMON_RO_MASK'; do
	if ! grep -Fq "${gdma_teardown_marker}" <<<"${gdma_teardown_compact}"; then
		printf 'M9 GDMA teardown gate is missing semantic check: %s\n' \
			"${gdma_teardown_marker}" >&2
		exit 1
	fi
done

for gdma_semantic_block in \
	"${gdma_configure_compact}" \
	"${gdma_runtime_compact}" \
	"${gdma_teardown_compact}"; do
	if grep -Eq '(^|[ (])(st_ena0|st_ena1|sig_ena0|sig_ena1|common_st_ena|common_sig_ena)[[:space:]]*(==|!=)' \
		<<<"${gdma_semantic_block}" ||
		grep -Eq 'readl\([^)]*DW_GDMA_(CH_)?INT_(STATUS|SIGNAL)_ENA1?\)[[:space:]]*(==|!=)' \
			<<<"${gdma_semantic_block}"; then
		printf 'M9 kernel retained an exact-full-word GDMA enable comparison.\n' >&2
		exit 1
	fi
done

native_i2c_policy_block="$(sed -n \
	'/^static bool micronux_native_i2c_policy_valid(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_i2c_policy_compact="$(tr '\n\t' '  ' \
	<<<"${native_i2c_policy_block}" | tr -s ' ')"
readonly NATIVE_DARK_I2C_RELEASE_POLICY='if (display_state == MICRONUX_DISPLAY_STATE_SCANOUT_INITIALIZING || display_state == MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT) return i2c_state == MICRONUX_NATIVE_I2C_RELEASED && micronux_cold_i2c_released(dsi);'
if ! grep -Fq "${NATIVE_DARK_I2C_RELEASE_POLICY}" \
	<<<"${native_i2c_policy_compact}"; then
	printf 'M9 kernel is missing the narrow dark-initialization I2C release policy.\n' >&2
	exit 1
fi

scanout_failure_log_block="$(awk '
	$0 == "micronux_log_scanout_failure(struct micronux_dsi *dsi," {
		matches++
		if (matches == 2)
			capture = 1
	}
	capture { print }
	capture && $0 == "}" { exit }
' "${DISPLAY_DRIVER}")"
for scanout_log_marker in \
	'MICRONUX:M9.2:COLD-INIT stage=scanout-qualification reason=%s detail=%s error=%d window=%d poll-error=%d initial-generation=%u generation=%u frames=%u faults=%lx dma-error=%08x rearm=%u/%u guards=%u runtime-policy=%u' \
	'MICRONUX:M9.2:COLD-INIT stage=scanout-qualification reason=%s snapshot=host raw=%08x:%08x sticky=%08x:%08x seen=%u mode=%08x vid=%08x active=%08x lpclk=%08x bridge-raw=%08x bridge-filler=%08x bridge-misc=%08x underruns=%u gdma=%08x:%08x:%08x:%08x cfg=%08x chen=%08x cfglo=%08x cfghi=%08x llp=%08x sar=%08x ctrlhi=%08x' \
	'snapshot->bridge_filler, snapshot->bridge_misc,' \
	'reason, snapshot->host_raw0, snapshot->host_raw1,' \
	'snapshot->gdma_top, snapshot->gdma_status0,' \
	'snapshot->gdma_status1, snapshot->gdma_common,' \
	'snapshot->gdma_cfg_lo, snapshot->gdma_cfg_hi,' \
	'snapshot->gdma_llp, snapshot->gdma_sar,' \
	'snapshot->descriptor_ctrl_hi);'; do
	if ! grep -Fq "${scanout_log_marker}" \
		<<<"${scanout_failure_log_block}"; then
		printf 'M9 kernel is missing scanout failure raw-field diagnostic: %s\n' \
			"${scanout_log_marker}" >&2
		exit 1
	fi
done

native_start_scanout_block="$(sed -n \
	'/^static int micronux_native_start_scanout(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_start_scanout_compact="$(tr '\n\t' '  ' \
	<<<"${native_start_scanout_block}" | tr -s ' ')"
for scanout_phase_marker in \
	'micronux_log_scanout_failure(dsi, "initial-precheck", "global-master-masked", -EIO, -1, 0, snapshot.generation, &snapshot);' \
	'micronux_log_scanout_failure(dsi, "initial-precheck", "latched-status", -EIO, -1, 0, snapshot.generation, &snapshot);' \
	'micronux_log_scanout_failure(dsi, "global-enable", "master-or-latched-fault", -EIO, -1, 0, snapshot.generation, &snapshot);' \
	'micronux_log_scanout_failure(dsi, "arm-readback", "descriptor-channel-readback", ret, -1, 0, snapshot.generation, &snapshot);' \
	'micronux_log_scanout_failure(dsi, "qualification-window", "window-health", error, window, ret, initial_generation, &snapshot);'; do
	if ! grep -Fq "${scanout_phase_marker}" \
		<<<"${native_start_scanout_compact}"; then
		printf 'M9 kernel is missing scanout qualification phase diagnostic: %s\n' \
			"${scanout_phase_marker}" >&2
		exit 1
	fi
done

# Patch39 native DMA arm transaction gate.
native_dma_arm_block="$(sed -n \
	'/^micronux_native_dma_arm_frame(struct micronux_dsi \*dsi, u8 index,$/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_dma_arm_compact="$(tr '\n\t' '  ' \
	<<<"${native_dma_arm_block}" | tr -s ' ')"
if [[ -z "${native_dma_arm_block}" ]]; then
	printf 'M9 kernel is missing the native DMA arm helper.\n' >&2
	exit 1
fi
for native_dma_pre_enable_marker in \
	'chen = readl(dsi->gdma + DW_GDMA_CHEN); if (chen & channel)' \
	'writel(MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED, descriptor + DMA_LLI_CTRL_HI);' \
	'writel(MICRONUX_DMA_CHANNEL_CFG_LO, dsi->channel + DW_GDMA_CH_CFG_LO);' \
	'writel(MICRONUX_DMA_CHANNEL_CFG_HI, dsi->channel + DW_GDMA_CH_CFG_HI);' \
	'writel(descriptor_address | DW_GDMA_LLP_MEMORY_PORT, dsi->channel + DW_GDMA_CH_LLP);' \
	'writel(0, dsi->channel + DW_GDMA_CH_LLP + sizeof(u32));' \
	'wmb(); chen = readl(dsi->gdma + DW_GDMA_CHEN); ctrl_hi = readl(descriptor + DMA_LLI_CTRL_HI);' \
	'cfg_lo = readl(dsi->channel + DW_GDMA_CH_CFG_LO); cfg_hi = readl(dsi->channel + DW_GDMA_CH_CFG_HI); llp = readl(dsi->channel + DW_GDMA_CH_LLP); llp_hi = readl(dsi->channel + DW_GDMA_CH_LLP + sizeof(u32)); status0 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS); status1 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS1); common = readl(dsi->gdma + DW_GDMA_INT_COMMON_STATUS); top = readl(dsi->gdma + DW_GDMA_INT_STATUS); if ((chen & channel)' \
	'if ((chen & channel) || ctrl_hi != MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED || cfg_lo != MICRONUX_DMA_CHANNEL_CFG_LO || cfg_hi != MICRONUX_DMA_CHANNEL_CFG_HI || llp != (descriptor_address | DW_GDMA_LLP_MEMORY_PORT) || llp_hi || (status0 & DW_GDMA_INT_NATIVE_SCANOUT_MASK) || (status1 & DW_GDMA_INT_ECC_ERROR_MASK) || (common & DW_GDMA_INT_COMMON_VALID_MASK) || (top & ~DW_GDMA_INT_TOP_ALLOWED))'; do
	if ! grep -Fq "${native_dma_pre_enable_marker}" \
		<<<"${native_dma_arm_compact}"; then
		printf 'M9 native DMA arm gate is missing pre-enable transaction check: %s\n' \
			"${native_dma_pre_enable_marker}" >&2
		exit 1
	fi
done

native_dma_post_enable="${native_dma_arm_compact#*/* The one-shot engine owns descriptor and LLP state after this write. */}"
native_dma_enable_write_count="$(grep -oF \
	'writel(channel | (channel << 8), dsi->gdma + DW_GDMA_CHEN);' \
	<<<"${native_dma_arm_compact}" | wc -l)"
if [[ "${native_dma_enable_write_count}" -ne 1 ]] ||
	[[ "${native_dma_post_enable}" == "${native_dma_arm_compact}" ]] ||
	! grep -Fq 'writel(channel | (channel << 8), dsi->gdma + DW_GDMA_CHEN); chen = readl(dsi->gdma + DW_GDMA_CHEN); status0 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS); status1 = readl(dsi->channel + DW_GDMA_CH_INT_STATUS1); common = readl(dsi->gdma + DW_GDMA_INT_COMMON_STATUS); top = readl(dsi->gdma + DW_GDMA_INT_STATUS); if (!(chen & channel) || (status0 & DW_GDMA_INT_ERROR_MASK) || (status1 & DW_GDMA_INT_ECC_ERROR_MASK) || (common & DW_GDMA_INT_COMMON_VALID_MASK) || (top & ~DW_GDMA_INT_TOP_ALLOWED))' \
		<<<"${native_dma_post_enable}"; then
	printf 'M9 native DMA arm gate is missing the bounded post-enable safety check.\n' >&2
	exit 1
fi
if grep -Eq 'readl\([^)]*(DMA_LLI_CTRL_HI|DW_GDMA_CH_(CFG_LO|CFG_HI|LLP))' \
	<<<"${native_dma_post_enable}" ||
	grep -Eq '(ctrl_hi|cfg_lo|cfg_hi|llp|llp_hi)[[:space:]]*(==|!=)' \
		<<<"${native_dma_post_enable}"; then
	printf 'M9 native DMA arm gate reads or compares hardware-owned descriptor/LLP state after enable.\n' >&2
	exit 1
fi
native_dma_post_enable_readl_count="$(grep -oF 'readl(' \
	<<<"${native_dma_post_enable}" | wc -l)"
if [[ "${native_dma_post_enable_readl_count}" -ne 5 ]]; then
	printf 'M9 native DMA arm gate permits extra post-enable register reads.\n' >&2
	exit 1
fi

native_dma_irq_block="$(sed -n \
	'/^static irqreturn_t micronux_native_dma_irq(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
if ! grep -Fq 'micronux_native_dma_arm_frame(dsi, dsi->front_index, &snapshot)' \
	<<<"${native_start_scanout_block}" ||
	grep -Eq '(^|[^[:alnum:]_])(msleep|usleep_range|read_poll_timeout|schedule_timeout|mutex_lock)\(' \
	<<<"${native_dma_arm_block}"; then
	printf 'M9 kernel did not retain the checked non-sleeping initial DMA arm path.\n' >&2
	exit 1
fi
native_dma_snapshot_count="$(grep -oF 'micronux_capture_scanout_snapshot(' \
	<<<"${native_dma_arm_block}" | wc -l)"
native_dma_snapshot_guard_count="$(grep -oF 'if (failure_snapshot)' \
	<<<"${native_dma_arm_block}" | wc -l)"
if [[ "${native_dma_snapshot_count}" -ne 3 ]] ||
	[[ "${native_dma_snapshot_guard_count}" -ne \
	   "${native_dma_snapshot_count}" ]]; then
	printf 'M9 IRQ DMA arm path can capture a diagnostic snapshot with NULL.\n' >&2
	exit 1
fi
# End Patch39 native DMA arm transaction gate.  Patch41 keeps this checked
# helper for the initial arm and gives recurring IRQ rearm a separate gate.

# Patch41 keeps the one-shot source fed with a minimal recurring IRQ rearm,
# makes bridge starvation black, and publishes passive continuity telemetry.
for patch41_constant_marker in \
	'^#define DSI_BRG_DPI_RSV_DATA[[:space:]]+0x28$' \
	'^#define DSI_BRG_DPI_RSV_DATA_MASK[[:space:]]+GENMASK\(29, 0\)$' \
	'^#define DSI_BRG_FIFO_FLOW_DEPTH[[:space:]]+0x00003fffU$' \
	'^#define DSI_BRG_NATIVE_MISC_OFF[[:space:]]+0x00003200$' \
	'^#define DSI_BRG_NATIVE_MISC_ON[[:space:]]+0x00003201$' \
	'^#define DSI_BRG_NATIVE_DPI_RSV_DATA[[:space:]]+0x00000000$' \
	'^#define MICRONUX_NATIVE_ARM_TO_IRQ_SLOW_NS[[:space:]]+20000000ULL$'; do
	if ! grep -Eq "${patch41_constant_marker}" "${DISPLAY_DRIVER}"; then
		printf 'M9 kernel is missing Patch41 constant: %s\n' \
			"${patch41_constant_marker}" >&2
		exit 1
	fi
done

for patch41_bridge_marker in \
	'writel(0, dsi->bridge + DSI_BRG_CLK_EN); writel(0, dsi->bridge + DSI_BRG_EN); writel(DSI_BRG_NATIVE_DPI_RSV_DATA, dsi->bridge + DSI_BRG_DPI_RSV_DATA); filler = readl(dsi->bridge + DSI_BRG_DPI_RSV_DATA) & DSI_BRG_DPI_RSV_DATA_MASK; if (filler != DSI_BRG_NATIVE_DPI_RSV_DATA) return -EIO;' \
	'writel(DSI_BRG_NATIVE_MISC_OFF, dsi->bridge + DSI_BRG_DPI_MISC_CONFIG);' \
	'writel(0, dsi->bridge + DSI_BRG_INT_ENA);' \
	'writel(DSI_BRG_MODULE_EN, dsi->bridge + DSI_BRG_EN);' \
	'(readl(dsi->bridge + DSI_BRG_DPI_RSV_DATA) & DSI_BRG_DPI_RSV_DATA_MASK) != DSI_BRG_NATIVE_DPI_RSV_DATA' \
	'readl(dsi->bridge + DSI_BRG_DPI_MISC_CONFIG) != DSI_BRG_NATIVE_MISC_OFF' \
	'underflow-filler=00000000 discard-vcnt=800'; do
	if ! grep -Fq "${patch41_bridge_marker}" \
		<<<"${native_dpi_bridge_compact}"; then
		printf 'M9 kernel is missing Patch41 bridge programming: %s\n' \
			"${patch41_bridge_marker}" >&2
		exit 1
	fi
done
patch41_filler_write_line="$(grep -nF \
	'writel(DSI_BRG_NATIVE_DPI_RSV_DATA,' \
	<<<"${native_dpi_bridge_block}" | cut -d: -f1)"
patch41_bridge_enable_line="$(grep -nF \
	'writel(DSI_BRG_MODULE_EN, dsi->bridge + DSI_BRG_EN);' \
	<<<"${native_dpi_bridge_block}" | cut -d: -f1)"
if [[ "$(grep -cF 'writel(DSI_BRG_NATIVE_DPI_RSV_DATA,' \
	<<<"${native_dpi_bridge_block}")" -ne 1 ]] ||
	[[ "$(grep -cF 'writel(DSI_BRG_MODULE_EN, dsi->bridge + DSI_BRG_EN);' \
	<<<"${native_dpi_bridge_block}")" -ne 1 ]] ||
	[[ "$(grep -cF 'writel(0, dsi->bridge + DSI_BRG_INT_ENA);' \
	<<<"${native_dpi_bridge_block}")" -ne 1 ]] ||
	((patch41_filler_write_line >= patch41_bridge_enable_line)); then
	printf 'M9 black filler is not programmed exactly once before bridge enable.\n' >&2
	exit 1
fi
for patch41_runtime_marker in \
	'!(readl(dsi->bridge + DSI_BRG_DPI_RSV_DATA) & DSI_BRG_DPI_RSV_DATA_MASK)' \
	'readl(dsi->bridge + DSI_BRG_DPI_MISC_CONFIG) == DSI_BRG_NATIVE_MISC_ON'; do
	if ! grep -Fq "${patch41_runtime_marker}" \
		<<<"${gdma_runtime_compact}"; then
		printf 'M9 kernel runtime policy is missing Patch41 check: %s\n' \
			"${patch41_runtime_marker}" >&2
		exit 1
	fi
done
if ! grep -Fq 'writel(DSI_BRG_NATIVE_MISC_ON, dsi->bridge + DSI_BRG_DPI_MISC_CONFIG);' \
	<<<"${native_start_scanout_compact}" ||
	! grep -Fq 'snapshot->bridge_filler = readl(dsi->bridge + DSI_BRG_DPI_RSV_DATA) & DSI_BRG_DPI_RSV_DATA_MASK;' \
	<<<"${native_scanout_snapshot_compact}" ||
	! grep -Fq 'snapshot->bridge_misc = readl(dsi->bridge + DSI_BRG_DPI_MISC_CONFIG);' \
	<<<"${native_scanout_snapshot_compact}"; then
	printf 'M9 kernel is missing Patch41 runtime/snapshot bridge evidence.\n' >&2
	exit 1
fi

native_dma_fast_block="$(sed -n \
	'/^micronux_native_dma_rearm_frame_fast(struct micronux_dsi \*dsi, u8 index,$/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_dma_fast_compact="$(tr '\n\t' '  ' \
	<<<"${native_dma_fast_block}" | tr -s ' ')"
if [[ -z "${native_dma_fast_block}" ]]; then
	printf 'M9 kernel is missing the Patch41 fast recurring rearm helper.\n' >&2
	exit 1
fi
for patch41_fast_marker in \
	'if (index >= MICRONUX_DISPLAY_BUFFER_COUNT) return -EINVAL;' \
	'writel(MICRONUX_NATIVE_DMA_DESCRIPTOR_ARMED, descriptor + DMA_LLI_CTRL_HI);' \
	'writel(descriptor_address | DW_GDMA_LLP_MEMORY_PORT, dsi->channel + DW_GDMA_CH_LLP);' \
	'writel(0, dsi->channel + DW_GDMA_CH_LLP + sizeof(u32));' \
	'wmb(); writel(channel | (channel << 8), dsi->gdma + DW_GDMA_CHEN); arm_ns = local_clock();' \
	'WRITE_ONCE(dsi->rearm_attempts, READ_ONCE(dsi->rearm_attempts) + 1);' \
	'dsi->native_rearm_last_ns = rearm_ns;' \
	'dsi->native_rearm_max_ns = max(dsi->native_rearm_max_ns, rearm_ns);' \
	'dsi->native_last_arm_ns = arm_ns;'; do
	if ! grep -Fq "${patch41_fast_marker}" \
		<<<"${native_dma_fast_compact}"; then
		printf 'M9 fast recurring rearm is missing exact operation: %s\n' \
			"${patch41_fast_marker}" >&2
		exit 1
	fi
done
patch41_fast_index_line="$(grep -nF \
	'if (index >= MICRONUX_DISPLAY_BUFFER_COUNT)' \
	<<<"${native_dma_fast_block}" | cut -d: -f1)"
patch41_fast_attempt_line="$(grep -nF \
	'WRITE_ONCE(dsi->rearm_attempts,' \
	<<<"${native_dma_fast_block}" | cut -d: -f1)"
native_dma_fast_writel_count="$(grep -oF 'writel(' \
	<<<"${native_dma_fast_compact}" | wc -l)"
native_dma_fast_wmb_count="$(grep -oF 'wmb();' \
	<<<"${native_dma_fast_compact}" | wc -l)"
if [[ "${native_dma_fast_writel_count}" -ne 4 ]] ||
	[[ "${native_dma_fast_wmb_count}" -ne 1 ]] ||
	[[ "$(grep -cF 'WRITE_ONCE(dsi->rearm_attempts,' \
		<<<"${native_dma_fast_block}")" -ne 1 ]] ||
	grep -Fq 'rearm_failures' <<<"${native_dma_fast_block}" ||
	((patch41_fast_index_line >= patch41_fast_attempt_line)) ||
	grep -Eq 'readl\(|read_poll|DW_GDMA_CH_CFG_(LO|HI)|MICRONUX_DMA_CHANNEL_CFG_(LO|HI)' \
		<<<"${native_dma_fast_block}"; then
	printf 'M9 fast recurring rearm is not the exact four-write/read-free transaction.\n' >&2
	exit 1
fi

readonly PATCH41_FAST_IRQ_CALL='micronux_native_dma_rearm_frame_fast(dsi, next_index,'
native_dma_irq_lock_count="$(grep -cF \
	'spin_lock_irqsave(&dsi->state_lock, flags);' \
	<<<"${native_dma_irq_block}")"
native_dma_irq_unlock_count="$(grep -cF \
	'spin_unlock_irqrestore(&dsi->state_lock, flags);' \
	<<<"${native_dma_irq_block}")"
native_dma_irq_fast_count="$(grep -cF "${PATCH41_FAST_IRQ_CALL}" \
	<<<"${native_dma_irq_block}")"
native_dma_irq_lock_line="$(grep -nF \
	'spin_lock_irqsave(&dsi->state_lock, flags);' \
	<<<"${native_dma_irq_block}" | cut -d: -f1)"
native_dma_irq_fast_line="$(grep -nF "${PATCH41_FAST_IRQ_CALL}" \
	<<<"${native_dma_irq_block}" | cut -d: -f1)"
native_dma_irq_unlock_line="$(grep -nF \
	'spin_unlock_irqrestore(&dsi->state_lock, flags);' \
	<<<"${native_dma_irq_block}" | cut -d: -f1)"
if [[ "${native_dma_irq_lock_count}" -ne 1 ]] ||
	[[ "${native_dma_irq_unlock_count}" -ne 1 ]] ||
	[[ "${native_dma_irq_fast_count}" -ne 1 ]] ||
	((native_dma_irq_fast_line <= native_dma_irq_lock_line ||
	  native_dma_irq_fast_line >= native_dma_irq_unlock_line)); then
	printf 'M9 recurring fast rearm is not uniquely called under state_lock.\n' >&2
	exit 1
fi

native_telemetry_capture_block="$(sed -n \
	'/^micronux_native_capture_telemetry_locked(struct micronux_dsi \*dsi,$/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_telemetry_reset_block="$(sed -n \
	'/^micronux_native_reset_telemetry(struct micronux_dsi \*dsi,$/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_dma_poll_block="$(sed -n \
	'/^static enum hrtimer_restart micronux_dma_poll(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_dma_irq_compact="$(tr '\n\t' '  ' \
	<<<"${native_dma_irq_block}" | tr -s ' ')"
native_dma_poll_compact="$(tr '\n\t' '  ' \
	<<<"${native_dma_poll_block}" | tr -s ' ')"
native_telemetry_reset_compact="$(tr '\n\t' '  ' \
	<<<"${native_telemetry_reset_block}" | tr -s ' ')"
for patch41_capture_marker in \
	'telemetry->arm_to_irq_last_ns = dsi->native_arm_to_irq_last_ns;' \
	'telemetry->arm_to_irq_max_ns = dsi->native_arm_to_irq_max_ns;' \
	'telemetry->rearm_last_ns = dsi->native_rearm_last_ns;' \
	'telemetry->rearm_max_ns = dsi->native_rearm_max_ns;' \
	'telemetry->arm_to_irq_over_20ms = dsi->native_arm_to_irq_over_20ms;' \
	'telemetry->fifo_irq_last = dsi->native_fifo_irq_last;' \
	'telemetry->fifo_irq_min = dsi->native_fifo_irq_min;' \
	'telemetry->fifo_irq_zero = dsi->native_fifo_irq_zero;' \
	'telemetry->fifo_poll_last = dsi->native_fifo_poll_last;' \
	'telemetry->fifo_poll_min = dsi->native_fifo_poll_min;' \
	'telemetry->fifo_poll_zero = dsi->native_fifo_poll_zero;'; do
	if ! grep -Fq "${patch41_capture_marker}" \
		<<<"$(tr '\n\t' '  ' <<<"${native_telemetry_capture_block}" | tr -s ' ')"; then
		printf 'M9 locked telemetry copy is missing field: %s\n' \
			"${patch41_capture_marker}" >&2
		exit 1
	fi
done
for patch41_telemetry_marker in \
	'#include <linux/sched/clock.h>' \
	'u64 native_last_arm_ns;' \
	'u64 native_arm_to_irq_last_ns;' \
	'u64 native_arm_to_irq_max_ns;' \
	'u64 native_rearm_last_ns;' \
	'u64 native_rearm_max_ns;' \
	'u32 native_arm_to_irq_over_20ms;' \
	'u32 native_fifo_irq_last;' \
	'u32 native_fifo_irq_min;' \
	'u32 native_fifo_irq_zero;' \
	'u32 native_fifo_poll_last;' \
	'u32 native_fifo_poll_min;' \
	'u32 native_fifo_poll_zero;'; do
	if ! grep -Fq "${patch41_telemetry_marker}" "${DISPLAY_DRIVER}"; then
		printf 'M9 kernel is missing Patch41 telemetry state: %s\n' \
			"${patch41_telemetry_marker}" >&2
		exit 1
	fi
done
for patch41_irq_telemetry_marker in \
	'irq_entry_ns = local_clock(); fifo_depth = readl(dsi->bridge + DSI_BRG_FIFO_FLOW_STATUS) & DSI_BRG_FIFO_FLOW_DEPTH; top = readl(dsi->gdma + DW_GDMA_INT_STATUS);' \
	'if (dsi->native_last_arm_ns) { arm_to_irq_ns = irq_entry_ns - dsi->native_last_arm_ns;' \
	'if (arm_to_irq_ns > MICRONUX_NATIVE_ARM_TO_IRQ_SLOW_NS) dsi->native_arm_to_irq_over_20ms++;' \
	'dsi->native_fifo_irq_last = fifo_depth;' \
	'dsi->native_fifo_irq_min = min(dsi->native_fifo_irq_min, fifo_depth);' \
	'if (!fifo_depth) dsi->native_fifo_irq_zero++;'; do
	if ! grep -Fq "${patch41_irq_telemetry_marker}" \
		<<<"${native_dma_irq_compact}"; then
		printf 'M9 IRQ path is missing passive Patch41 telemetry: %s\n' \
			"${patch41_irq_telemetry_marker}" >&2
		exit 1
	fi
done
if ! grep -Fq 'if (!(status0 & DW_GDMA_INT_DMA_TFR_DONE)) {' \
	<<<"${native_dma_irq_block}"; then
	printf 'M9 IRQ completion mask is not fixed to transfer-done.\n' >&2
	exit 1
fi
native_dma_irq_tfr_line="$(grep -nF \
	'if (!(status0 & DW_GDMA_INT_DMA_TFR_DONE)) {' \
	<<<"${native_dma_irq_block}" | cut -d: -f1)"
native_dma_irq_fifo_commit_line="$(grep -nF \
	'dsi->native_fifo_irq_last = fifo_depth;' \
	<<<"${native_dma_irq_block}" | cut -d: -f1)"
if [[ "$(grep -cF 'DSI_BRG_FIFO_FLOW_STATUS' \
	<<<"${native_dma_irq_block}")" -ne 1 ]] ||
	((native_dma_irq_fifo_commit_line <= native_dma_irq_tfr_line)); then
	printf 'M9 IRQ FIFO telemetry is not sampled once and committed only on completion.\n' >&2
	exit 1
fi
if [[ "$(grep -cF 'DSI_BRG_FIFO_FLOW_STATUS' \
	<<<"${native_dma_poll_block}")" -ne 1 ]] ||
	! grep -Fq 'native = dsi->required_handoff_abi == MICRONUX_DISPLAY_ABI_VERSION_V3; bridge_status = readl(dsi->bridge + DSI_BRG_INT_RAW); if (native) { fifo_depth = readl(dsi->bridge + DSI_BRG_FIFO_FLOW_STATUS) & DSI_BRG_FIFO_FLOW_DEPTH; spin_lock_irqsave(&dsi->state_lock, flags);' \
		<<<"${native_dma_poll_compact}"; then
	printf 'M9 watchdog FIFO telemetry is not a single ABI-v3-only sample.\n' >&2
	exit 1
fi
for patch41_reset_marker in \
	'if (reset_last_arm) dsi->native_last_arm_ns = 0;' \
	'dsi->native_arm_to_irq_last_ns = 0;' \
	'dsi->native_arm_to_irq_max_ns = 0;' \
	'dsi->native_rearm_last_ns = 0;' \
	'dsi->native_rearm_max_ns = 0;' \
	'dsi->native_arm_to_irq_over_20ms = 0;' \
	'dsi->native_fifo_irq_last = 0;' \
	'dsi->native_fifo_irq_min = DSI_BRG_FIFO_FLOW_DEPTH;' \
	'dsi->native_fifo_irq_zero = 0;' \
	'dsi->native_fifo_poll_last = 0;' \
	'dsi->native_fifo_poll_min = DSI_BRG_FIFO_FLOW_DEPTH;' \
	'dsi->native_fifo_poll_zero = 0;'; do
	if ! grep -Fq "${patch41_reset_marker}" \
		<<<"${native_telemetry_reset_compact}"; then
		printf 'M9 telemetry reset is missing exact state: %s\n' \
			"${patch41_reset_marker}" >&2
		exit 1
	fi
done
if ! grep -Fq 'spin_lock_irqsave(&dsi->state_lock, flags);' \
	<<<"${native_telemetry_reset_block}" ||
	! grep -Fq 'spin_unlock_irqrestore(&dsi->state_lock, flags);' \
	<<<"${native_telemetry_reset_block}" ||
	[[ "$(grep -cF 'micronux_native_reset_telemetry(dsi, true);' \
		"${DISPLAY_DRIVER}")" -ne 1 ]] ||
	[[ "$(grep -cF 'micronux_native_reset_telemetry(dsi, false);' \
		"${DISPLAY_DRIVER}")" -ne 2 ]] ||
	! grep -Fq 'micronux_native_reset_telemetry(dsi, false); micronux_watchdog_seed_progress(dsi); WRITE_ONCE(dsi->scanout_ready, true);' \
		<<<"$(tr '\n\t' '  ' <"${DISPLAY_DRIVER}" | tr -s ' ')"; then
	printf 'M9 telemetry reset is not locked at start and immediately before publication.\n' >&2
	exit 1
fi

scanout_show_block="$(sed -n \
	'/^static ssize_t scanout_show(/,/^}/p' "${DISPLAY_DRIVER}")"
for patch41_sysfs_block in \
	"${scanout_show_block}" \
	"${diagnostics_show_block}"; do
	patch41_sysfs_compact="$(tr '\n\t' '  ' \
		<<<"${patch41_sysfs_block}" | tr -s ' ')"
	for patch41_sysfs_marker in \
		'micronux_native_capture_telemetry_locked(dsi, &telemetry);' \
		'bridge_filler = readl(dsi->bridge + DSI_BRG_DPI_RSV_DATA) & DSI_BRG_DPI_RSV_DATA_MASK;' \
		'bridge_misc = readl(dsi->bridge + DSI_BRG_DPI_MISC_CONFIG);' \
		'arm-to-irq-ns=%llu/%llu arm-to-irq-over20ms=%u rearm-ns=%llu/%llu fifo-irq=%u/%u/%u fifo-poll=%u/%u/%u bridge-filler=%08x bridge-misc=%08x'; do
		if ! grep -Fq "${patch41_sysfs_marker}" \
			<<<"${patch41_sysfs_compact}"; then
			printf 'M9 sysfs telemetry is missing exact field: %s\n' \
				"${patch41_sysfs_marker}" >&2
			exit 1
		fi
	done
	if ! grep -Fq 'spin_lock_irqsave(&dsi->state_lock, flags);' \
		<<<"${patch41_sysfs_block}" ||
		! grep -Fq 'spin_unlock_irqrestore(&dsi->state_lock, flags);' \
			<<<"${patch41_sysfs_block}"; then
		printf 'M9 sysfs telemetry is not copied under state_lock.\n' >&2
		exit 1
	fi
done
# End Patch41 fast-rearm/black-filler/passive-telemetry gate.

# Patch42 starts the visible-runtime telemetry epoch at the last dark health
# boundary while retaining the arm that crosses reveal.
display_driver_compact="$(tr '\n\t' '  ' <"${DISPLAY_DRIVER}" | tr -s ' ')"
patch42_epoch_remaining="${display_driver_compact}"
for patch42_epoch_marker in \
	'dsi->backlight->props.power = FB_BLANK_POWERDOWN;' \
	'if (!micronux_native_final_status_clean(dsi)) {' \
	'ret = -EIO;' \
	'goto fail_render;' \
	'/* PWM is still the ACKed zero from Stage B; reveal control comes first. */' \
	'/* Start the visible-runtime epoch at the final dark boundary. */' \
	'micronux_native_reset_telemetry(dsi, false);' \
	'WRITE_ONCE(dsi->native_reveal_may_be_lit, true);' \
	'/* Publish possible illumination before the external gate write. */' \
	'smp_wmb();' \
	'ret = micronux_native_write_control(dsi, reveal_command);'; do
	case "${patch42_epoch_remaining}" in
	*"${patch42_epoch_marker}"*)
		patch42_epoch_remaining="${patch42_epoch_remaining#*"${patch42_epoch_marker}"}"
		;;
	*)
		printf 'M9 kernel is missing or reorders the Patch42 final-dark telemetry epoch: %s\n' \
			"${patch42_epoch_marker}" >&2
		exit 1
		;;
	esac
done

# Patch43 must capture live runtime evidence before either containment path and
# emit it only after off-first teardown has finished.
fault_work_block="$(sed -n '/^static void micronux_fault_work(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
fault_work_compact="$(tr '\n\t' '  ' <<<"${fault_work_block}" | tr -s ' ')"
for patch43_marker in \
	'struct micronux_runtime_fault_record record;' \
	'micronux_capture_runtime_fault(dsi, state, faults, &record);' \
	'micronux_cold_runtime_fail_quiescent(dsi, "runtime-fault-dark", -EIO);' \
	'micronux_native_fail_reveal_locked(dsi, "runtime-fault", -EIO, false);' \
	'micronux_log_runtime_fault(dsi, &record);'; do
	if ! grep -Fq "${patch43_marker}" <<<"${fault_work_compact}"; then
		printf 'M9 kernel is missing Patch43 fault evidence ordering: %s\n' \
			"${patch43_marker}" >&2
		exit 1
	fi
done
for patch43_marker in \
	'__micronux_capture_scanout_snapshot(dsi, snapshot, index, ignored_status1, true);' \
	'__micronux_capture_scanout_snapshot(dsi, snapshot, index, 0, false);' \
	'MICRONUX:M9.2:RUNTIME-FAULT state=CAPTURED display-state=%s trigger-faults=%lx faults=%lx' \
	'MICRONUX:M9.2:RUNTIME-FAULT state=CAPTURED snapshot=host raw=%08x:%08x'; do
	if ! grep -Fq "${patch43_marker}" <<<"${display_driver_compact}"; then
		printf 'M9 kernel is missing Patch43 bounded fault evidence: %s\n' \
			"${patch43_marker}" >&2
		exit 1
	fi
done

# Patch44 is the official PSRAM-bandwidth mitigation: ABI3 is derated while
# the legacy ABI2 handoff remains at its established 80 MHz / 1.5 Gbps tuple.
for patch44_marker in \
	'#define DSI_HOST_CLKMGR_VALUE 0x00000d07' \
	'#define HP_CLKRST_DSI_DPI_CLK_DIV_VALUE 3' \
	'#define HP_CLKRST_DSI_DPI_CLOCK_VALUE 0x000003a3' \
	'micronux_cold_phy_write(dsi, 0x44, 0x54);' \
	'micronux_cold_phy_write(dsi, 0x17, 0x00);' \
	'micronux_cold_phy_write(dsi, 0x18, 0x11);' \
	'micronux_cold_phy_write(dsi, 0x18, 0x81);' \
	'handoff->pixel_clock_hz != 60000000' \
	'handoff->lane_bit_rate_mbps != 1000' \
	'writel(42, dsi->host + DSI_HOST_VID_HSA_TIME);' \
	'writel(42, dsi->host + DSI_HOST_VID_HBP_TIME);' \
	'writel(1833, dsi->host + DSI_HOST_VID_HLINE_TIME);' \
	'lane-mbps=1000 lanes=2 pll-m=50 pll-n=1 range=2a' \
	'source=f240m divider=4 dpi-hz=60000000'; do
	if ! grep -Fq "${patch44_marker}" <<<"${display_driver_compact}"; then
		printf 'M9 kernel is missing Patch44 bandwidth profile: %s\n' \
			"${patch44_marker}" >&2
		exit 1
	fi
done
legacy_handoff_block="$(sed -n \
	'/^static int micronux_validate_handoff_v2(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
legacy_handoff_compact="$(tr '\n\t' '  ' \
	<<<"${legacy_handoff_block}" | tr -s ' ')"
if ! grep -Fq 'handoff->pixel_clock_hz != 80000000' \
	<<<"${legacy_handoff_compact}" ||
	! grep -Fq 'handoff->lane_bit_rate_mbps != 1500' \
		<<<"${legacy_handoff_compact}"; then
	printf 'M9 kernel Patch44 changed the legacy ABI2 display profile.\n' >&2
	exit 1
fi

# Patch46 validates the future reload transaction without changing scanout.
for patch46_constant in \
	'MICRONUX_DMA_CHANNEL_RELOAD_CFG_LO[[:space:]]+0x00000005' \
	'MICRONUX_NATIVE_DMA_RELOAD_CTRL_HI[[:space:]]+0x00108840'; do
	if ! grep -Eq "${patch46_constant}" "${DISPLAY_DRIVER}"; then
		printf 'M9 kernel is missing Patch46 constant: %s\n' \
			"${patch46_constant}" >&2
		exit 1
	fi
done
patch46_reload_block="$(sed -n \
	'/^micronux_native_prepare_reload_plan(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
patch46_reload_compact="$(tr '\n\t' '  ' \
	<<<"${patch46_reload_block}" | tr -s ' ')"
for patch46_reload_marker in \
	'dsi->queued_index >= 0' \
	'dsi->front_index != dsi->presented_index' \
	'transfer_bytes = ((u64)MICRONUX_DMA_BLOCK_TRANSFER_SIZE + 1) * 8;' \
	'transfer_bytes != dsi->handoff.framebuffer_size' \
	'plan->source = dsi->handoff.framebuffer_address[front];' \
	'plan->destination = dsi->handoff.dsi_fifo_address;' \
	'plan->config_lo = MICRONUX_DMA_CHANNEL_RELOAD_CFG_LO;' \
	'plan->interrupt_mask = DW_GDMA_INT_ERROR_MASK;'; do
	if ! grep -Fq "${patch46_reload_marker}" \
		<<<"${patch46_reload_compact}"; then
		printf 'M9 kernel is missing Patch46 reload transaction: %s\n' \
			"${patch46_reload_marker}" >&2
		exit 1
	fi
done
if grep -Eq 'writel|readl|readl_poll' <<<"${patch46_reload_block}"; then
	printf 'M9 Patch46 preparation unexpectedly accesses MMIO.\n' >&2
	exit 1
fi
patch47_start_block="$(sed -n \
	'/^static int micronux_native_start_continuous_fixed_front(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
patch47_start_compact="$(tr '\n\t' '  ' \
	<<<"${patch47_start_block}" | tr -s ' ')"
for patch47_start_marker in \
	'dsi->queued_index >= 0' \
	'dsi->front_index != dsi->presented_index' \
	'dsi->scanout_active = false;' \
	'synchronize_irq(dsi->irq);' \
	'writel(channel << 8, dsi->gdma + DW_GDMA_CHEN);' \
	'writel(abort_mask, dsi->gdma + DW_GDMA_CHABORT);' \
	'writel(front_address, dsi->channel + DW_GDMA_CH_SAR);' \
	'writel(dsi->handoff.dsi_fifo_address, dsi->channel + DW_GDMA_CH_DAR);' \
	'writel(MICRONUX_DMA_BLOCK_TRANSFER_SIZE, dsi->channel + DW_GDMA_CH_BLOCK_TS);' \
	'writel(MICRONUX_NATIVE_DMA_RELOAD_CTRL_HI, dsi->channel + DW_GDMA_CH_CTL_HI);' \
	'writel(MICRONUX_DMA_CHANNEL_RELOAD_CFG_LO, dsi->channel + DW_GDMA_CH_CFG_LO);' \
	'writel(DW_GDMA_INT_NATIVE_RELOAD_MASK | DW_GDMA_INT_STATUS0_RO_MASK, dsi->channel + DW_GDMA_CH_INT_STATUS_ENA);' \
	'dsi->native_continuous_fixed_front = true;' \
	'writel(channel | (channel << 8), dsi->gdma + DW_GDMA_CHEN);' \
	'wraps < MICRONUX_SCANOUT_STABLE_FRAMES' \
	'micronux_native_final_status_clean(dsi)' \
	'mode=continuous-fixed-front state=QUALIFIED'; do
	if ! grep -Fq "${patch47_start_marker}" \
		<<<"${patch47_start_compact}"; then
		printf 'M9 kernel is missing Patch47 fixed-front reload marker: %s\n' \
			"${patch47_start_marker}" >&2
		exit 1
	fi
done
if [[ "$(grep -cF 'synchronize_irq(dsi->irq);' \
	<<<"${patch47_start_block}")" -ne 2 ]] ||
	grep -Fq 'DW_GDMA_INT_DMA_TFR_DONE' <<<"${patch47_start_block}"; then
	printf 'M9 Patch47 did not close one-shot completion rearm cleanly.\n' >&2
	exit 1
fi

patch47_irq_block="$(sed -n \
	'/^static irqreturn_t micronux_native_dma_irq(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
patch47_irq_compact="$(tr '\n\t' '  ' \
	<<<"${patch47_irq_block}" | tr -s ' ')"
for patch47_irq_marker in \
	'continuous = READ_ONCE(dsi->native_continuous_fixed_front);' \
	'if (continuous) {' \
	'micronux_latch_fault(dsi, MICRONUX_FAULT_DMA);' \
	'if (!(status0 & DW_GDMA_INT_DMA_TFR_DONE)) {'; do
	if ! grep -Fq "${patch47_irq_marker}" \
		<<<"${patch47_irq_compact}"; then
		printf 'M9 kernel is missing Patch47 IRQ containment: %s\n' \
			"${patch47_irq_marker}" >&2
		exit 1
	fi
done

for patch47_runtime_marker in \
	'scanout-mode=continuous-fixed-front cpu-rearm=disabled' \
	'scanout-mode=continuous-fixed-front rearm=hardware-reload' \
	'scanout-mode=continuous-fixed-front refresh-progress=sar' \
	'READ_ONCE(dsi->native_last_progress_jiffies)' \
	'WRITE_ONCE(dsi->render_enabled, false);'; do
	if ! grep -Fq "${patch47_runtime_marker}" "${DISPLAY_DRIVER}"; then
		printf 'M9 kernel is missing Patch47 runtime marker: %s\n' \
			"${patch47_runtime_marker}" >&2
		exit 1
	fi
done

for patch48_link_marker in \
	'writel(0, dsi->channel + DW_GDMA_CH_LLP);' \
	'writel(0, dsi->channel + DW_GDMA_CH_LLP + sizeof(u32));' \
	'readl(dsi->channel + DW_GDMA_CH_LLP) ||' \
	'readl(dsi->channel + DW_GDMA_CH_LLP + sizeof(u32)) ||' \
	'reason=enable-not-observed' \
	'reason=stopped-before-wrap'; do
	if ! grep -Fq "${patch48_link_marker}" \
		<<<"${patch47_start_compact}"; then
		printf 'M9 kernel is missing Patch48 reload-link marker: %s\n' \
			"${patch48_link_marker}" >&2
		exit 1
	fi
done

boot_ready_block="$(sed -n \
	'/^static ssize_t micronux_native_boot_ready_store(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
for one_shot_marker in \
	'if (READ_ONCE(dsi->boot_ready_consumed)) {' \
	'ret = -EALREADY;' \
	'WRITE_ONCE(dsi->boot_ready_consumed, true);' \
	'micronux_native_requalify_front(dsi, target_commit);' \
	'MICRONUX_DISPLAY_STATE_SCANOUT_QUALIFIED_QUIESCENT' \
	'MICRONUX_DISPLAY_STATE_RUNTIME_REVEALED'; do
	if ! grep -Fq "${one_shot_marker}" <<<"${boot_ready_block}"; then
		printf 'M9 kernel is missing one-shot boot_ready marker: %s\n' \
			"${one_shot_marker}" >&2
		exit 1
	fi
done

vpg_store_block="$(sed -n '/^static ssize_t vpg_test_ms_store(/,/^}/p' \
	"${DISPLAY_DRIVER}")"
native_runtime_attrs="$(sed -n \
	'/^static struct attribute .*micronux_native_runtime_attrs/,/^};/p' \
	"${DISPLAY_DRIVER}")"
if ! grep -q 'MICRONUX_DISPLAY_ABI_VERSION_V3' <<<"${vpg_store_block}" ||
	! grep -q 'return -EOPNOTSUPP;' <<<"${vpg_store_block}" ||
	[[ -z "${native_runtime_attrs}" ]] ||
	grep -Eq 'dev_attr_(boot_ready|vpg_test_ms)' \
		<<<"${native_runtime_attrs}"; then
	printf 'M9 kernel did not retain native VPG-unsupported/attribute-absent semantics.\n' >&2
	exit 1
fi
if grep -q 'micronux_dma_start_reload' \
	"${KERNEL_DIR}/drivers/video/fbdev/esp32p4-dsi.c"; then
	printf 'M9 kernel retained the rejected GDMA auto-reload path.\n' >&2
	exit 1
fi
if [[ ! -x "${OUTPUT_DIR}/target/usr/bin/micronux-display-test" ]]; then
	printf 'M9 rootfs is missing micronux-display-test.\n' >&2
	exit 1
fi
readonly JD9365_FIRMWARE_SOURCE="${EXTERNAL_DIR}/package/micronux-jd9365-firmware"
readonly JD9365_FIRMWARE_TARGET="${OUTPUT_DIR}/target/lib/firmware/micronux"
for firmware_file in \
	jd9365-waveshare-10.1-v2.bin \
	jd9365-waveshare-10.1-v2.LICENSE \
	jd9365-waveshare-10.1-v2.provenance.txt; do
	if [[ ! -f "${JD9365_FIRMWARE_TARGET}/${firmware_file}" ]]; then
		printf 'M9 rootfs is missing JD9365 firmware artifact: %s\n' \
			"${firmware_file}" >&2
		exit 1
	fi
done
firmware_sha256="$(sha256sum \
	"${JD9365_FIRMWARE_TARGET}/jd9365-waveshare-10.1-v2.bin" |
	awk '{print $1}')"
if [[ "${firmware_sha256}" != \
	"5f5d5d5fde2471130c3508160763235320854beb561dda049d066475e2bbf0f3" ]]; then
	printf 'M9 rootfs has the wrong JD9365 firmware payload: %s\n' \
		"${firmware_sha256}" >&2
	exit 1
fi
if ! cmp -s "${JD9365_FIRMWARE_SOURCE}/LICENSE" \
	"${JD9365_FIRMWARE_TARGET}/jd9365-waveshare-10.1-v2.LICENSE" ||
	! cmp -s "${JD9365_FIRMWARE_SOURCE}/PROVENANCE.md" \
	"${JD9365_FIRMWARE_TARGET}/jd9365-waveshare-10.1-v2.provenance.txt"; then
	printf 'M9 rootfs JD9365 license/provenance does not match source.\n' >&2
	exit 1
fi
if grep -Eq '^BR2_PACKAGE_LVGL[^=]*=y$' "${OUTPUT_DIR}/.config" ||
	find "${OUTPUT_DIR}/target" -iname '*lvgl*' -print -quit | grep -q .; then
	printf 'M9.1 foundation unexpectedly contains an LVGL artifact.\n' >&2
	exit 1
fi

dt_source="$(${OUTPUT_DIR}/host/bin/dtc -I dtb -O dts "${DTB}" 2>/dev/null)"
display_node="$(sed -n '/display@500a0000 {/,/};/p' <<<"${dt_source}")"
display_pool_node="$(sed -n '/display-pool@49300000 {/,/};/p' <<<"${dt_source}")"
dt_compact="$(tr '\n\t' '  ' <<<"${dt_source}" | tr -s ' ')"
display_node_compact="$(tr '\n\t' '  ' <<<"${display_node}" | tr -s ' ')"
if ! grep -Fq 'espressif,dual-slot; espressif,allow-single-slot;' \
	<<<"${dt_compact}" ||
	! grep -Fq 'slot@0 { reg = <0x00>; status = "disabled";' \
		<<<"${dt_compact}" ||
	! grep -Fq 'slot@1 { reg = <0x01>; max-frequency = <0x1312d00>; bus-width = <0x04>; non-removable;' \
		<<<"${dt_compact}"; then
	printf 'M9 DTB did not disable only microSD slot 0 and retain C6 SDIO slot 1.\n' >&2
	exit 1
fi
if ! grep -q 'display-pool@49300000' <<<"${dt_source}" ||
	! grep -q 'reg = <0x49300000 0x400000>;' <<<"${dt_source}" ||
	! grep -q 'no-map;' <<<"${display_pool_node}" ||
	! grep -q 'memory-region = <' <<<"${display_node}"; then
	printf 'M9 DTB is missing the protected 4 MiB display pool.\n' >&2
	exit 1
fi
for property in \
	'espressif,handoff-address = <0x49f00000>;' \
	'espressif,required-handoff-abi = <0x03>;' \
	'espressif,required-silicon-revision = <0x67>;' \
	'espressif,uncached-offset = <0x40000000>;' \
	'firmware-name = "micronux/jd9365-waveshare-10.1-v2.bin";'; do
	if ! grep -q "${property}" <<<"${display_node}"; then
		printf 'M9 DTB display node is missing: %s\n' "${property}" >&2
		exit 1
	fi
done
for resource in \
	'0x500a0000 0x800' \
	'0x500a0800 0x104' \
	'0x500a6000 0x1000' \
	'0x50081000 0x1000' \
	'0x500c4000 0x1000' \
	'0x500d6000 0x1000' \
	'0x500e0000 0x1000' \
	'0x500e1000 0x1000' \
	'0x500e6000 0x1000' \
	'0x50115000 0x1000' \
	'0x5012d000 0x1000'; do
	if ! grep -q "${resource}" <<<"${display_node_compact}"; then
		printf 'M9 DTB is missing cold-init MMIO resource: %s\n' \
			"${resource}" >&2
		exit 1
	fi
done
if ! grep -q 'reg-names = "dsi-host", "dsi-bridge", "dma-pms", "gdma", "i2c", "intr-matrix", "gpio", "iomux", "hp-clkrst", "pmu", "efuse";' \
	<<<"${dt_compact}"; then
	printf 'M9 DTB has the wrong cold-init MMIO resource ordering.\n' >&2
	exit 1
fi
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
	--max-memory-end "${DISPLAY_POOL_ADDRESS}" \
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
