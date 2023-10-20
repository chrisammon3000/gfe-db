SERVICE := infrastructure

target:
	$(info ${HELP_MESSAGE})
	@exit 0

# stdout colors
# blue: runtime message, no action required
# green: parameter value message, no action required
# yellow: message to user, action required
# red: error message, action required
define blue
	@tput setaf 4
	@echo $1
	@tput sgr0
endef

define green
	@tput setaf 2
	@echo $1
	@tput sgr0
endef

define yellow
	@tput setaf 3
	@echo $1
	@tput sgr0
endef

define red
	@tput setaf 1
	@echo $1
	@tput sgr0
endef

# VPC_ID, USE_PRIVATE_SUBNET, PUBLIC_SUBNET_ID and PRIVATE_SUBNET_ID are validated in the root Makefile
# TODO test using env vars for endpoint IDs, currently blocked by conflicting DNS in VPC. Using static env vars should not change cfn conditions between deployments
deploy: 
	@echo "$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Deploying ${SERVICE} service" 2>&1 | tee -a $${CFN_LOG_PATH}
	$(call blue, "HAS_DEPLOYED_STAGE: ${HAS_DEPLOYED_STAGE}")
ifeq ($(CREATE_VPC),false)
ifndef PRIVATE_SUBNET_ID
	$(eval USE_PRIVATE_SUBNET := false)
endif
ifdef PRIVATE_SUBNET_ID
	$(eval USE_PRIVATE_SUBNET := true)
endif
endif
	$(call blue, "HAS_DEPLOYED_STAGE: ${HAS_DEPLOYED_STAGE}")
ifeq ($(HAS_DEPLOYED_STAGE),null)
	$(info SSM_VPC_ENDPOINT_ID: ${SSM_VPC_ENDPOINT_ID})
	$(info SECRETSMANAGER_VPC_ENDPOINT_ID: ${SECRETSMANAGER_VPC_ENDPOINT_ID})
	$(if $(shell test -z "${SSM_VPC_ENDPOINT_ID}" && echo true),\
		$(call blue, "No Systems Manager VPC endpoint found. Deployment will create a new one in ${PRIVATE_SUBNET_ID}."),\
		$(call blue, "VPC endpoint for Systems Manager found: ${SSM_VPC_ENDPOINT_ID}. Deployment will use this one.")\
	)
	$(if $(shell test -z "${SECRETSMANAGER_VPC_ENDPOINT_ID}" && echo true),\
		$(call blue, "No Secrets Manager VPC endpoint found. Deployment will create a new one in ${PRIVATE_SUBNET_ID}."),\
		$(call blue, "VPC endpoint for Secrets Manager found: ${SECRETSMANAGER_VPC_ENDPOINT_ID}. Deployment will use this one.")\
	)
	$(MAKE) service.deploy USE_PRIVATE_SUBNET=${USE_PRIVATE_SUBNET} SSM_VPC_ENDPOINT_ID=${SSM_VPC_ENDPOINT_ID} SECRETSMANAGER_VPC_ENDPOINT_ID=${SECRETSMANAGER_VPC_ENDPOINT_ID}
ifeq ($(UPDATE_VPC_ENDPOINT_SECURITY_GROUPS),true)
	$(if $(shell test -z "${SSM_VPC_ENDPOINT_ID}" && echo true),,$(MAKE) service.deploy.update-vpc-endpoint service=ssm endpoint_id=${SSM_VPC_ENDPOINT_ID})
	$(if $(shell test -z "${SECRETSMANAGER_VPC_ENDPOINT_ID}" && echo true),,$(MAKE) service.deploy.update-vpc-endpoint service=secretsmanager endpoint_id=${SECRETSMANAGER_VPC_ENDPOINT_ID})
else ifeq ($(UPDATE_VPC_ENDPOINT_SECURITY_GROUPS),false)
	$(call yellow, Skipping update of VPC endpoint security groups. The security group for the Neo4j server must be added manually.)
