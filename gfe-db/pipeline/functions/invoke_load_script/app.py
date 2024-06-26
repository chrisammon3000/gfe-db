import os
import logging
import json
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

neo4j_load_query_document_name_param = os.environ["NEO4J_LOAD_QUERY_DOCUMENT_NAME_SSM_PARAM"]
neo4j_database_instance_id_param = os.environ["NEO4J_DATABASE_INSTANCE_ID_SSM_PARAM"]
load_neo4j_activity = os.environ["LOAD_NEO4J_ACTIVITY"]
app_name = os.environ["APP_NAME"]

# Get SSM Document Neo4jLoadQuery
ssm = boto3.client('ssm', region_name=os.environ["AWS_REGION"])
neo4j_load_query_document_name = ssm.get_parameter(Name=neo4j_load_query_document_name_param)["Parameter"]["Value"]
response = ssm.get_document(Name=neo4j_load_query_document_name)
neo4j_load_query_document_content = json.loads(response["Content"])

# Get Instance ID
neo4j_database_instance_id = ssm.get_parameter(Name=neo4j_database_instance_id_param)["Parameter"]["Value"]

# Extract document parameters
neo4j_load_query_document_parameters = neo4j_load_query_document_content["parameters"]
command_line_default = neo4j_load_query_document_parameters["commandLine"]["default"]
source_info_default = neo4j_load_query_document_parameters["sourceInfo"]["default"]

def lambda_handler(event, context):
    """Invoke SSM Run Command for server side loading on Neo4j

    Example using AWS CLI:
    .. code-block:: python
        aws ssm send-command \
        --document-name "dev-gfe-db-database-Neo4jLoadQueryDocument-UgYcOg48yiQB" \
        --document-version "1" \
        --targets '[{"Key":"InstanceIds","Values":["i-0f8ec07e314226283"]}]' \
        --parameters '{"executionTimeout":["3600"],"sourceInfo":["{\"path\":\"https://<data bucket name>.s3.amazonaws.com/config/scripts/load_db.sh\"}"],"sourceType":["S3"],"workingDirectory":["/home/ec2-user"],"commandLine":["bash load_db.sh"]}' \
        --timeout-seconds 600 \
        --max-concurrency "50" \
        --max-errors "0" \
        --cloud-watch-output-config '{"CloudWatchOutputEnabled":true}' \
        --region us-east-1
    
    """

    logger.info(json.dumps(event))

    # Update params for this execution
    params = {
        "params": {
            "app_name": app_name,
            "activity_arn": load_neo4j_activity,
        }
    }

    # Include params JSON as command line argument
    cmd = f"{command_line_default} \'{json.dumps(params)}\'"

    try:
        response = ssm.send_command(
            InstanceIds=[
                neo4j_database_instance_id,
            ],
            DocumentName=neo4j_load_query_document_name,
            Parameters={
                "commandLine":[cmd],
                "sourceInfo":[json.dumps(source_info_default)]
            },
            MaxConcurrency='1',
            CloudWatchOutputConfig={
                'CloudWatchOutputEnabled': True
            })

        if response['ResponseMetadata']['HTTPStatusCode'] != 200:
            logger.error(json.dumps(response, cls=DatetimeEncoder))
            message = f"Failed to send command `{cmd}` to instance {neo4j_database_instance_id}"
            raise Exception("Failed to send command")
        else:
            message = f"Command `{cmd}` invoked on instance {neo4j_database_instance_id}"
            logger.info(message)
    
    except Exception as err:
        logger.error(err)
        raise err

    return {
        "message": message
    }


# Needed to serialize datetime objects in JSON responses
class DatetimeEncoder(json.JSONEncoder):
    """
    Helps convert datetime objects to pure strings in AWS service API responses. Does not
    convert timezone information.

    Extend `json.JSONEncoder`. 
    """

    def default(self, obj):
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


if __name__ == "__main__":
    from pathlib import Path

    event_path = Path(__file__).parent / "error-event.json"

    with open(event_path, "r") as file:
        event = json.load(file)

    lambda_handler(event,"")
