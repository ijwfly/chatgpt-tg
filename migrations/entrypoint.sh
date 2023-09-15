#!/bin/bash
set -e

docker-entrypoint.sh postgres &

/docker-entrypoint-initdb.d/wait-for-it.sh localhost:5432 --timeout=0

bash /docker-entrypoint-initdb.d/pg_init.sh

wait $!
