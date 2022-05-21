#!/bin/bash -x

# DIR=$(pwd)
# PARENT_DIR="$(dirname "$DIR")"
ROOT_DIR=/home/bitnami

# Load BOOTSTRAP_PUBLIC_IPV4 set in user data
source $ROOT_DIR/env.sh
echo "Bootstrapped public IP is $BOOTSTRAP_PUBLIC_IPV4"
PUBLIC_IPV4=$(curl http://169.254.169.254/latest/meta-data/public-ipv4)
echo "Current public IPv4 is $PUBLIC_IPV4"

export NEO4J_ENDPOINT=$(aws ssm get-parameters \
    --region $REGION \
    --names "/$APP_NAME/$STAGE/$REGION/Neo4jDatabaseEndpoint" \
    | jq -r '.Parameters | map(select(.Version == 1))[0].Value')

echo "Target Elastic IP is $NEO4J_ENDPOINT"

echo "Waiting for Elastic IP Association..."
# Set timeout
TIMEOUT=${1:-60}
counter=0
until [ "$PUBLIC_IPV4" = "$NEO4J_ENDPOINT" ]; do
    PUBLIC_IPV4=$(curl http://169.254.169.254/latest/meta-data/public-ipv4)
    printf '.'
    sleep 1
    counter=$((counter + 1))

    if [ $counter -eq $TIMEOUT ]; then
        echo "Task timed out"
        exit 1
        break
    fi
done
printf "%s\n"
echo "Validating association..."
echo "Instance is associated with Elastic IP at $PUBLIC_IPV4"
exit 0