# Production deployment

This document covers the production runtime, automated deployment, backup/restore, lightweight
monitoring, and operational hardening for a single Amazon Lightsail Ubuntu 24.04 VPS.

## Architecture

```text
Internet
  │ HTTPS :443 (and HTTP :80 for redirects/certificate issuance)
  ▼
Caddy ── internal Docker network ── dashboard:8000

scheduler ── outbound-only application network ── RSS/OpenAI
  ├── app_data:/data/state
  └── generated_outputs:/data/outputs

dashboard
  └── generated_outputs:/data/outputs
```

Only Caddy publishes host ports. The dashboard is reachable only from Caddy on the internal
Docker network, and the scheduler publishes no port. Caddy obtains and renews HTTPS certificates
automatically after DNS points the configured domain to the VPS and ports 80/443 are reachable.

The deployment runs exactly one scheduler container. **Never use `--scale scheduler=...` or start
a second production Compose project.** The scheduler lock is process-local, so multiple instances
can duplicate paid OpenAI text and image generation.

## Services and persistent data

- `scheduler` runs `python -m app.runtime run`, handles SIGTERM/SIGINT, and uses the existing local
  database/writability health check. The check never contacts RSS or OpenAI.
- `dashboard` runs the existing FastAPI application on container port 8000. Its `/health` endpoint
  performs no paid work. The generated output mount is writable so authenticated editors can save
  revisions, approve drafts, and continue scene-image generation.
- `caddy` terminates HTTPS and proxies requests to `dashboard:8000`.
- `app_data` stores SQLite and scheduler state.
- `generated_outputs` stores images, `manifest.json`, and `run.json`.
- `caddy_data` and `caddy_config` retain Caddy certificates and runtime configuration.

Normal `docker compose down` preserves all named volumes. Do not use `down -v` in production.

## Configuration

Create the untracked production environment file and restrict its permissions:

```bash
cp .env.example .env
chmod 600 .env
```

Set every empty value required by the selected pipeline, especially:

- `APP_DOMAIN`: public DNS name, without `https://` or a path.
- `APP_IMAGE`: GHCR image tag used by both application services. Use an immutable SHA tag in
  production rather than relying only on `latest`.
- `DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`: credentials protecting every dashboard and API
  route except `/health`. Generate a long, unique password and leave
  `DASHBOARD_AUTH_REQUIRED=true` in production.
- `OPENAI_API_KEY`: required when `SCHEDULER_PROVIDER=openai`; keep it only in `.env` on the VPS.
- `SCHEDULER_FEED_URLS`: comma-separated HTTP(S) RSS/Atom URLs.
- `SCHEDULER_RELEVANCE_TARGET`: scoring target used by the news pipeline.
- `YOUTUBE_CHANNEL_FOCUS`: required for `PIPELINE_MODE=end_to_end`.

The example defaults production to `SCHEDULER_PROVIDER=openai` and `PIPELINE_MODE=end_to_end`.
`PIPELINE_NEWS_LIMIT` caps both the number of articles summarized/scored per run and the final
priority-news selection, so a large first feed import cannot create unbounded text-provider calls.
`YOUTUBE_SCENE_LIMIT=50` is a hard pre-request cost guard; lower it before the first production run
if desired. `OPENAI_MODEL`, `OPENAI_IMAGE_MODEL`, item counts, interval, image size, and `TZ` can be
changed in `.env`. Never commit `.env` or paste it into logs.

`DOCKER_LOG_MAX_SIZE` and `DOCKER_LOG_MAX_FILES` control the `json-file` rotation applied to every
production service. The defaults retain five 10 MB files per container. Application, scheduler,
dashboard, and Caddy process output remains on stdout/stderr and is collected by Docker.

For a deterministic no-cost validation environment, override `SCHEDULER_PROVIDER=local`. Health
checks themselves never call providers or download feeds.

## Published images

