# Docker & Portainer Deployment Guide

This guide covers deploying **ComfyUI** with **MSS-Login** using **Docker Compose** or **Portainer**, with full GPU passthrough, encrypted persistence, and mobile app integration (such as [Comfy Portal](https://github.com/ShunL12324/comfy-portal)).

---

## 1. Prerequisites

- **Docker Engine** 24.0+ and **Docker Compose** v2
- **NVIDIA GPU** with latest NVIDIA drivers and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (if GPU acceleration is desired)
- **Portainer CE / Business** (optional, if deploying via GUI)

Verify NVIDIA Container Toolkit:
```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## 2. Quickstart with Docker Compose

The `deploy/` directory includes a production-ready `docker-compose.yml`.

### Step 1: Clone the repository

```bash
git clone https://github.com/Monster-Spawned-Studios/ComfyUI-MSS-Login.git
cd ComfyUI-MSS-Login/deploy
```

### Step 2: Configure Environment Variables

Create a `.env` file in `deploy/`:

```env
# Public domain or LAN IP of your ComfyUI instance (crucial for CSP & WebSockets)
HOST_BASE_URL=http://192.168.1.100:8188

# 32+ character random secret key for JWT and SQLCipher database encryption
SECRET_KEY=generate_a_long_random_string_here_with_openssl_rand_hex_32

# Port to expose on the host
PORT=8188

# Enable embedded headless graph parser & pre-warmed browser pool for mobile apps
CPE_EMBEDDED_CONVERT=true

# Allow custom login background images and videos
EXPERIMENTAL_LOGIN_BACKGROUND=true
```

> [!TIP]
> Generate a strong `SECRET_KEY` using:
> ```bash
> openssl rand -hex 32
> ```

### Step 3: Launch the Stack

```bash
docker compose up -d
```

Check the startup logs:
```bash
docker compose logs -f comfyui
```

Access the ComfyUI MSS-Login interface at `http://<your-host-ip>:8188`. The first user to register will automatically become the system **Owner**.

---

## 3. Deploying via Portainer

Portainer provides an intuitive web interface for managing Docker containers and stacks.

### Step 1: Open Portainer Stacks

1. Log into your Portainer dashboard.
2. Select your environment (e.g. `primary` or `local`).
3. In the left navigation, click **Stacks** -> **Add stack**.

### Step 2: Configure the Stack

1. **Name**: `comfyui-mss-login`
2. **Build method**: Select **Web editor** (or **Repository** to point to this git repo).
3. Paste the contents of [`deploy/portainer-stack.yml`](file:///mnt/ASUS-TUF/Projects/Python/ComfyUI-MSS-Login/deploy/portainer-stack.yml).

### Step 3: Set Environment Variables

In the **Environment variables** section beneath the editor, add:

| Variable | Example Value | Description |
|---|---|---|
| `HOST_BASE_URL` | `https://comfy.yourdomain.com` | Public base URL used for CSP and WebSocket origins |
| `SECRET_KEY` | `e2a4...` | Cryptographic secret for tokens and database encryption |
| `PORT` | `8188` | Host port to publish |
| `CPE_EMBEDDED_CONVERT` | `true` | Enables headless graph parser for mobile workflow conversion |

### Step 4: Deploy

Click **Deploy the stack**. Portainer will pull the image, create the volumes, and allocate the NVIDIA GPU.

---

## 4. Volume Architecture & Persistence

All stateful data is isolated in Docker volumes:

| Volume | Container Mount | Description |
|---|---|---|
| `mss_login_data` | `/data/mss-login` | SQLite/SQLCipher DB, user profiles, API tokens, custom backgrounds, and audit logs |
| `comfyui_models` | `/root/ComfyUI/models` | Checkpoints, LoRAs, VAEs, and diffusion models |
| `comfyui_output` | `/root/ComfyUI/output` | Generated images, videos, and workflow outputs |
| `comfyui_input` | `/root/ComfyUI/input` | Input images for image-to-image or controlnets |
| `comfyui_user` | `/root/ComfyUI/user` | ComfyUI saved workflow graphs |

### Backing up MSS-Login Configuration & Users

To create a backup of all users, roles, permissions, and tokens:
```bash
docker run --rm -v comfyui_mss_data:/data -v $(pwd):/backup ubuntu \
  tar czf /backup/mss-login-data-backup-$(date +%Y%m%d).tar.gz -C /data .
```

---

## 5. Mobile & Tablet App Integration

MSS-Login includes dedicated endpoints and token management to support mobile applications such as [Comfy Portal](https://github.com/ShunL12324/comfy-portal) (iOS / Android) and custom REST / WebSocket clients.

### Step 1: Generate an API Token

1. Open ComfyUI in your browser and log in.
2. Click your avatar in the status bar or the MSS-Login icon in the menu bar.
3. Click **MSS-Login Control Center** -> **Tokens & API Keys**.
4. In the **Create API Token** panel:
   - Provide a label (e.g. `My iPad Portal`).
   - Select an expiration duration (e.g. `30 Days` or `1 Year`).
   - Click **Generate Token**.
5. Copy the generated token string using the **Copy Token** button.

### Step 2: Connect from Mobile / Tablet

In your mobile app or REST client:
- **Server Address**: `http://<your-host-ip>:8188` (or `https://ai.example.com`)
- **HTTP Authorization**: Send header `Authorization: Bearer <your-token>`
- **WebSocket Connection**: Append `?token=<your-token>` to the WebSocket URL:
  ```text
  wss://<your-host-ip>:8188/ws?clientId=<uuid>&token=<your-token>
  ```

### Step 3: Direct API Authentication for Mobile Apps

Mobile applications can authenticate programmatically via JSON:

```http
POST /mss-login/api/login
Content-Type: application/json

{
  "username": "your_username",
  "password": "your_password"
}
```

**Response**:
```json
{
  "message": "Login successful",
  "jwt_token": "eyJhbGciOi...",
  "user": {
    "username": "your_username",
    "roles": ["member"]
  }
}
```

Store `jwt_token` in the mobile app's secure keystore / keychain and pass it via `Authorization: Bearer <token>` in subsequent requests.

---

## 6. Reverse Proxy Configuration

When exposing ComfyUI MSS-Login behind a reverse proxy (e.g. Nginx, Traefik, Caddy), configure WebSocket upgrades and match `HOST_BASE_URL`.

### Nginx Example

```nginx
server {
    listen 443 ssl http2;
    server_name ai.example.com;

    ssl_certificate /etc/letsencrypt/live/ai.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ai.example.com/privkey.pem;

    client_max_body_size 500M;

    location / {
        proxy_pass http://127.0.0.1:8188;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

Ensure `HOST_BASE_URL` in your `.env` or Portainer stack is set to `https://ai.example.com`.
