# VoCoType feedback service deployment

The feedback receiver is a compiled C++ service using Boost.Beast, SQLite,
OpenSSL, and nlohmann/json. It does not install a Python interpreter or virtual
environment.

Production layout:

- executable: `/opt/vocotype-feedback/bin/vocotype-feedback`
- configuration: `/etc/vocotype-feedback.env` (`0600`)
- database and private attachments: `/var/lib/vocotype-feedback`
- listener: `127.0.0.1:18088`
- public endpoint: `https://feedback.vocotype-linux.lsamc.website/v1/feedback`

Build and install:

```bash
cmake -S feedback_service -B build/feedback-service   -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/vocotype-feedback
cmake --build build/feedback-service -j
sudo cmake --install build/feedback-service
```

Runtime libraries are provided by the server distribution: SQLite, OpenSSL,
libstdc++, and the standard C/C++ runtime. Boost.System is header-only in this
build.

After DNS points to the VPS, stage `nginx.conf` and run `enable-tls.sh` as
administrator. Nginx terminates TLS and proxies only `/`, `/healthz`, and
`/v1/feedback` to the loopback listener.

The service stores no plaintext source IP. It keeps HMAC hashes in a short-lived
rate-limit table and a pseudonymous installation hash with each report.

Operator commands:

```bash
sudo vocotype-feedback list --status new
sudo vocotype-feedback show fb_...
sudo vocotype-feedback status fb_... triaged --note "reproduced"
```

The maintenance timer expires private support bundles after 30 days and writes
consistent SQLite backups to `/var/backups/vocotype-feedback`, retaining 14
days.