After the `CI` workflow succeeds for a push to `main`, the `Publish production image` workflow
builds the validated commit once and publishes both of these tags:

```text
ghcr.io/ecokiyoshi/ai-news-intelligence:latest
ghcr.io/ecokiyoshi/ai-news-intelligence:sha-<40-character-commit-sha>
```

The workflow authenticates with the repository-scoped `GITHUB_TOKEN`; no personal access token is
required. Its permissions are limited to reading repository contents and writing packages. Pull
request workflows never publish an image.

The package may be private depending on its GHCR visibility. Authenticate Docker with a token that
can read the package before pulling a private image. Do not store that token in this repository.

Pin the VPS to the immutable tag recorded by the successful workflow:

```dotenv
APP_IMAGE=ghcr.io/ecokiyoshi/ai-news-intelligence:sha-0123456789abcdef0123456789abcdef01234567
```

`latest` is convenient for discovery but can move after every successful `main` build. An
immutable SHA tag makes the deployed application version auditable and allows rollback to a
previously published SHA tag.

## Create the Amazon Lightsail instance

In the AWS console, create a Lightsail Linux/Unix instance with the **OS only / Ubuntu 24.04 LTS**
blueprint. Use the AWS Region closest to the intended audience (normally Tokyo for this project)
and select a bundle with at least 2 vCPU, 4 GB RAM, and 40 GB persistent disk. Keep only one
production instance running the scheduler.

After the instance becomes ready:

1. Allocate a Lightsail static IPv4 address in the same Region and attach it to the instance. The
   instance's original dynamic public IP can change after a stop/start, so do not use it for DNS or
   GitHub Actions secrets.
2. In the instance's **Networking** tab, configure the Lightsail IPv4 firewall with TCP 22, TCP 80,
   TCP 443, and UDP 443. Restrict TCP 22 to trusted administrator addresses while bootstrapping.
   GitHub-hosted runners use changing egress addresses; before enabling automated deployment,
   choose the SSH access model described under **Configure GitHub Actions secrets**.
3. Do not enable public port 8000. The Compose stack exposes only Caddy on 80/443.
4. Create an `A` record for `APP_DOMAIN` pointing to the attached static IPv4 address. Add `AAAA`
   only if Lightsail IPv6 is deliberately enabled and protected by equivalent firewall rules.

Lightsail's browser SSH session and downloaded default key initially connect to an Ubuntu instance
as `ubuntu`. Use that administrative account only for bootstrapping; automated deployments use the
dedicated, non-root `deploy` account created below.

## Bootstrap Ubuntu

Copy the tracked bootstrap script to the new instance using the static IP, then execute it from the
browser SSH session or a trusted administrator terminal:

```bash
scp -i <lightsail-key.pem> deploy/lightsail-bootstrap.sh ubuntu@<static-ip>:/tmp/
ssh -i <lightsail-key.pem> ubuntu@<static-ip>
sudo bash /tmp/lightsail-bootstrap.sh
```

The script installs Docker Engine and the Compose plugin from Docker's official Ubuntu repository,
creates the `deploy` user and application directories, and enables UFW for SSH, HTTP, and HTTPS.
It is safe to run again after an interrupted installation. If SSH uses a non-default port, preserve
access by running it as `sudo SSH_PORT=<port> bash /tmp/lightsail-bootstrap.sh` and configure the
same port in the Lightsail firewall before enabling UFW.

The Lightsail firewall and UFW are independent layers; the required port must be allowed in both.
Docker-published ports can bypass some UFW rules. This Compose file intentionally publishes only
Caddy's 80/443 TCP and 443 UDP ports; it never publishes dashboard port 8000.

## Configure the deployment account

Generate a dedicated Ed25519 key on a trusted administrator machine. Use a separate key from the
Lightsail default key, protect the private key, and never commit it:

```bash
ssh-keygen -t ed25519 -f ./lightsail-github-deploy -C github-actions-production
```