endif
else 
	$(call blue, "HAS_DEPLOYED_STAGE: ${HAS_DEPLOYED_STAGE}; Updating deployed stack")
	$(MAKE) service.deploy USE_PRIVATE_SUBNET=${USE_PRIVATE_SUBNET} SSM_VPC_ENDPOINT_ID=${SSM_VPC_ENDPOINT_ID} SECRETSMANAGER_VPC_ENDPOINT_ID=${SECRETSMANAGER_VPC_ENDPOINT_ID}
endif
	# $(MAKE) service.monitoring.create-subscriptions

service.deploy.update-dns:
	@echo "$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Updating DNS records" 2>&1 | tee -a $${CFN_LOG_PATH}
	@config_path=${INFRA_DIR}/change-batch.json && \
	elastic_ip=$$(aws ssm get-parameters \
		--names "/$${APP_NAME}/$${STAGE}/$${AWS_REGION}/Neo4jDatabaseEndpoint" \
		--with-decryption \
		--query "Parameters[0].Value" \
		--output text) && \
	sed -e "s/<domain_name>/${SUBDOMAIN}.${HOST_DOMAIN}./g" -e "s/<elastic_ip>/$$elastic_ip/g" $$config_path > $$config_path.tmp && \
	echo "Updating DNS records with:" && \
	cat $$config_path.tmp | jq -r && \
	res=$$(aws route53 change-resource-record-sets --hosted-zone-id $${HOSTED_ZONE_ID} --change-batch file://$$config_path.tmp) && \
	echo && \
	echo "Response:" && \
	echo $$res | jq -r

# service.deploy.update-vpc-endpoints:
# ifeq ($(CREATE_SSM_ENDPOINT),false)
# 	$(MAKE) service.deploy.update-vpc-endpoint service=ssm endpoint_id=${SSM_VPC_ENDPOINT_ID}
# endif
# ifeq ($(CREATE_SECRETSMANAGER_ENDPOINT),false)
# 	$(MAKE) service.deploy.update-vpc-endpoint service=secretsmanager endpoint_id=${SECRETSMANAGER_VPC_ENDPOINT_ID}
# endif

# the vpc endpoint must match PRIVATE_SUBNET_ID env var
service.deploy.update-vpc-endpoint: #service=<string[ssm,secretsmanager]>
	@echo "\033[0;34m$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Adding security group to $${service} VPC endpoint\033[0m" 2>&1 | tee -a $${CFN_LOG_PATH}
	@echo "$$endpoint_id"
	@[ "$$endpoint_id" = "" ] && $$(echo "\033[0;31mFailed to get $${service} VPC endpoint ID\033[0m" && exit 1) || \
	echo "\033[0;34mUpdating $${service} VPC endpoint ID: $$endpoint_id\033[0m"
	@security_group_id=$$(aws ssm get-parameters \
		--names "/$${APP_NAME}/$${STAGE}/$${AWS_REGION}/Neo4jDatabaseSecurityGroupId" \
		--with-decryption \
		--query "Parameters[0].Value" \
		--output text) && \
	[ "$$security_group_id" = "None" ] && $$(echo "\033[0;31mFailed to get security group ID\033[0m" && exit 1) || \
	echo "Found security group ID: $$security_group_id" && \
	response=$$(aws ec2 modify-vpc-endpoint \
		--vpc-endpoint-id "$$endpoint_id" \
		--add-security-group-ids "$$security_group_id" | jq -r '.Return') && \
	[ "$$response" = "true" ] && echo "\033[0;34mSuccessfully added security group to $${service} VPC endpoint\033[0m" || \
	(echo "\033[0;31mFailed to add security group to $${service} VPC endpoint\033[0m" && exit 1)

# TODO test AWSCLI output and validate the stack was created successfully
service.deploy: #=> USE_PRIVATE_SUBNET=<bool> SSM_ENDPOINT_ID=<string> SECRETSMANAGER_ENDPOINT_ID=<string>
	@echo "$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Deploying CloudFormation" 2>&1 | tee -a $${CFN_LOG_PATH}
	@echo "USE_PRIVATE_SUBNET: $${USE_PRIVATE_SUBNET}"
	@echo "SSM_VPC_ENDPOINT_ID: $${SSM_VPC_ENDPOINT_ID}"
	@echo "SECRETSMANAGER_VPC_ENDPOINT_ID: $${SECRETSMANAGER_VPC_ENDPOINT_ID}"
	@aws cloudformation deploy \
		--region $${AWS_REGION} \
		--template-file template.yaml \
		--stack-name "$${STAGE}-$${APP_NAME}-${SERVICE}" \
		--tags stage="$${STAGE}" app="$${APP_NAME}" service="${SERVICE}" branch="$$(git branch --show-current)" commit=$$(git rev-parse HEAD) \
		--parameter-overrides \
			Stage="$${STAGE}" \
			AppName="$${APP_NAME}" \
			createVpc="$${CREATE_VPC}" \
			usePrivateSubnet="$${USE_PRIVATE_SUBNET}" \
			SsmVpcEndpointId="$${SSM_VPC_ENDPOINT_ID}" \
			SecretsManagerVpcEndpointId="$${SECRETSMANAGER_VPC_ENDPOINT_ID}" \
			VpcId="$${VPC_ID}" \
			PublicSubnetId="$${PUBLIC_SUBNET_ID}" \
			PrivateSubnetId="$${PRIVATE_SUBNET_ID}" \
			AdminEmail="$${ADMIN_EMAIL}" \
			DataBucketName="$$DATA_BUCKET_NAME" \
			GitHubPersonalAccessToken="$$GITHUB_PERSONAL_ACCESS_TOKEN" \
			2>&1 | tee -a $${CFN_LOG_PATH} || true
	# $(MAKE) service.deploy.update-vpc-endpoints

service.monitoring.create-subscriptions:
	@echo "$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Creating SNS topic subscriptions" 2>&1 | tee -a $${CFN_LOG_PATH}
	@topic_arn=$$(aws ssm get-parameters \
		--names "/$${APP_NAME}/$${STAGE}/$${AWS_REGION}/DataPipelineErrorsTopicArn" \
		--with-decryption \
		--query "Parameters[0].Value" \
		--output text) && \
	for EMAIL in $$(echo $${SUBSCRIBE_EMAILS} | sed 's/,/ /g'); do \
		aws sns subscribe \
			--topic-arn "$$topic_arn" \
			--protocol email \
			--notification-endpoint "$$EMAIL" 2>&1 | tee -a $${CFN_LOG_PATH} || true; \
	done

service.monitoring.subscribe-email: #email=<string>
	@echo "$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Creating SNS topic subscription" 2>&1 | tee -a $${CFN_LOG_PATH}
	@topic_arn=$$(aws ssm get-parameters \
		--names "/$${APP_NAME}/$${STAGE}/$${AWS_REGION}/DataPipelineErrorsTopicArn" \
		--with-decryption \
		--query "Parameters[0].Value" \
		--output text) && \
	aws sns subscribe \
		--topic-arn "$$topic_arn" \
		--protocol email \
		--notification-endpoint "$$email" 2>&1 | tee -a $${CFN_LOG_PATH} || true;

delete: ##=> Delete resources
	@echo "$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Deleting ${SERVICE} service" 2>&1 | tee -a $${CFN_LOG_PATH}
ifeq ($(data),true)
	@echo "$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Deleting data in $$DATA_BUCKET_NAME" 2>&1 | tee -a $${CFN_LOG_PATH}
	@aws s3 rm --recursive s3://$${DATA_BUCKET_NAME}/
endif
	$(MAKE) service.delete

service.delete:
	@echo "$$(gdate -u +'%Y-%m-%d %H:%M:%S.%3N') - Deleting CloudFormation" 2>&1 | tee -a $${CFN_LOG_PATH}
	@aws cloudformation delete-stack \
		--stack-name "$${STAGE}-$${APP_NAME}-${SERVICE}" 2>&1 | tee -a $${CFN_LOG_PATH} || true && \
	aws cloudformation wait stack-delete-complete \
		--stack-name "$${STAGE}-$${APP_NAME}-${SERVICE}" 2>&1 | tee -a $${CFN_LOG_PATH} || true

#############
#  Helpers  #
#############

define HELP_MESSAGE

	Environment variables:

	SERVICE: "${SERVICE}"
		Description: Name of the service being deployed

	Common usage:

	...::: Deploy all CloudFormation based services :::...
	$ make deploy

	...::: Delete all CloudFormation based services :::...
	$ make delete

endef