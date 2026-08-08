// SPDX-License-Identifier: MIT
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <net/if.h>
#include <net/if_arp.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <unistd.h>

#define SERIAL_DEVICE "/dev/esps0"
#define NETWORK_DEVICE "ethsta0"
#define BUFFER_CAPACITY 768
#define RPC_TIMEOUT_MS 10000

#define RPC_TYPE_REQUEST 1
#define RPC_TYPE_RESPONSE 2
#define RPC_RESPONSE_OFFSET 256
#define RPC_GET_MAC_ADDRESS 257
#define RPC_SET_WIFI_MODE 260
#define RPC_WIFI_INIT 278
#define RPC_WIFI_START 280
#define RPC_WIFI_CONNECT 282
#define RPC_WIFI_SET_CONFIG 284

#define WIFI_INTERFACE_STA 0
#define WIFI_MODE_STA 1
#define WIFI_INIT_CONFIG_MAGIC 0x1f2f3f4fU

struct buffer {
	uint8_t data[BUFFER_CAPACITY];
	size_t length;
};

struct pb_field {
	uint32_t number;
	uint8_t wire_type;
	uint64_t varint;
	const uint8_t *bytes;
	size_t bytes_length;
};

static uint32_t next_uid = 1;

static int buffer_append(struct buffer *buffer, const void *data, size_t length)
{
	if (length > sizeof(buffer->data) - buffer->length) {
		errno = EMSGSIZE;
		return -1;
	}
	memcpy(buffer->data + buffer->length, data, length);
	buffer->length += length;
	return 0;
}

static int buffer_u8(struct buffer *buffer, uint8_t value)
{
	return buffer_append(buffer, &value, sizeof(value));
}

static int buffer_u16_le(struct buffer *buffer, uint16_t value)
{
	uint8_t encoded[2] = {
		(uint8_t)value,
		(uint8_t)(value >> 8),
	};

	return buffer_append(buffer, encoded, sizeof(encoded));
}

static int pb_varint(struct buffer *buffer, uint64_t value)
{
	do {
		uint8_t byte = (uint8_t)(value & 0x7fU);

		value >>= 7;
		if (value != 0)
			byte |= 0x80U;
		if (buffer_u8(buffer, byte) < 0)
			return -1;
	} while (value != 0);
	return 0;
}

static int pb_key(struct buffer *buffer, uint32_t field, uint8_t wire_type)
{
	return pb_varint(buffer, ((uint64_t)field << 3) | wire_type);
}

static int pb_uint(struct buffer *buffer, uint32_t field, uint64_t value)
{
	if (value == 0)
		return 0;
	if (pb_key(buffer, field, 0) < 0)
		return -1;
	return pb_varint(buffer, value);
}

static int pb_bytes(struct buffer *buffer, uint32_t field,
		const void *data, size_t length)
{
	if (pb_key(buffer, field, 2) < 0 || pb_varint(buffer, length) < 0)
		return -1;
	return buffer_append(buffer, data, length);
}

static int pb_message(struct buffer *buffer, uint32_t field,
		const struct buffer *message)
{
	return pb_bytes(buffer, field, message->data, message->length);
}

static int decode_varint(const uint8_t *data, size_t length, size_t *offset,
		uint64_t *value)
{
	uint64_t result = 0;
	unsigned int shift = 0;

	while (*offset < length && shift < 64) {
		uint8_t byte = data[(*offset)++];

		result |= (uint64_t)(byte & 0x7fU) << shift;
		if ((byte & 0x80U) == 0) {
			*value = result;
			return 0;
		}
		shift += 7;
	}
	errno = EBADMSG;
	return -1;
}

static int pb_next(const uint8_t *data, size_t length, size_t *offset,
		struct pb_field *field)
{
	uint64_t key;
	uint64_t bytes_length;

	memset(field, 0, sizeof(*field));
	if (decode_varint(data, length, offset, &key) < 0)
		return -1;
	field->number = (uint32_t)(key >> 3);
	field->wire_type = (uint8_t)(key & 0x07U);
	if (field->number == 0) {
		errno = EBADMSG;
		return -1;
	}
	if (field->wire_type == 0)
		return decode_varint(data, length, offset, &field->varint);
	if (field->wire_type != 2 ||
		decode_varint(data, length, offset, &bytes_length) < 0 ||
		bytes_length > length - *offset) {
		errno = EBADMSG;
		return -1;
	}
	field->bytes = data + *offset;
	field->bytes_length = (size_t)bytes_length;
	*offset += field->bytes_length;
	return 0;
}

