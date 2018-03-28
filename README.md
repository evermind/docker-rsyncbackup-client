# Dockerized client for RSYNC based backups

## USAGE

```
docker run -it --rm \
   --cap-add SYS_ADMIN \
   -v /etc/localtime:/etc/localtime:ro \
   -v /data/backup-conf:/data/conf \
   -e BACKUP_SERVER=backup.mycompany.com \
   -e BACKUP_SERVER_PORT=2201 \
   -e BACKUP_SERVER_PUBLIC_KEY=ssh-rsa:AAA[...] \
   -e BACKUP_VOLUMES=ROOT \
   -e VOL_ROOT_EXCLUDE=tmp,var/cache,var/lib/docker \
   -e VOL_DATA_TIMEOUT=2h \
   -v /:/data/volumes/ROOT:ro \
   run
```

The following environment variables can be used:

* BACKUP_SERVER (required) - hostname of a server where the server-docker-image runs on
* BACKUP_SERVER_PORT (optional, default: 22) - port of the server
* BACKUP_SERVER_PUBLIC_KEY (optional) - the rsa public key of the server. If ommitted, the trust relationship must be set manually (i.e. create known_hosts file in /data/conf volume)
* BACKUP_VOLUMES (required) - a comma-separated list of volume identifiers
* VOL_{VOLUME_IDENTIFIER}_EXCLUDE (optional) - a comma-separated list of files or folder to exclude from backup
* VOL_{VOLUME_IDENTIFIER}_TIMEOUT (optional, default: 24h) - Timeout after which the backup will be auto-canceled (a number followed by h|m|s)
* VOL_{VOLUME_IDENTIFIER}_PATH (optional, default: /data/volumes/{VOLUME_IDENTIFIER}) - Location of the volume to backup

The following (volumes) are required or recommended:
* /etc/localtime:/etc/localtime (recommended) - ensures that scheduling and logs have the same time zone as the host
* /data/conf (recommened) - The location where thinks like the client's ssh key (id_rsa,id_rsa.pub) and the known_hosts file is stored to. A new key will be created if it is missing
* /data/volumes/{VOLUME_IDENTIFIER} - The actual data to backup (per volume)
* Data volume and /etc/localtime should be mounted read-only

The following docker options are required:
* --cap-add SYS_ADMIN (required) - allows the backup to bind-mount the volume. This ensures that sub-mounts are excluded from backups.

The following commands can be executed:

* run [VOLUME1 VOLUME2 ...] - Executes the backup - optionally one or more volumes can be specified
* schedule HH:MM - schedules the backup on every day at the given time

