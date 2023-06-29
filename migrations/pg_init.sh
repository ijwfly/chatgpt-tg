#!/usr/bin/env bash
source /usr/local/bin/docker-entrypoint.sh

docker_process_init_files /docker-entrypoint-initdb.d/migrations-1/*
