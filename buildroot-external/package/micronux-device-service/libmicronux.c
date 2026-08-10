/* SPDX-License-Identifier: MIT */

#include <errno.h>
#include <poll.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "device-protocol.h"
#include "micronux/device.h"

#define CLIENT_RESPONSE_GRACE_MS 1000U

static uint32_t next_request_id;

static int status_errno(int32_t status)
{
	switch (status) {
	case MICRONUX_DEVICE_STATUS_VERSION:
		return EPROTONOSUPPORT;
	case MICRONUX_DEVICE_STATUS_DENIED:
		return EACCES;
	case MICRONUX_DEVICE_STATUS_INVALID:
		return EINVAL;
	case MICRONUX_DEVICE_STATUS_TIMEOUT:
		return ETIMEDOUT;
	case MICRONUX_DEVICE_STATUS_BUSY:
		return EBUSY;
	case MICRONUX_DEVICE_STATUS_IO:
	default:
		return EIO;
	}
}

static int exchange(uint32_t operation, uint32_t value, uint32_t timeout_ms,
		struct micronux_device_response *response)
{
	struct sockaddr_un address;
	struct micronux_device_request request = {
		.magic = MICRONUX_DEVICE_MAGIC,
		.abi_major = MICRONUX_DEVICE_ABI_MAJOR,
		.abi_minor = MICRONUX_DEVICE_ABI_MINOR,
		.request_id = __sync_add_and_fetch(&next_request_id, 1),
		.operation = operation,
		.value = value,
		.timeout_ms = timeout_ms,
	};
	struct pollfd pfd;
	ssize_t length;
	int fd;
	int rc;

	fd = socket(AF_UNIX, SOCK_SEQPACKET, 0);
	if (fd < 0)
		return -1;
	memset(&address, 0, sizeof(address));
	address.sun_family = AF_UNIX;
	if (strlen(MICRONUX_DEVICE_SOCKET_PATH) >= sizeof(address.sun_path)) {
		errno = ENAMETOOLONG;
		goto fail;
	}
	strcpy(address.sun_path, MICRONUX_DEVICE_SOCKET_PATH);
	if (connect(fd, (struct sockaddr *)&address, sizeof(address)) < 0)
		goto fail;
	length = send(fd, &request, sizeof(request), MSG_NOSIGNAL);
	if (length != (ssize_t)sizeof(request)) {
		if (length >= 0)
			errno = EIO;
		goto fail;
	}

	pfd.fd = fd;
	pfd.events = POLLIN;
	do {
		rc = poll(&pfd, 1, (int)(timeout_ms + CLIENT_RESPONSE_GRACE_MS));
	} while (rc < 0 && errno == EINTR);
	if (rc == 0) {
		errno = ETIMEDOUT;
		goto fail;
	}
	if (rc < 0 || (!(pfd.revents & POLLIN) &&
		(pfd.revents & (POLLERR | POLLHUP | POLLNVAL))))
		goto fail;
	length = recv(fd, response, sizeof(*response), 0);
	if (length != (ssize_t)sizeof(*response)) {
		errno = EBADMSG;
		goto fail;
	}
	close(fd);
	if (response->magic != MICRONUX_DEVICE_MAGIC ||
		response->request_id != request.request_id ||
		response->abi_major != MICRONUX_DEVICE_ABI_MAJOR) {
		errno = EPROTO;
		return -1;
	}
	if (response->status != MICRONUX_DEVICE_STATUS_OK) {
		errno = status_errno(response->status);
		return -1;
	}
	return 0;

fail:
	close(fd);
	return -1;
}

int micronux_device_get_info(struct micronux_device_info *info)
{
	struct micronux_device_response response;

	if (info == NULL) {
		errno = EINVAL;
		return -1;
	}
	if (exchange(MICRONUX_DEVICE_OP_INFO, 0, 0, &response) < 0)
		return -1;
	info->abi_major = response.abi_major;
	info->abi_minor = response.abi_minor;
	info->capabilities = response.capabilities;
	info->devices = response.devices;
	return 0;
}

int micronux_device_list(uint32_t *devices)
{
	struct micronux_device_response response;

	if (devices == NULL) {
		errno = EINVAL;
		return -1;
	}
	if (exchange(MICRONUX_DEVICE_OP_LIST, 0, 0, &response) < 0)
		return -1;
	*devices = response.devices;
	return 0;
}

int micronux_device_wait(uint32_t required_devices, uint32_t timeout_ms,
		uint32_t *devices)
{
	struct micronux_device_response response;

	if (devices == NULL || required_devices == 0 ||
		timeout_ms == 0 || timeout_ms > MICRONUX_DEVICE_MAX_WAIT_MS) {
		errno = EINVAL;
		return -1;
	}
	if (exchange(MICRONUX_DEVICE_OP_WAIT, required_devices, timeout_ms,
			&response) < 0)
		return -1;
	*devices = response.devices;
	return 0;
}

int micronux_device_admin_probe(void)
{
	struct micronux_device_response response;

	return exchange(MICRONUX_DEVICE_OP_ADMIN_PROBE, 0, 0, &response);
}
