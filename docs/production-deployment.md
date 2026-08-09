# Production deployment

This document covers the production runtime and automated deployment to a single Ubuntu 24.04
VPS. Backup, restore, monitoring, and log-rotation automation are handled by a later phase.

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
  └── generated_outputs:/data/outputs:ro
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
  performs no paid work, and its generated output mount is read-only.
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
- `OPENAI_API_KEY`: required when `SCHEDULER_PROVIDER=openai`; keep it only in `.env` on the VPS.
- `SCHEDULER_FEED_URLS`: comma-separated HTTP(S) RSS/Atom URLs.
- `SCHEDULER_RELEVANCE_TARGET`: scoring target used by the news pipeline.
- `YOUTUBE_CHANNEL_FOCUS`: required for `PIPELINE_MODE=end_to_end`.

The example defaults production to `SCHEDULER_PROVIDER=openai` and `PIPELINE_MODE=end_to_end`.
`YOUTUBE_SCENE_LIMIT=50` is a hard pre-request cost guard; lower it before the first production run
if desired. `OPENAI_MODEL`, `OPENAI_IMAGE_MODEL`, item counts, interval, image size, and `TZ` can be
changed in `.env`. Never commit `.env` or paste it into logs.

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

## Provision the Ubuntu VPS

Use Ubuntu 24.04 with at least 2 vCPU, 4 GB RAM, and 40 GB of persistent disk. Create a non-root
deployment user and the fixed application directory:

```bash
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy
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
docker version
docker compose version
```

Add a dedicated public key to `/home/deploy/.ssh/authorized_keys`, owned by `deploy`, with directory
mode `700` and file mode `600`. Prefix that key's line with OpenSSH's `restrict` option to disable
PTY allocation and forwarding while still allowing the required non-interactive commands. Confirm
a second key-authenticated SSH session works before disabling password authentication. Restrict the
key at the network layer to GitHub-hosted runner egress ranges only if those changing ranges are
maintained automatically; otherwise use a self-hosted runner or a VPN/bastion for a stable source.
Never expose the Docker socket over TCP.

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

Docker-published ports can bypass some UFW rules. This Compose file intentionally publishes only
Caddy's 80/443 TCP and 443 UDP ports; it never publishes dashboard port 8000. Create an `A` DNS
record for `APP_DOMAIN` pointing at the VPS public IPv4 address. Add `AAAA` only when IPv6 is fully
configured and firewalled. Wait for DNS propagation before the first deployment so Caddy can
obtain a certificate.

## Configure production state and registry access

On the VPS, create `/opt/ai-news-intelligence/.env` from `.env.example`, populate the required
runtime values described above, and run `chmod 600 .env`. The automation updates only the
`APP_IMAGE` line. It never uploads, replaces, displays, or checks in the remaining runtime secrets.

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
and deployment scripts over strictly verified SSH. It does not build on the VPS or transfer `.env`.

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
