#!/bin/bash

# Align appuser UID and GID with the owner of the mounted /app volume
TARGET_UID=$(stat -c '%u' /app 2>/dev/null || stat -f '%u' /app 2>/dev/null || echo "")
TARGET_GID=$(stat -c '%g' /app 2>/dev/null || stat -f '%g' /app 2>/dev/null || echo "")

if [ -n "$TARGET_UID" ] && [ "$TARGET_UID" -ne 0 ] && [ "$TARGET_UID" != "$(id -u appuser 2>/dev/null)" ]; then
    groupmod -g "$TARGET_GID" appuser 2>/dev/null || true
    usermod -u "$TARGET_UID" -g "$TARGET_GID" appuser 2>/dev/null || usermod -u "$TARGET_UID" appuser 2>/dev/null || true
    chown -R appuser:appuser /home/appuser 2>/dev/null || true
fi

# Secrets that live in the project directory. /app is a bind mount of the host
# checkout, so every chmod below writes through to the host's inodes -- these
# names are held out of the blanket `a+rX` sweep and locked to 600 afterwards.
# Mirrors the ignore lists in .gitignore / .dockerignore. .env.example is
# tracked sample config, not a secret, so it is excluded from the match.
AOC_SECRET_NAMES=( '*bot_token*' 'client_secret_*' '.env' '.env.*' )

secret_match=()
for pattern in "${AOC_SECRET_NAMES[@]}"; do
    secret_match+=( -o -name "$pattern" )
done
# Drop the leading -o, then exempt the committed example file
secret_match=( \( "${secret_match[@]:1}" \) ! -name '.env.example' )

# Ensure /app and its files are readable and directories are executable.
# -prune keeps the secrets out of it: a+rX would leave the host copies
# world-readable, which is how a 600 token silently became 644 on restart.
find /app "${secret_match[@]}" -prune -o -exec chmod a+rX {} + 2>/dev/null || true

# If appuser still cannot read /app/main.py, ensure ownership
if ! runuser -u appuser -- test -r /app/main.py 2>/dev/null; then
    chown -R appuser:appuser /app 2>/dev/null || true
fi

# Restrict secrets to owner-only. Runs after the chown above so it has the
# final say on the mode regardless of which branch ran.
find /app "${secret_match[@]}" -type f -exec chmod 600 {} + 2>/dev/null || true

# --- SSH credential provisioning -------------------------------------------
# Host keys are mounted READ-ONLY at /mnt/.ssh and then *copied* into each
# user's home directory. They are deliberately not bind-mounted onto
# ~/.ssh: a bind mount preserves the host UID, so /root/.ssh files stay owned
# by appuser and OpenSSH aborts with "Bad owner or permissions". chown cannot
# fix that because the inode is shared with the host. Copying gives each user
# a private, correctly-owned copy.
SSH_SRC=""
for candidate in /mnt/.ssh /mnt/ssh; do
    if [ -d "$candidate" ] && [ "$(ls -A "$candidate" 2>/dev/null)" ]; then
        SSH_SRC="$candidate"
        break
    fi
done

