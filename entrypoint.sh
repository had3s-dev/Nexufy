#!/bin/sh

echo "Setting umask to ${UMASK}"
umask ${UMASK}
echo "Creating download directory (${DOWNLOAD_DIR})"
mkdir -p "${DOWNLOAD_DIR}" /.spotdl

if [ `id -u` -eq 0 ] && [ `id -g` -eq 0 ]; then
    if [ "${UID}" -eq 0 ]; then
        echo "Warning: it is not recommended to run as root user, please check your setting of the UID environment variable"
    fi
    echo "Changing ownership of download and state directories to ${UID}:${GID}"
    chown -R "${UID}":"${GID}" /downtify /.spotdl "${DOWNLOAD_DIR}"
    echo "Running Downtify as user ${UID}:${GID}"
    
    # Create user and group if they don't exist
    groupadd -g "${GID}" appgroup 2>/dev/null || true
    useradd -u "${UID}" -g "${GID}" -s /bin/sh appuser 2>/dev/null || true
    
    # Use gosu instead of su-exec
    exec gosu "${UID}":"${GID}" uvicorn main:app --host 0.0.0.0 --port $DOWNTIFY_PORT
else
    echo "User set by docker; running Downtify as `id -u`:`id -g`"
    uvicorn main:app --host 0.0.0.0 --port $DOWNTIFY_PORT
fi