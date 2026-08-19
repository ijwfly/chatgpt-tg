#!/usr/bin/env bash
set -euo pipefail

# Render per-user resource limits (applied to sudo -u sessions via pam_limits).
# "as" (address space) is in KB.
USER_MAX_PROCS="${USER_MAX_PROCS:-256}"
USER_MAX_MEM_MB="${USER_MAX_MEM_MB:-4096}"
cat > /etc/security/limits.d/sandbox.conf <<EOF
@mcpusers soft nproc  $USER_MAX_PROCS
@mcpusers hard nproc  $USER_MAX_PROCS
@mcpusers soft as     $((USER_MAX_MEM_MB * 1024))
@mcpusers hard as     $((USER_MAX_MEM_MB * 1024))
@mcpusers soft nofile 4096
@mcpusers hard nofile 4096
EOF
echo "[entrypoint] User limits: nproc=$USER_MAX_PROCS mem=${USER_MAX_MEM_MB}MB"

# Sync built-in skills into the shared read-only catalog. The volume survives
# container rebuilds, so bundled skills are overwritten (deleted files included)
# while skills an admin dropped into the volume by hand are left alone.
PUBLIC_SKILLS=/workspace/public_skills
mkdir -p "$PUBLIC_SKILLS"
if [ -d /opt/skills ]; then
  for skill in /opt/skills/*/; do
    [ -d "$skill" ] || continue
    name=$(basename "$skill")
    rm -rf "${PUBLIC_SKILLS:?}/$name"
    cp -a "$skill" "$PUBLIC_SKILLS/$name"
    echo "[entrypoint] Built-in skill synced: $name"
  done
fi
chown -R root:root "$PUBLIC_SKILLS"
chmod -R a+rX "$PUBLIC_SKILLS"

echo "[entrypoint] Starting sandbox server..."
exec python3 /opt/server/main.py