provision_ssh_dir() {
    dest="$1"
    owner="$2"

    mkdir -p "$dest" || return 0

    if [ -n "$SSH_SRC" ]; then
        # -L dereferences symlinked keys so the copy is self-contained
        cp -RL "$SSH_SRC"/. "$dest"/ 2>/dev/null || true
    fi

    if [ -f "$dest/config" ]; then
        # UseKeychain is macOS-only and makes OpenSSH on Linux fail to parse
        sed -i -E 's/^([[:space:]]*)([Uu]se[Kk]eychain.*)$/\1# \2/' "$dest/config" 2>/dev/null || true
        # Repoint IdentityFile at the in-container copy. Host layouts vary
        # (/Users/x/.ssh on macOS, /volume1/homes/x/.ssh on Synology, ...), so
        # match any absolute path ending in /.ssh/ rather than specific homes.
        sed -i -E "s#^([[:space:]]*[Ii]dentity[Ff]ile[[:space:]]+)[~]?/[^[:space:]]*/\.ssh/#\1$dest/#" "$dest/config" 2>/dev/null || true
        # Any remaining host-style ~/.ssh references resolve per-user already
        sed -i -E "s#(/Users/[^/[:space:]]+|/home/[^/[:space:]]+)/\.ssh#$dest#g" "$dest/config" 2>/dev/null || true
    fi

    # Non-interactive defaults so automated git sync never blocks on a prompt.
    # Also advertise every private key we find: keys with non-default names
    # (e.g. "gitlab") are never tried automatically, so a host without an
    # explicit config entry would fail with "Permission denied (publickey)".
    if ! grep -q "aoc-managed defaults" "$dest/config" 2>/dev/null; then
        {
            echo ""
            echo "# --- aoc-managed defaults (added by entrypoint.sh) ---"
            echo "Host *"
            echo "    StrictHostKeyChecking accept-new"
            echo "    BatchMode yes"
            echo "    ConnectTimeout 15"
            for key in "$dest"/*; do
                [ -f "$key" ] || continue
                case "$key" in
                    *.pub|*/config|*/known_hosts*|*/authorized_keys|*/agent.env) continue ;;
                esac
                if grep -qI "PRIVATE KEY" "$key" 2>/dev/null; then
                    echo "    IdentityFile $key"
                fi
            done
        } >> "$dest/config" 2>/dev/null || true
    fi

    chown -R "$owner" "$dest" 2>/dev/null || true
    chmod 700 "$dest" 2>/dev/null || true
    find "$dest" -type f -exec chmod 600 {} \; 2>/dev/null || true
    chmod 644 "$dest"/*.pub "$dest/known_hosts" 2>/dev/null || true
}

if [ -n "$SSH_SRC" ]; then
    echo "Provisioning SSH credentials from $SSH_SRC for appuser and root..."
else
    echo "Warning: no SSH keys staged at /mnt/.ssh; git over SSH will fail."
fi
provision_ssh_dir /home/appuser/.ssh appuser:appuser
provision_ssh_dir /root/.ssh root:root

# Report which keys were staged. A wrong host mount (e.g. "~" resolving to
# /root under sudo) otherwise only shows up as "Permission denied (publickey)".
for pub in /root/.ssh/*.pub; do
    [ -f "$pub" ] || continue
    echo "  SSH key: $(ssh-keygen -lf "$pub" 2>/dev/null || basename "$pub")"
done

# Ensure system-wide SSH client config allows non-interactive host key acceptance for automated git sync
if [ -d "/etc/ssh" ] && ! grep -qi "StrictHostKeyChecking accept-new" /etc/ssh/ssh_config 2>/dev/null; then
    cat >> /etc/ssh/ssh_config << 'EOF'

Host *
    StrictHostKeyChecking accept-new
    BatchMode yes
    ConnectTimeout 15
EOF
fi

# Configure Git safe.directory for both root and appuser to support mounted volumes
git config --system --add safe.directory '*'
runuser -u appuser -- git config --global --add safe.directory '*' 2>/dev/null || true

# Copy host .gitconfig if mounted, or ensure default user identity for commits
if [ -f "/mnt/.gitconfig" ]; then
    cp /mnt/.gitconfig /home/appuser/.gitconfig
    chown appuser:appuser /home/appuser/.gitconfig
fi

GIT_USER_NAME="${GIT_USER_NAME:-${GIT_AUTHOR_NAME:-AOC Bot}}"
GIT_USER_EMAIL="${GIT_USER_EMAIL:-${GIT_AUTHOR_EMAIL:-aoc@localhost}}"
git config --system user.name "$GIT_USER_NAME" 2>/dev/null || true
git config --system user.email "$GIT_USER_EMAIL" 2>/dev/null || true
if ! runuser -u appuser -- git config --global user.name >/dev/null 2>&1; then
    runuser -u appuser -- git config --global user.name "$GIT_USER_NAME" 2>/dev/null || true
fi
if ! runuser -u appuser -- git config --global user.email >/dev/null 2>&1; then
    runuser -u appuser -- git config --global user.email "$GIT_USER_EMAIL" 2>/dev/null || true
fi

# Configure GitHub CLI git credential helper if token is present
if [ -n "$GITHUB_TOKEN" ] || [ -n "$GH_TOKEN" ]; then
    runuser -u appuser -- gh auth setup-git 2>/dev/null || true
fi

# Ensure gogcli home, PKM, and workspaces exist and have proper ownership
export GOG_HOME="${GOG_HOME:-/app/.gogcli}"
export GOG_KEYRING_BACKEND="${GOG_KEYRING_BACKEND:-file}"
if [ -n "$GOG_KEYRING_PASSWORD" ]; then
    export GOG_KEYRING_PASSWORD
fi
GOG_DIR="$GOG_HOME"
mkdir -p "$GOG_DIR/config" "$GOG_DIR/data" "$GOG_DIR/state" "$GOG_DIR/cache" /home/appuser/pkm /home/appuser/workspaces /home/appuser/.config
chown -R appuser:appuser "$GOG_DIR" /home/appuser/pkm /home/appuser/workspaces /home/appuser/.config 2>/dev/null || true

# npm writes its cache and global prefix as appuser. Recreate them here as well
# as in the image: the UID remap above can leave them owned by the old UID, and
# an unwritable cache makes every `npm`/`npx` setup command fail with EACCES.
NPM_CACHE_DIR="${NPM_CONFIG_CACHE:-/home/appuser/.npm}"
NPM_PREFIX_DIR="${NPM_CONFIG_PREFIX:-/home/appuser/.npm-global}"
mkdir -p "$NPM_CACHE_DIR" "$NPM_PREFIX_DIR" 2>/dev/null || true
chown -R appuser:appuser "$NPM_CACHE_DIR" "$NPM_PREFIX_DIR" 2>/dev/null || true

# Symlink legacy ~/.config/gogcli to project gogcli config directory
ln -sfn "$GOG_DIR/config" /home/appuser/.config/gogcli 2>/dev/null || true

# Symlink and resolve repository paths between /app/pkm and /home/appuser/pkm
if [ -d "/app" ]; then
    # If /app/pkm is an existing git repo or real directory and /home/appuser/pkm has no .git, link /home/appuser/pkm -> /app/pkm
    if [ -d "/app/pkm/.git" ] && [ ! -d "/home/appuser/pkm/.git" ]; then
        rm -rf /home/appuser/pkm 2>/dev/null || true
        ln -sfn /app/pkm /home/appuser/pkm 2>/dev/null || true
    elif [ ! -e "/app/pkm" ] || [ -L "/app/pkm" ]; then
        ln -sfn /home/appuser/pkm /app/pkm 2>/dev/null || true
    fi

    if [ ! -e "/app/workspaces" ] || [ -L "/app/workspaces" ]; then
        ln -sfn /home/appuser/workspaces /app/workspaces 2>/dev/null || true
    fi

    # Ensure scripts in /app/scripts are executable
    chmod +x /app/scripts/*.py 2>/dev/null || true
fi

# The named volume mounted at $AOC_ASSET_ROOT carries the ownership baked into
# the image, which stops matching appuser after the UID remap at the top of this
# script; a volume created before that directory existed is owned by root
# instead. Either way appuser could not refresh the cache, so realign it.
ASSET_ROOT="${AOC_ASSET_ROOT:-/opt/aoc}"
if [ -d "$ASSET_ROOT" ]; then
    chown -R appuser:appuser "$ASSET_ROOT" 2>/dev/null || true
fi

# Restore assets the /app bind mount hid, create ignored runtime directories,
# and build the vector index if the mounted PKM has none. Runs as appuser so
# everything it writes is owned by the account that later reads it. Never
# fatal: a missing index degrades search, it should not block startup.
if [ "${AOC_PROVISION_RUNTIME:-1}" = "1" ] && [ -f /app/scripts/provision_assets.py ]; then
    runuser -u appuser -- python3 /app/scripts/provision_assets.py --stage runtime || \
        echo "Warning: runtime asset provisioning reported errors; continuing startup."
fi

# Start Chromium in the background as appuser for visual AI browser automation if enabled.
# Disabled by default (0) to conserve CPU cores and RAM on NAS hosts.
if [ "${AOC_ENABLE_BROWSER:-0}" = "1" ]; then
    echo "Starting headless Chromium on port 9222..."
    runuser -u appuser -- chromium --headless --no-sandbox --disable-gpu --remote-debugging-port=9222 &
else
    echo "Headless Chromium disabled (set AOC_ENABLE_BROWSER=1 in .env to enable)."
fi

# Execute the command passed to docker run as appuser
exec runuser -u appuser -- "$@"