From the existing `ubuntu` session, install only the public key. Prefix it with OpenSSH `restrict`
to disable PTY allocation and forwarding while retaining non-interactive deployment commands:

```bash
sudo sh -c 'printf "restrict %s\\n" "<contents-of-lightsail-github-deploy.pub>" >> /home/deploy/.ssh/authorized_keys'
sudo chown deploy:deploy /home/deploy/.ssh/authorized_keys
sudo chmod 600 /home/deploy/.ssh/authorized_keys
```

Open a second terminal and verify login and Docker access before closing the administrative session:

```bash
ssh -i ./lightsail-github-deploy deploy@<static-ip> 'docker version && docker compose version'
```

Save the private key contents for the GitHub `VPS_SSH_KEY` secret only after this succeeds. The
downloaded Lightsail key remains an administrator recovery credential and must not be used by the
deployment workflow.

## Manual Ubuntu provisioning reference

The bootstrap script above automates this section. These commands are retained as an auditable
manual reference for rebuilding a host without the script.

Use Ubuntu 24.04 with at least 2 vCPU, 4 GB RAM, and 40 GB of persistent disk. Create a non-root
deployment user and the fixed application directory:

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo install -d -o deploy -g deploy -m 0750 /opt/ai-news-intelligence
sudo install -d -o deploy -g deploy -m 0750 /opt/ai-news-intelligence/deploy
sudo install -d -o deploy -g deploy -m 0750 /opt/ai-news-intelligence/scripts
```

Membership in the `docker` group is effectively root-level access. Reserve this account and its
SSH key for deployment. Log out and back in after adding the group before testing Docker.

Install Docker Engine and the Compose plugin from Docker's official Ubuntu repository:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker deploy
docker version
docker compose version
```

Add the dedicated public key to `/home/deploy/.ssh/authorized_keys`, owned by `deploy`, with directory
mode `700` and file mode `600`, as described above. Confirm a second key-authenticated SSH session
works before changing SSH settings. Never expose the Docker socket over TCP.

Allow the configured SSH port plus Caddy's HTTP/HTTPS ports, then enable the firewall. Replace
`22` if `VPS_PORT` is different:

```bash
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
sudo ufw status verbose
```

Wait for the `A` record created during Lightsail setup to propagate before the first deployment so
Caddy can obtain a certificate.

## Configure production state and registry access

From a trusted checkout, copy only the unpopulated template to the VPS before the first automated
deployment:

```bash
scp -P <port> .env.example deploy@<host>:/opt/ai-news-intelligence/.env
ssh -p <port> deploy@<host> 'chmod 600 /opt/ai-news-intelligence/.env'
```

On the VPS, populate the required runtime values described above. Do not enter secret values on the
local machine or pass them through `scp`. The automation updates only the `APP_IMAGE` line. It never
uploads, replaces, displays, or checks in the remaining runtime secrets.

If the GHCR package is private, authenticate once as `deploy` with a token that has only the
package read permission:

```bash
docker login ghcr.io -u ecokiyoshi
```

Paste the token at Docker's password prompt; do not put it directly in a command, shell history,
repository file, or GitHub Actions log. Confirm that the user can pull an immutable image.

## Configure GitHub Actions secrets

Configure these repository or `production` environment secrets:

- `VPS_HOST`: the VPS DNS name or IPv4 address.
- `VPS_USER`: the dedicated deployment user, normally `deploy`.
- `VPS_PORT`: the SSH TCP port, normally `22`.
- `VPS_SSH_KEY`: the complete private key for the matching authorized key.
- `VPS_KNOWN_HOSTS`: the verified OpenSSH known-hosts line for this host and port.

The included workflow runs on a GitHub-hosted runner and therefore needs an inbound SSH path to the
instance. GitHub's published runner address ranges change over time and are not recommended as a
long-lived allowlist. Prefer a self-hosted runner behind a stable address or a VPN/bastion. If the
standard workflow is used directly, TCP 22 must be reachable through both the Lightsail firewall
and UFW; use key-only authentication, the dedicated restricted deployment key, verified host keys,
and automated maintenance of any source-IP allowlist. Do not silently leave SSH open to the world
after initial setup.