static int wait_fd(int fd, short events, int timeout_ms)
{
	struct pollfd pfd = {.fd = fd, .events = events};
	int rc;

	do {
		rc = poll(&pfd, 1, timeout_ms);
	} while (rc < 0 && errno == EINTR);
	if (rc <= 0)
		return rc;
	if (pfd.revents & (POLLERR | POLLHUP | POLLNVAL)) {
		errno = EIO;
		return -1;
	}
	return (pfd.revents & events) != 0;
}

static int write_all(int fd, const uint8_t *data, size_t length)
{
	size_t done = 0;

	while (done < length) {
		ssize_t rc = write(fd, data + done, length - done);

		if (rc > 0) {
			done += (size_t)rc;
			continue;
		}
		if (rc < 0 && errno == EINTR)
			continue;
		if (rc < 0 && (errno == EAGAIN || errno == EWOULDBLOCK) &&
			wait_fd(fd, POLLOUT, RPC_TIMEOUT_MS) > 0)
			continue;
		return -1;
	}
	return 0;
}

static ssize_t read_frame(int fd, uint8_t *data, size_t capacity)
{
	ssize_t total = 0;
	int timeout_ms = RPC_TIMEOUT_MS;

	while ((size_t)total < capacity) {
		int ready = wait_fd(fd, POLLIN, timeout_ms);
		ssize_t rc;

		if (ready < 0)
			return -1;
		if (ready == 0)
			return total;
		rc = read(fd, data + total, capacity - (size_t)total);
		if (rc > 0) {
			total += rc;
			timeout_ms = 50;
			continue;
		}
		if (rc < 0 && (errno == EINTR || errno == EAGAIN ||
				errno == EWOULDBLOCK))
			continue;
		return rc < 0 ? -1 : total;
	}
	return total;
}

static int unwrap_pserial(const uint8_t *frame, size_t frame_length,
		const uint8_t **protobuf, size_t *protobuf_length)
{
	static const uint8_t endpoint[] = "RPCRsp";
	size_t offset = 0;
	uint16_t length;

	if (frame_length < 12 || frame[offset++] != 1)
		goto bad;
	length = (uint16_t)frame[offset] | ((uint16_t)frame[offset + 1] << 8);
	offset += 2;
	if (length != sizeof(endpoint) - 1 || length > frame_length - offset ||
		memcmp(frame + offset, endpoint, length) != 0)
		goto bad;
	offset += length;
	if (offset + 3 > frame_length || frame[offset++] != 2)
		goto bad;
	length = (uint16_t)frame[offset] | ((uint16_t)frame[offset + 1] << 8);
	offset += 2;
	if (length > frame_length - offset)
		goto bad;
	*protobuf = frame + offset;
	*protobuf_length = length;
	return 0;

bad:
	errno = EBADMSG;
	return -1;
}

static int rpc_exchange(int fd, uint32_t request_id,
		const struct buffer *request_payload, struct buffer *response_payload)
{
	static const uint8_t endpoint[] = "RPCRsp";
	struct buffer protobuf = {0};
	struct buffer pserial = {0};
	uint8_t frame[BUFFER_CAPACITY];
	uint32_t uid = next_uid++;
	uint32_t response_id = request_id + RPC_RESPONSE_OFFSET;
	ssize_t frame_length;
	const uint8_t *response;
	size_t response_length;
	size_t offset = 0;
	uint64_t message_type = 0;
	uint64_t message_id = 0;
	uint64_t response_uid = 0;
	bool have_response_payload = false;
	struct pb_field field;

	if (pb_uint(&protobuf, 1, RPC_TYPE_REQUEST) < 0 ||
		pb_uint(&protobuf, 2, request_id) < 0 ||
		pb_uint(&protobuf, 3, uid) < 0 ||
		pb_message(&protobuf, request_id, request_payload) < 0 ||
		buffer_u8(&pserial, 1) < 0 ||
		buffer_u16_le(&pserial, sizeof(endpoint) - 1) < 0 ||
		buffer_append(&pserial, endpoint, sizeof(endpoint) - 1) < 0 ||
		buffer_u8(&pserial, 2) < 0 ||
		buffer_u16_le(&pserial, (uint16_t)protobuf.length) < 0 ||
		buffer_append(&pserial, protobuf.data, protobuf.length) < 0)
		return -1;
	if (write_all(fd, pserial.data, pserial.length) < 0)
		return -1;
	frame_length = read_frame(fd, frame, sizeof(frame));
	if (frame_length <= 0) {
		if (frame_length == 0)
			errno = ETIMEDOUT;
		return -1;
	}
	if (unwrap_pserial(frame, (size_t)frame_length, &response,
			&response_length) < 0)
		return -1;

	response_payload->length = 0;
	while (offset < response_length) {
		if (pb_next(response, response_length, &offset, &field) < 0)
			return -1;
		if (field.number == 1 && field.wire_type == 0)
			message_type = field.varint;
		else if (field.number == 2 && field.wire_type == 0)
			message_id = field.varint;
		else if (field.number == 3 && field.wire_type == 0)
			response_uid = field.varint;
		else if (field.number == response_id && field.wire_type == 2) {
			have_response_payload = true;
			if (buffer_append(response_payload, field.bytes,
					field.bytes_length) < 0)
				return -1;
		}
	}
	if (message_type != RPC_TYPE_RESPONSE || message_id != response_id ||
		response_uid != uid || !have_response_payload) {
		errno = EBADMSG;
		return -1;
	}
	return 0;
}

