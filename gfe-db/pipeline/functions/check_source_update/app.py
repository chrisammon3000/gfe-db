"""
Checks a GitHub repository against app state for new commits and triggers data ingestion. This function processes
only the releases that it finds. To process specific releases, use a different method.

The execution state table is used to track the state of the application. It uses a composite key of
- commit__sha (hash or primary key)
- execution__version (range or sort key)

There will usually be multiple commits for each release, so the script will determine the most recent commit for each and process only that.

Note: this function is only responsible for checking and processing the most recent commits. It is not responsible for 
syncing state. If old commits are deleted on the Execution state table while the most recent commits remain, 
this function will not reprocess the deleted commits.
"""
import os
if __name__ != "app":
    import sys
    # for dev, local path to gfe-db modules
    # ./gfe-db/pipeline/lambda_layers/gfe_db_models (use absolute path)
    sys.path.append(os.environ["GFEDBMODELS_PATH"])
import logging
from decimal import Decimal
from datetime import timedelta
import json
from gfedbmodels.constants import session, pipeline
from gfedbmodels.types import (
    str_to_datetime,
    str_from_datetime,
    InputParameters,
    ExecutionStateItem,
    RepositoryConfig,
    Commit,
    ExecutionDetailsConfig,
    ExecutionPayloadItem,
)
from gfedbmodels.utils import (
    get_utc_now,
    restore_nested_json,
    list_commits,
    flatten_json,
    filter_null_fields,
)
from gfedbmodels.ingest import (
    read_source_config,
    get_release_version_for_commit
)
from constants import (
    PIPELINE_SOURCE_CONFIG_S3_PATH,
    GITHUB_REPOSITORY_OWNER,
    GITHUB_REPOSITORY_NAME,
    execution_state_table_name,
    data_bucket_name,
    gfedb_processing_queue_url,
    execution_state_table_fields,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

logger.info(
    f"Fetching source config from {data_bucket_name}/{PIPELINE_SOURCE_CONFIG_S3_PATH}"
)

s3 = session.client("s3")
dynamodb = session.resource("dynamodb")
queue = session.resource("sqs")

GITHUB_PERSONAL_ACCESS_TOKEN = pipeline.secrets.GitHubPersonalAccessToken

# Get data source configuration
source_repo_config = read_source_config(
    s3_client=s3, 
    bucket=data_bucket_name, 
    key=PIPELINE_SOURCE_CONFIG_S3_PATH
).repositories[f"{GITHUB_REPOSITORY_OWNER}/{GITHUB_REPOSITORY_NAME}"]

gfedb_processing_queue = queue.Queue(gfedb_processing_queue_url)

# TODO validate commits against tracked source files requiring ingestion
def lambda_handler(event, context):
    utc_now = get_utc_now()
    logger.info(json.dumps(event))

    try:
        ### Get the app state and compare with the repository ###

        # 1) Get all items from state table
        logger.info(f"Fetching execution state from {execution_state_table_name}")
        table = dynamodb.Table(execution_state_table_name)
        execution_state = get_execution_state(table)

        if not execution_state:
            message = "No execution items found. Please populate the state table."
            logger.error(message)
            raise Exception(message)

        # Handle manually triggered pipeline execution
        if "releases" in event:

            logger.info(f"Processing releases from user: {event['releases']}")

            # extract the most recent record for the release value passed in the event
            releases = event["releases"].split(",")
            commits_with_releases = []
            for release in releases:
                commits_with_releases.extend(list(filter(
                    lambda record: record.execution.version == int(release),
                    execution_state
                )))

            # Set input parameters for manual pipeline execution from event
            input_parameters = InputParameters(**event)

        # Handle automated pipeline execution
        else:

            # Use default parameters for automated pipeline execution
            input_parameters = source_repo_config.default_input_parameters
            # 2) Get the most recent commits from github since the most recent commit date retrieved from DynamoDB
            commits = get_most_recent_commits(execution_state)

            # Return if no commits are found, otherwise process the new commits
            if not commits:
                message = "No new commits found"
                logger.info(message)
                return {
                    "statusCode": 200,
                    "body": json.dumps({"message": message}),
                }
            
            logger.info(
                f"Found {len(commits)} commit(s) not yet processed\n{json.dumps([commit['sha'] for commit in commits], indent=2)}"
            )

            # Get the release version for each new commit and create a new state record
            logger.info(f"Getting release versions")
            commits_with_releases = []
            for commit in commits:
                sha = commit["sha"]

                # Loop through available file assets containing release version metadata
                for asset_config in source_repo_config.target_metadata_config.items:
                    try:

                        # Get the release version for the commit by examining file asset contents
                        release_version = get_release_version_for_commit(
                            commit=commit, 
                            owner=GITHUB_REPOSITORY_OWNER,
                            repo=GITHUB_REPOSITORY_NAME,
                            token=GITHUB_PERSONAL_ACCESS_TOKEN,
                            **asset_config.model_dump()
                        )
                        logger.info(
                            f"Found release version {release_version} for commit {sha}"
                        )

                        # Build the execution object to be stored in the state table (`execution__*` fields)
                        execution_detail = ExecutionDetailsConfig(
                            **{"version": release_version, "status": "NOT_PROCESSED"}
                        )

                        # Build the repository object to be stored in the state table (`repository__*` fields)
                        repository_config = RepositoryConfig(
                            **{
                                "owner": GITHUB_REPOSITORY_OWNER,
                                "name": GITHUB_REPOSITORY_NAME,
                                "url": f"https://github.com/{GITHUB_REPOSITORY_OWNER}/{GITHUB_REPOSITORY_NAME}",
                                # TODO remove default params from state table, they are retrieved from source config file in S3
                                # "default_input_parameters": source_repo_config.default_input_parameters,
                            }
                        )

                        # Assemble the execution state item for the new commit
                        execution_state_item = ExecutionStateItem(
                            created_utc=utc_now,
                            execution=execution_detail,
                            repository=repository_config,
                            commit=Commit.from_response_json(commit),
                        )
                        commits_with_releases.append(execution_state_item)
                        # break the loop if successful
                        break
                    except Exception as e:
                        logger.info(f"Error getting release version for commit {sha}: {e}")

        ### Process each release using the most recent commit for that version ###
        logger.info("Updating execution state")

        # 1a) Mark the most recent commit for each release as PENDING
        # input_parameters = source_repo_config.default_input_parameters
        recent_commits_for_release = select_most_recent_commit_for_release(commits_with_releases)
        pending_commits = [
            update_execution_state_item(
                execution_state_item=commit,
                status="PENDING",
                timestamp=utc_now,
                input_parameters=input_parameters,
            )
            for item in recent_commits_for_release
            for commit in item.values()
        ]

        # 1b) Mark the older commits for each release as SKIPPED
        skipped_commits = [
            update_execution_state_item(
            execution_state_item=commit, 
            status="SKIPPED",
            timestamp=utc_now)
            for commit in commits_with_releases
            if commit not in pending_commits
        ]

        # 1c) Combine the pending and skipped commits
        new_execution_state = pending_commits + skipped_commits
        new_execution_state = sorted(
            new_execution_state, key=lambda x: x.commit.date_utc, reverse=False
        )

        # 2) Preprocess the records for the state table (DynamoDB payload)
        items = [
            filter_null_fields(
                flatten_json(
                    data=item.model_dump(),
                    sep="__",
                    select_fields=[
                        item.replace(".", "__") for item in execution_state_table_fields
                    ],
                )
            )
            for item in new_execution_state
        ]

        logger.info(f'Adding items to state table: {json.dumps(items, indent=2)}')

        # 3) Load new commit records to the state table
        if len(items) > 0:
            with table.batch_writer() as batch:
                logger.info(
                    f"Loading {len(items)} items to {execution_state_table_name}"
                )
                for item in items:
                    batch.put_item(Item=item)
            logger.info(f"{len(items)} items loaded to {execution_state_table_name}")
        else:
            raise Exception("Commits were found but the DynamoDB payload is empty")

        # 4) Send pending commits to the state machine for build and load
        execution_payload = [
            ExecutionPayloadItem.from_execution_state_item(item).model_dump()
            for item in pending_commits
        ]
        execution_payload = sorted(
            execution_payload, key=lambda x: x["version"], reverse=False
        )
        for item in execution_payload:
            gfedb_processing_queue.send_message(MessageBody=json.dumps(item))

        message = f"Queued {len(execution_payload)} release(s) for processing\n{execution_payload}"
        logger.info(message)
        return {
            "statusCode": 200,
            "body": json.dumps({"message": message}),
        }
    except Exception as e:
        import traceback

        message = f"Error processing releases: {e}\n{traceback.format_exc()}\n{json.dumps(event)}"
        logger.error(message)
        raise Exception(message)


# @cache_pickle
def get_execution_state(table, sort_column="commit__date_utc", reverse_sort=True):
    # Retrieve execution state from table
    items = table.scan()["Items"]

    if not items:
        return []

    items = [
        {k: int(v) if isinstance(v, Decimal) else v for k, v in item.items()}
        for item in items
    ]
    items = sorted(items, key=lambda x: x[sort_column], reverse=reverse_sort)

    # TODO Deserialize and repack the items
    return [
        ExecutionStateItem(**restore_nested_json(item, split_on="__")) for item in items
    ]


# @cache_json
# TODO return Commit class to make sure data is correct
def get_most_recent_commits(execution_state):
    # 1) Get the most recent commit date from DynamoDB using max(), add one second to it so the same commit is not returned
    last_commit_date = max(
        [str_to_datetime(item.commit.date_utc) for item in execution_state]
    )

    # add minor offset to avoid duplicate commits
    since = str_from_datetime(last_commit_date + timedelta(seconds=1))

    # 2) Get the most recent commits from GitHub using since=<date> parameter
    return list_commits(GITHUB_REPOSITORY_OWNER, GITHUB_REPOSITORY_NAME, since=since, token=GITHUB_PERSONAL_ACCESS_TOKEN)


def select_most_recent_commit_for_release(commits: list[ExecutionStateItem]):
    # group by release version and get most recent by commit date (max date_utc)
    unique_new_releases = list(set([item.execution.version for item in commits]))
    return [
        {
            version: max(
                [item for item in commits if item.execution.version == version],
                key=lambda x: x.commit.date_utc,
            )
        }
        for version in unique_new_releases
    ]


def update_execution_state_item(
    execution_state_item: ExecutionStateItem,
    status: str,
    timestamp: str,
    input_parameters: dict = None,
):
    execution_state_item.execution.status = status

    if input_parameters is not None and status == "PENDING":
        execution_state_item.execution.input_parameters = input_parameters
        # TODO Update format to s3://<data_bucket_name>/data/csv/<version>' for csv and s3://<data_bucket_name>/data/dat/<version>' for hla.dat for Glue Catalog
        execution_state_item.execution.s3_path = (
            f"s3://{data_bucket_name}/data/{execution_state_item.execution.version}"
        )
        execution_state_item.execution.date_utc = timestamp

    execution_state_item.updated_utc = timestamp
    return execution_state_item


if __name__ == "__main__":
    from pathlib import Path

    event = json.loads((Path(__file__).parent / "user-event.json").read_text())

    lambda_handler(event, None)