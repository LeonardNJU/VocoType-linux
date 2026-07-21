# VoCoType feedback service deployment

Production layout:

- application: `/opt/vocotype-feedback`
- virtualenv: `/opt/vocotype-feedback/.venv`
- configuration: `/etc/vocotype-feedback.env` (`0600`)
- database and private attachments: `/var/lib/vocotype-feedback`
- listener: `127.0.0.1:18088`
- public endpoint: `https://feedback.vocotype-linux.lsamc.website/v1/feedback`

After DNS points to the VPS, stage `nginx.conf` as
`/tmp/vocotype-feedback-nginx-tls.conf` and run:

```bash
sudo /tmp/vocotype-enable-feedback-tls
```

The activation script obtains a dedicated Let's Encrypt certificate, installs
the final HTTPS virtual host, validates Nginx, reloads it, and checks `/healthz`.

The service stores no plaintext source IP. It keeps HMAC hashes in a short-lived
rate-limit table and a pseudonymous installation hash with each report.

Operator commands:

```bash
sudo vocotype-feedback list --status new
sudo vocotype-feedback show fb_...
sudo vocotype-feedback status fb_... triaged --note "reproduced"
```

`vocotype-feedback-maintenance.timer` runs daily. It expires support bundles
after 30 days and writes consistent SQLite backups to
`/var/backups/vocotype-feedback`, retaining 14 days.