static int response_status(const struct buffer *response, uint32_t field_number,
		uint32_t *status)
{
	size_t offset = 0;
	struct pb_field field;

	*status = 0;
	while (offset < response->length) {
		if (pb_next(response->data, response->length, &offset, &field) < 0)
			return -1;
		if (field.number == field_number && field.wire_type == 0) {
			*status = (uint32_t)field.varint;
			return 0;
		}
	}
	return 0;
}

static int rpc_status_call(int fd, uint32_t request_id,
		const struct buffer *payload)
{
	struct buffer response = {0};
	uint32_t status;

	if (rpc_exchange(fd, request_id, payload, &response) < 0 ||
		response_status(&response, 1, &status) < 0)
		return -1;
	if (status != 0) {
		fprintf(stderr, "micronux-netctl: RPC %u failed: 0x%04x\n",
			request_id, status);
		errno = EREMOTEIO;
		return -1;
	}
	return 0;
}

static int rpc_wifi_init(int fd)
{
	struct buffer config = {0};
	struct buffer request = {0};

	/* ESP-IDF 5.3 C6 defaults, kept conservative for the factory firmware. */
	if (pb_uint(&config, 1, 10) < 0 || pb_uint(&config, 2, 32) < 0 ||
		pb_uint(&config, 3, 1) < 0 || pb_uint(&config, 5, 32) < 0 ||
		pb_uint(&config, 8, 1) < 0 || pb_uint(&config, 9, 1) < 0 ||
		pb_uint(&config, 11, 1) < 0 || pb_uint(&config, 13, 6) < 0 ||
		pb_uint(&config, 15, 752) < 0 || pb_uint(&config, 16, 32) < 0 ||
		pb_uint(&config, 17, 0xa1) < 0 || pb_uint(&config, 18, 1) < 0 ||
		pb_uint(&config, 19, 7) < 0 ||
		pb_uint(&config, 20, WIFI_INIT_CONFIG_MAGIC) < 0 ||
		pb_uint(&config, 22, 5) < 0 || pb_uint(&config, 23, 1) < 0 ||
		pb_message(&request, 1, &config) < 0)
		return -1;
	return rpc_status_call(fd, RPC_WIFI_INIT, &request);
}

static int rpc_set_station_mode(int fd)
{
	struct buffer request = {0};

	if (pb_uint(&request, 1, WIFI_MODE_STA) < 0)
		return -1;
	return rpc_status_call(fd, RPC_SET_WIFI_MODE, &request);
}

static int rpc_get_station_mac(int fd, uint8_t mac[6])
{
	struct buffer request = {0};
	struct buffer response = {0};
	struct pb_field field;
	size_t offset = 0;
	uint32_t status = 0;
	bool have_mac = false;

	if (rpc_exchange(fd, RPC_GET_MAC_ADDRESS, &request, &response) < 0)
		return -1;
	while (offset < response.length) {
		if (pb_next(response.data, response.length, &offset, &field) < 0)
			return -1;
		if (field.number == 1 && field.wire_type == 2 &&
			field.bytes_length == 6) {
			memcpy(mac, field.bytes, 6);
			have_mac = true;
		} else if (field.number == 2 && field.wire_type == 0) {
			status = (uint32_t)field.varint;
		}
	}
	if (status != 0 || !have_mac) {
		fprintf(stderr, "micronux-netctl: get MAC failed: 0x%04x\n", status);
		errno = EREMOTEIO;
		return -1;
	}
	return 0;
}

static int rpc_set_station_config(int fd, const char *ssid, const char *password)
{
	struct buffer station = {0};
	struct buffer config = {0};
	struct buffer request = {0};
	size_t ssid_length = strlen(ssid);
	size_t password_length = strlen(password);

	if (ssid_length == 0 || ssid_length > 32 || password_length > 64) {
		errno = EINVAL;
		return -1;
	}
	if (pb_bytes(&station, 1, ssid, ssid_length) < 0 ||
		(password_length != 0 &&
		 pb_bytes(&station, 2, password, password_length) < 0) ||
		pb_message(&config, 2, &station) < 0 ||
		pb_uint(&request, 1, WIFI_INTERFACE_STA) < 0 ||
		pb_message(&request, 2, &config) < 0)
		return -1;
	return rpc_status_call(fd, RPC_WIFI_SET_CONFIG, &request);
}

