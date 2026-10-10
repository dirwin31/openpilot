# AGNOS kernel changes for the on-device browser

The Google sign-in browser runs Chromium inside a sandbox, which keeps it walled
off from the rest of the comma. That sandbox relies on Linux kernel features the
stock AGNOS kernel has turned off, so the browser won't start without them.

## Changes

Turn these on in the kernel config:

```text
CONFIG_SYSVIPC=y
CONFIG_IPC_NS=y
CONFIG_UTS_NS=y
CONFIG_USER_NS=y
CONFIG_PID_NS=y
```

| Setting | Why it's needed |
| --- | --- |
| `USER_NS` | Lets the browser create its sandbox without root. |
| `PID_NS` | Gives the browser its own process list, so it can't see or signal openpilot's processes. |
| `IPC_NS` | Gives the browser its own shared-memory space, separate from the rest of the system. |
| `UTS_NS` | Gives the sandbox its own hostname, which bubblewrap expects to be able to set. |
| `SYSVIPC` | Shared memory the browser's display uses, and required for `IPC_NS`. |

These were already on and must stay on: `NAMESPACES`, `NET_NS`, `SECCOMP` and
`SECCOMP_FILTER`. Turning on `SYSVIPC` also turns on `SYSVIPC_SYSCTL` and
`SYSVIPC_COMPAT` automatically.

Nothing else in the kernel changes.
