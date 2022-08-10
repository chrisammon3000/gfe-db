#!/bin/bash -x

# Part of user data, to be run on the database instance on initialization, or later for renewal

echo "Provisioning SSL certificate..."
export NEO4J_HOME=/opt/bitnami/neo4j

# Passed from command line
HOST_DOMAIN=$1
ADMIN_EMAIL=$2

if [[ -z $HOST_DOMAIN ]]; then
    echo "$(date -u +'%Y-%m-%d %H:%M:%S.%3N') - No host domain found"
    exit 0
else
    echo "$(date -u +'%Y-%m-%d %H:%M:%S.%3N') - Found host domain $HOST_DOMAIN"
fi

if [[ -z $ADMIN_EMAIL ]]; then
    echo "$(date -u +'%Y-%m-%d %H:%M:%S.%3N') - No email found"
    exit 1
fi

certbot certonly -n \
  -d $HOST_DOMAIN \
  --standalone \
  -m $ADMIN_EMAIL \
  --agree-tos \
  --redirect

chgrp -R neo4j /etc/letsencrypt/*
chmod -R g+rx /etc/letsencrypt/*
mkdir -p $NEO4J_HOME/certificates/{bolt,cluster,https}/trusted

for certsource in bolt cluster https; do
  ln -sf "/etc/letsencrypt/live/$HOST_DOMAIN/fullchain.pem" "$NEO4J_HOME/certificates/$certsource/neo4j.cert"
  ln -sf "/etc/letsencrypt/live/$HOST_DOMAIN/privkey.pem" "$NEO4J_HOME/certificates/$certsource/neo4j.key"
  ln -sf "/etc/letsencrypt/live/$HOST_DOMAIN/fullchain.pem" "$NEO4J_HOME/certificates/$certsource/trusted/neo4j.cert"
done

chgrp -R neo4j $NEO4J_HOME/certificates/*
chmod -R g+rx $NEO4J_HOME/certificates/*

exit 0