Obtain the server's Ed25519 host-key fingerprint on the VPS with
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`. On a trusted administrator machine, collect the
known-hosts line with `ssh-keyscan -p <port> <host>`, then compare its fingerprint with the VPS
value through an independent channel before saving the line. `ssh-keyscan` alone does not verify
identity. The workflow requires strict host-key checking and does not use trust-on-first-use.

The workflow uses GitHub's `production` environment so deployments have their own audit trail and
can use environment protection rules. Add required reviewers there when manual production approval
is desired. Do not store `OPENAI_API_KEY` or the GHCR read token in GitHub for this workflow; they
remain on the VPS.

## Automatic and first deployment

The workflow chain is:

```text
CI succeeds on main
  -> Publish production image succeeds for that exact commit
    -> Deploy production checks out that commit and sends its immutable SHA tag to the VPS
```

Only a successful push-driven publication can deploy. Production concurrency is one and queued
deployments are not cancelled mid-flight. The workflow uploads only the Compose file, Caddyfile,
and deployment scripts over strictly verified SSH. Files are staged under a commit-specific
incoming directory and promoted only while holding the same exclusive lock used by backup and
restore. It does not build on the VPS or transfer `.env`.

For the first deployment, complete all VPS, DNS, `.env`, GHCR login, SSH, and GitHub secret setup,
then merge a validated change to `main` or rerun the successful `Publish production image` workflow.
Follow the `Deploy production` run until the immutable image, all three healthy containers, and
the public `https://<APP_DOMAIN>/health` route are confirmed.

For an authorized manual deployment on the VPS, place the tracked deployment files under
`/opt/ai-news-intelligence`, then run:

```bash
cd /opt/ai-news-intelligence
./scripts/deploy.sh \
  ghcr.io/ecokiyoshi/ai-news-intelligence:sha-0123456789abcdef0123456789abcdef01234567
```

The script validates its inputs and Compose model, writes only `APP_IMAGE` in `.env`, pulls images,
runs `docker compose up -d --no-build --remove-orphans`, checks exactly one healthy
scheduler/dashboard/Caddy, verifies both application containers use the requested immutable image,
and checks the public health route. Any failure exits non-zero and prints status plus recent service
logs. It never builds on the VPS, runs `docker compose down -v`, or deletes a named volume.

## Roll back

Choose the immutable SHA tag from a previously successful `Publish production image` run and run:

```bash
cd /opt/ai-news-intelligence
./scripts/rollback.sh sha-0123456789abcdef0123456789abcdef01234567
```

Rollback uses the same pull, start, and health verification path as deployment. It changes only the
application image selection and containers; persistent state, generated outputs, and Caddy data are
retained. A moving `latest` tag is deliberately rejected. Rollback does not reverse database or
output schema changes, so check release notes before crossing a migration boundary.

## Operational status and health

Run the status command from the deployment directory:

```bash
cd /opt/ai-news-intelligence
./scripts/status.sh
```

It reports Compose status, health, recent scheduler/dashboard logs, host filesystem usage, named
volume usage, and Docker disk usage. It reads no environment values except the non-secret public
domain and never prints `.env`, container environments, tokens, or credentials. Set
`STATUS_LOG_LINES` for a different log tail length.

The lightweight health command is suitable for cron, a systemd timer, or an external monitor:

```bash
./scripts/healthcheck.sh
echo "$?"  # 0 healthy; non-zero unhealthy
```

