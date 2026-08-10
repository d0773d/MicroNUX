/* SPDX-License-Identifier: MIT */

#ifndef MICRONUX_JOB_POLICY_H
#define MICRONUX_JOB_POLICY_H

#define MICRONUX_JOB_USER "micronux-job"
#define MICRONUX_JOB_UID 1000U
#define MICRONUX_JOB_GID 1000U

#define MICRONUX_JOB_WALL_MS 2000U
#define MICRONUX_JOB_CPU_SECONDS 3U
#define MICRONUX_JOB_NOFILE 16U
#define MICRONUX_JOB_NPROC 4U
#define MICRONUX_JOB_STACK_BYTES (256U * 1024U)
#define MICRONUX_JOB_DATA_BYTES (4U * 1024U * 1024U)
#define MICRONUX_JOB_ARENA_BYTES (6U * 1024U * 1024U)
#define MICRONUX_JOB_OUTPUT_BYTES (32U * 1024U)

#define MICRONUX_JOB_HELPER_PATH "/usr/libexec/micronux-job-exec"
#define MICRONUX_JOB_IGNITE_PATH "/usr/bin/micronux-ignite"
#define MICRONUX_JOB_APP_PREFIX "/opt/micronux/apps/"
#define MICRONUX_JOB_LOCK_PATH "/run/micronux-job.lock"

#endif