static int set_linux_mac(const uint8_t mac[6])
{
	struct ifreq request;
	int fd = socket(AF_INET, SOCK_DGRAM, 0);
	int rc;

	if (fd < 0)
		return -1;
	memset(&request, 0, sizeof(request));
	strncpy(request.ifr_name, NETWORK_DEVICE, sizeof(request.ifr_name) - 1);
	request.ifr_hwaddr.sa_family = ARPHRD_ETHER;
	memcpy(request.ifr_hwaddr.sa_data, mac, 6);
	rc = ioctl(fd, SIOCSIFHWADDR, &request);
	close(fd);
	return rc;
}

static int set_linux_interface_up(void)
{
	struct ifreq request;
	int fd = socket(AF_INET, SOCK_DGRAM, 0);
	int rc = -1;

	if (fd < 0)
		return -1;
	memset(&request, 0, sizeof(request));
	strncpy(request.ifr_name, NETWORK_DEVICE, sizeof(request.ifr_name) - 1);
	if (ioctl(fd, SIOCGIFFLAGS, &request) == 0) {
		request.ifr_flags |= IFF_UP;
		rc = ioctl(fd, SIOCSIFFLAGS, &request);
	}
	close(fd);
	return rc;
}

static int initialize_station(int fd, uint8_t mac[6], bool start)
{
	struct buffer empty = {0};

	if (rpc_wifi_init(fd) < 0 || rpc_set_station_mode(fd) < 0 ||
		rpc_get_station_mac(fd, mac) < 0 || set_linux_mac(mac) < 0)
		return -1;
	if (start && (rpc_status_call(fd, RPC_WIFI_START, &empty) < 0 ||
		set_linux_interface_up() < 0))
		return -1;
	return 0;
}

static int command_mac_or_up(bool start)
{
	uint8_t mac[6];
	int fd = open(SERIAL_DEVICE, O_RDWR | O_NONBLOCK);
	int rc;

	if (fd < 0)
		goto fail;
	rc = initialize_station(fd, mac, start);
	close(fd);
	if (rc < 0)
		goto fail;
	printf("MICRONUX:M6:NET:%s name=%s mac=%02x:%02x:%02x:%02x:%02x:%02x\n",
		start ? "UP" : "MAC", NETWORK_DEVICE, mac[0], mac[1], mac[2],
		mac[3], mac[4], mac[5]);
	return 0;

fail:
	fprintf(stderr, "micronux-netctl: %s\n", strerror(errno));
	return 1;
}

static int command_connect(const char *ssid, const char *password)
{
	struct buffer empty = {0};
	uint8_t mac[6];
	int fd = open(SERIAL_DEVICE, O_RDWR | O_NONBLOCK);
	int rc = -1;

	if (fd < 0)
		goto out;
	if (initialize_station(fd, mac, false) < 0 ||
		rpc_set_station_config(fd, ssid, password) < 0 ||
		rpc_status_call(fd, RPC_WIFI_START, &empty) < 0 ||
		rpc_status_call(fd, RPC_WIFI_CONNECT, &empty) < 0 ||
		set_linux_interface_up() < 0)
		goto out;
	rc = 0;

out:
	if (fd >= 0)
		close(fd);
	if (rc < 0) {
		fprintf(stderr, "micronux-netctl: connect: %s\n", strerror(errno));
		return 1;
	}
	printf("MICRONUX:M6:NET:CONNECT state=requested name=%s "
		"mac=%02x:%02x:%02x:%02x:%02x:%02x ssid=%s\n",
		NETWORK_DEVICE, mac[0], mac[1], mac[2], mac[3], mac[4], mac[5],
		ssid);
	return 0;
}

static void usage(const char *program)
{
	fprintf(stderr,
		"usage: %s mac\n"
		"       %s up\n"
		"       %s connect SSID [PASSWORD]\n",
		program, program, program);
}

int main(int argc, char **argv)
{
	if (argc == 2 && strcmp(argv[1], "mac") == 0)
		return command_mac_or_up(false);
	if (argc == 2 && strcmp(argv[1], "up") == 0)
		return command_mac_or_up(true);
	if ((argc == 3 || argc == 4) && strcmp(argv[1], "connect") == 0)
		return command_connect(argv[2], argc == 4 ? argv[3] : "");
	usage(argv[0]);
	return 64;
}
