# Security and private data

The UI is a local single-user service. It binds loopback, checks Host/Origin,
and limits API file access to its own workspace. It has no remote authentication
or multiuser isolation. Keep it local; a reverse proxy or public tunnel changes
that trust boundary.

Report vulnerabilities privately using this repository's GitHub **Security →
Report a vulnerability** feature when enabled, or contact the repository owner
privately. For ordinary bugs, open a redacted issue. Do not attach customer dumps,
backup archives, credentials, VINs, adapter serials, or proprietary binaries.

Only the current development release is maintained. Automatic tests cannot
establish the electrical wiring, hardware recovery path, or validity of firmware.