It requires exactly one healthy scheduler, dashboard, and Caddy container and a successful public
HTTPS `/health` response. The check never starts a pipeline, downloads a feed, or contacts OpenAI.
For a simple local alert hook, run it every five minutes and send only the exit status to the chosen
alerting service. Use an independent uptime monitor for the public URL so a full VPS/network outage
is still detected. Also monitor the repository's `CI`, `Publish production image`, and
`Deploy production` GitHub Actions runs; deployment failures do not automatically roll back data.

Useful failure inspection commands are:

```bash
./scripts/status.sh
docker compose --env-file .env -f compose.production.yaml logs --since=1h scheduler
docker compose --env-file .env -f compose.production.yaml logs --since=1h dashboard
```

## Back up persistent application data

`backup.sh` archives only the `app_data` and `generated_outputs` named volumes. It never copies
`.env`, SSH material, Docker credentials, API keys, or container environment values. The scheduler
is stopped during the short critical section so SQLite/state and generated outputs represent one
consistent point; dashboard and Caddy remain available. A scheduler that was already stopped is
left stopped. A shared non-blocking lock prevents deploy, rollback, backup, and restore operations
from modifying the stack concurrently.

```bash
cd /opt/ai-news-intelligence
./scripts/backup.sh
```

The default destination is `/opt/ai-news-intelligence/backups`, with one UTC timestamped directory:

```text
backups/2026-08-09_120000Z/
├── app_data.tar.gz
├── generated_outputs.tar.gz
├── manifest.txt
└── SHA256SUMS
```

Use a separate mounted disk when available and pass its absolute path explicitly:

```bash
BACKUP_DIR=/mnt/ai-news-backups ./scripts/backup.sh
```

A backup stored only on the VPS does not protect against instance or disk loss. Copy completed
timestamp directories to encrypted off-host storage with access controls and lifecycle policies.
Do not copy `.env` or Docker/SSH credential directories with them.

Optional retention deletes only direct, timestamp-named children of the resolved backup root:

```bash
BACKUP_DIR=/mnt/ai-news-backups BACKUP_RETENTION_DAYS=30 ./scripts/backup.sh
```

If `BACKUP_RETENTION_DAYS` is unset, no backup is removed. Retention never deletes generated
project data or anything outside `BACKUP_DIR`. Monitor both the backup destination and Docker data
filesystem so a failed or full backup disk is detected before recovery is needed.

## Validate and restore a backup

Before restoring, create and move a fresh backup off-host whenever the current state is still
readable. `restore.sh` requires a direct child of `BACKUP_DIR`, validates the format, exact artifact
set, SHA-256 checksums, archive paths, and member types before stopping services. It rejects links,
special files, malformed directories, and non-interactive use without `--force`.

Interactive restore:

```bash
cd /opt/ai-news-intelligence
./scripts/restore.sh backups/2026-08-09_120000Z
# Type RESTORE only after checking the warning and selected path.
```

Explicit non-interactive restore:

```bash
BACKUP_DIR=/mnt/ai-news-backups \
  ./scripts/restore.sh --force /mnt/ai-news-backups/2026-08-09_120000Z
```

Restore safely stops scheduler and dashboard, replaces the contents of the two existing named
volumes, restarts scheduler/dashboard/Caddy without building, and waits for container plus public
health. It does not delete or recreate a Docker volume. A failure prints current service state and
recent logs and attempts to restart the stack. Keep the selected backup until application behavior
and several scheduled runs have been verified.

Test the restore procedure periodically on a separate non-production Compose project or disposable
VPS. A backup is not considered reliable until its checksums and a complete restore have succeeded.

## Disk growth and manual output cleanup

Generated scene images are the main unbounded project data. Measure storage before each cleanup:

```bash
./scripts/status.sh
docker system df -v
```

There is intentionally no automatic deletion of generated projects. To remove an obsolete run,
first create a successful backup and record the exact run ID. Stop the scheduler so it cannot write
the selected directory while it is removed, validate the run-ID shape, then use a one-off container
that mounts the existing output volume without starting the scheduler process:

```bash
RUN_ID=20260809T120000000000Z-0123456789abcdef
[[ "${RUN_ID}" =~ ^[A-Za-z0-9._-]+$ ]] || exit 1
./scripts/backup.sh
docker compose --env-file .env -f compose.production.yaml stop --timeout 60 scheduler
docker compose --env-file .env -f compose.production.yaml run --rm --no-deps \
  --entrypoint sh scheduler -ceu 'rm -rf -- "/data/outputs/$1"' sh "${RUN_ID}"
docker compose --env-file .env -f compose.production.yaml up -d --no-build scheduler
./scripts/healthcheck.sh
```

Never delete the entire output volume, use `docker compose down -v`, or run a broad wildcard cleanup.
Docker image/build-cache cleanup is separate from application data; inspect `docker system df -v`
and remove only understood, unused artifacts.

## Reboot resilience

The bootstrap enables Docker at boot and every production service uses `restart: unless-stopped`.
After kernel/Docker maintenance, verify the complete path rather than assuming restart succeeded:

```bash
sudo systemctl is-enabled docker
sudo systemctl is-active docker
cd /opt/ai-news-intelligence
./scripts/healthcheck.sh
./scripts/status.sh
```

A container deliberately stopped by an operator may remain stopped after reboot. Recover the
declared stack with `docker compose --env-file .env -f compose.production.yaml up -d --no-build`,
then rerun the health check. Test one controlled VPS reboot after the first deployment and after
major Docker/Ubuntu upgrades.

## Production security checklist

- Use a dedicated restricted SSH key and non-root deployment account; disable password SSH after
  key-based recovery access is verified.
- Keep Ubuntu, Docker Engine, and the Compose plugin updated through a tested maintenance window.
- Keep both Lightsail firewall and UFW enabled; expose only the configured SSH port plus 80/443.
- Never expose the Docker daemon, dashboard port 8000, SQLite, or scheduler services publicly.
- Keep `.env`, GHCR credentials, SSH private keys, and API tokens out of Git and backups.
- Rotate SSH, GHCR, and provider credentials regularly and immediately after suspected exposure.
- Retain encrypted off-host backups, monitor disk capacity, and test restoration regularly.
- Preserve exactly one scheduler instance to prevent duplicate paid pipeline execution.

## Validate and operate

Validate interpolation and the rendered Compose model before starting:

```bash
docker compose --env-file .env -f compose.production.yaml config --quiet
```

Pull the selected image, then start all three services:

```bash
docker compose --env-file .env -f compose.production.yaml pull scheduler dashboard
docker compose --env-file .env -f compose.production.yaml up -d
docker compose --env-file .env -f compose.production.yaml ps
```

For local production-stack development, the retained `build` configuration still supports:

```bash
docker compose --env-file .env -f compose.production.yaml up -d --build
```

Stop, restart, or remove containers while retaining data:

```bash
docker compose --env-file .env -f compose.production.yaml stop
docker compose --env-file .env -f compose.production.yaml restart
docker compose --env-file .env -f compose.production.yaml down
```

Inspect status and logs without printing environment variables:

```bash
docker compose --env-file .env -f compose.production.yaml ps
docker compose --env-file .env -f compose.production.yaml logs --tail=200 scheduler
docker compose --env-file .env -f compose.production.yaml logs --tail=200 dashboard
docker compose --env-file .env -f compose.production.yaml logs --tail=200 caddy
```

Check health locally on the VPS and through the public route:

```bash
docker compose --env-file .env -f compose.production.yaml exec dashboard \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
curl --fail --show-error "https://${APP_DOMAIN}/health"
```

The public response is `{"status":"ok"}`. A health check never starts a scheduled or one-shot
pipeline. Use `docker compose ... ps` and the recent service logs when a container is unhealthy.

Production does not expose port 8000, SQLite, scheduler endpoints, or the Docker socket; it also
uses no privileged containers or arbitrary host-directory mounts. The only bind mount is the
read-only tracked Caddyfile.
