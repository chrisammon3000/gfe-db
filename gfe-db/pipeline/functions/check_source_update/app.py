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
    sys.path.append(os.environ["GFEDBMODELS_PATH"])
import logging
from decimal import Decimal
from datetime import datetime, timedelta
import json
from pygethub import list_branches, GitHubPaginator
from gfedbmodels.constants import session, pipeline
from gfedbmodels.types import (
    version_is_valid,
    str_to_datetime,
    str_from_datetime,
    InputParameters,
    ExecutionStatus,
    ExecutionStateItem,
    RepositoryConfig,
    Commit,
    ExecutionDetailsConfig,
    ExecutionPayloadItem,
    ExecutionState
)
from gfedbmodels.utils import (
    get_utc_now,
    restore_nested_json,
    list_commits,
    flatten_json,
    filter_null_fields,
    select_keys,
    get_commit
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

STAGE = os.environ["STAGE"]
APP_NAME = os.environ["APP_NAME"]
GITHUB_PERSONAL_ACCESS_TOKEN = pipeline.secrets.GitHubPersonalAccessToken

s3 = session.client("s3")
dynamodb = session.resource("dynamodb")
queue = session.resource("sqs")

gfedb_processing_queue = queue.Queue(gfedb_processing_queue_url)

logger.info(
    f"Fetching source config from {data_bucket_name}/{PIPELINE_SOURCE_CONFIG_S3_PATH}"
)

# Get data source configuration
source_config = read_source_config(
    s3_client=s3, 
    bucket=data_bucket_name, 
    key=PIPELINE_SOURCE_CONFIG_S3_PATH
)

source_repo_config = source_config.repositories[f"{GITHUB_REPOSITORY_OWNER}/{GITHUB_REPOSITORY_NAME}"]
default_input_parameters = source_repo_config.default_input_parameters


# TODO validate commits against tracked source files requiring ingestion
def lambda_handler(event, context):

    utc_now = get_utc_now()
    invocation_id = context.aws_request_id

    logger.info(f"Invocation Id: {invocation_id}")
    logger.info(json.dumps(event))

    is_user_event = True if "releases" in event else False

    try:
        ### Sync App State with Repo State ###

        logger.info(f"Fetching execution state from {execution_state_table_name}")
        table = dynamodb.Table(execution_state_table_name)

        # Get all items from app state table
        execution_state = get_execution_state(table)
        execution_state_items = execution_state.items

        # 2) Get the repository state from the GitHub API
        paginator = GitHubPaginator(GITHUB_PERSONAL_ACCESS_TOKEN)
        branch_pages = paginator.get_paginator(
            list_branches,
            owner=GITHUB_REPOSITORY_OWNER,
            repo=GITHUB_REPOSITORY_NAME,
            user_agent="nmdp-bioinformatics-gfe-db-update-pipeline/1.0",
        )
        all_branches = list(branch_pages)

        repo_state = build_execution_state(all_branches, utc_now)
        repo_state = repo_state.items

        # 3) Compare the app state with the repo state to find new commits

        # Extract commit sha, release version  into tuples from both the app and repo states for set operations
        app_state_commits = set([(item.commit.sha, item.execution.version) for item in execution_state_items])
        repo_state_commits = set([(item.commit.sha, item.execution.version) for item in repo_state])

        # get the difference between the two states
        new_items = []
        if app_state_commits != repo_state_commits:
            new_app_state_commits = repo_state_commits - app_state_commits

            # update the outdated records in app state with the new records from repo state
            logger.info(f"Updating execution state with new commits: {new_app_state_commits}")
            
            # get the new records from the repo state
            new_items.extend([item for item in repo_state if (item.commit.sha, item.execution.version) in new_app_state_commits])

            # insert the new records into the remote app state
            items = format_execution_state_items(new_items)
            for item in items:
                table.put_item(Item=item)

            # insert the new records into the local app state
            execution_state_items.extend(new_items)

        synced_execution_state_items = sorted(
            execution_state_items, key=lambda x: x.commit.date_utc, reverse=False
        )

        # # Reassign the app state commits to the updated state
        # app_state_commits = new_app_state_commits

    except Exception as e:
        import traceback
        message = f"Error syncing app state: {e}\n{traceback.format_exc()}\n{json.dumps(event)}"
        logger.error(message)
        raise Exception(message)





    ### Process New and User Requested Release Versions ###
    unprocessed_execution_state_items_with_params = []
    unprocessed_commits = set()

    # Parse event for user input
    user_items = []
    user_input_parameters = None
    if is_user_event:

        # Get the state items for each release given by the user
        user_releases = [int(release) for release in event["releases"].split(",")]
        user_items = list(filter(
            lambda item: item.execution.version in user_releases,
            synced_execution_state_items
        ))

        user_input_parameters = InputParameters(**event)


    # TODO BOOKMARK 3/10/24
    # Remove duplicate releases before combining new and user items
    if bool(new_items and user_items):
        user_commits = set([(item.commit.sha, item.execution.version) for item in user_items])

        # Remove duplicate release versions
        new_item_commits = list(set(new_app_state_commits) - set(user_commits))
        new_items = [ item for item in new_items if (item.commit.sha, item.execution.version) in new_item_commits ]

        unprocessed_execution_state_items_with_params.extend(
            [ (default_input_parameters, item) for item in new_items + user_items ]
        )

    # Combine the new items and user items paired with respective input parameters
    if bool(new_items and not user_items):
        unprocessed_execution_state_items_with_params.extend(
            [ (default_input_parameters, item) for item in new_items ]
        )
    if bool(not new_items and user_items):
        unprocessed_execution_state_items_with_params.extend(
            [ (user_input_parameters, item) for item in user_items ]
        )

    # Return if there are no new or user requested items
    if not unprocessed_execution_state_items_with_params:
        message = "No new commits found"
        logger.info(message)
        return {
            "statusCode": 200,
            "body": json.dumps({"message": message}),
        }

    # TODO Convert unprocessed items to state machine payload
    # TODO deduplicate and sort the unprocessed_execution_state_items by date


    # try:
        # commits_with_releases = []
        # # Handle manually triggered pipeline execution
        # if "releases" in event:
        #     is_user_event = True

        #     logger.info(f"Processing release(s) from user: {event['releases']}")

        #     # TODO marshal releases to InputPayloadItem instead of casting to int
        #     # extract the most recent record for the release value passed in the event
        #     releases = [int(release) for release in event["releases"].split(",")]

        #     # Filter the execution state for the release version(s) provided in the input event
        #     for release in releases:
        #         commits_with_releases.extend(list(filter(
        #             lambda record: record.execution.version == release,
        #             execution_state
        #         )))

        #     releases_without_commits = list(set(releases) - set([item.execution.version for item in execution_state]))

        #     if not commits_with_releases or releases_without_commits:
        #         logger.info("No commits found for release version(s) provided, fetching most recent commits...")
        #         most_recent_commits = get_most_recent_commits(execution_state)
        #     else:
        #         most_recent_commits = []

        #     # Set input parameters for manual pipeline execution from event
        #     input_parameters = InputParameters(**event)

        # # Handle scheduled pipeline execution
        # is_user_event = False

        # # Use default parameters for automated pipeline execution
        # input_parameters = source_repo_config.default_input_parameters
        # # 2) Get the most recent commits from github since the most recent commit date retrieved from DynamoDB
        # most_recent_commits = get_most_recent_commits(execution_state)

        # TODO (optional) Re-fetch app state from the table and compare it to the local app state to ensure they are in sync
        # # This block can be used to further separate responsibilities if state syncing and release processing are split into separate functions
        # # Get all items from app state table
        # execution_state = get_execution_state(table)
        # execution_state_items = execution_state.items
        # # Compare the app state with the local app state to find new commits
        # if execution_state_items != synced_execution_state_items:
        #     raise Exception("App state is out of sync with the local app state")

        # logger.info(f"Getting release versions")
        # if most_recent_commits:
        #     for commit in most_recent_commits:
        #         sha = commit["sha"]

        #         # Loop through available file assets containing release version metadata
        #         for asset_config in source_repo_config.target_metadata_config.items:
        #             try:

        #                 # Get the release version for the commit by examining file asset contents
        #                 release_version = get_release_version_for_commit(
        #                     commit=commit, 
        #                     owner=GITHUB_REPOSITORY_OWNER,
        #                     repo=GITHUB_REPOSITORY_NAME,
        #                     token=GITHUB_PERSONAL_ACCESS_TOKEN,
        #                     asset_path=asset_config.asset_path,
        #                     metadata_regex=asset_config.metadata_regex
        #                 )
        #                 logger.info(
        #                     f"Found release version {release_version} for commit {sha}"
        #                 )

        #                 # Build the execution object to be stored in the state table (`execution__*` fields)
        #                 execution_detail = ExecutionDetailsConfig(
        #                     **{
        #                         "version": release_version, 
        #                         "status": ExecutionStatus.NOT_PROCESSED
        #                     }
        #                 )

        #                 # Build the repository object to be stored in the state table (`repository__*` fields)
        #                 repository_config = RepositoryConfig(
        #                     **{
        #                         "owner": GITHUB_REPOSITORY_OWNER,
        #                         "name": GITHUB_REPOSITORY_NAME,
        #                         "url": f"https://github.com/{GITHUB_REPOSITORY_OWNER}/{GITHUB_REPOSITORY_NAME}",
        #                         # TODO remove default params from state table, they are retrieved from source config file in S3
        #                         # "default_input_parameters": source_repo_config.default_input_parameters,
        #                     }
        #                 )

        #                 # Assemble the execution state item for the new commit
        #                 execution_state_item = ExecutionStateItem(
        #                     created_utc=utc_now,
        #                     execution=execution_detail,
        #                     repository=repository_config,
        #                     commit=Commit.from_response_json(commit),
        #                 )

        #                 commits_with_releases.append(execution_state_item)
        #                 # break the loop if successful
        #                 break
        #             except Exception as e:
        #                 logger.info(f"Error getting release version for commit {sha}: {e}")

    logger.info("Updating execution state for pending release versions")
        # if is_user_event:
        #     recent_commits_for_release = select_most_recent_commit_for_release(commits_with_releases, releases)
        # else:
        ### Process each release using the most recent commit for that version ###
        # 1a) Mark the most recent commit for each release as PENDING
        # input_parameters = source_repo_config.default_input_parameters
        # recent_commits_for_release = select_most_recent_commit_for_release(commits_with_releases)

        # recent_commits_for_release = select_most_recent_commit_for_release(unprocessed_execution_state_items)
        
        # pending_commits = [
        #     update_execution_state_item(
        #         execution_state_item=execution_state_item,
        #         invocation_id=invocation_id,
        #         status=ExecutionStatus.PENDING,
        #         timestamp=utc_now,
        #         input_parameters=input_parameters,
        #         version=version,
        #     )
        #     for item in recent_commits_for_release
        #     for version, execution_state_item in item.items()
        # ]

        # # 1b) Mark the older commits for each release as SKIPPED *only* if they are marked as NOT_PROCESSED
        # skipped_commits = [
        #     update_execution_state_item(
        #         execution_state_item=execution_state_item, 
        #         invocation_id=invocation_id,
        #         status=ExecutionStatus.SKIPPED,
        #         timestamp=utc_now
        #     )
        #     for execution_state_item in commits_with_releases
        #     if (execution_state_item not in pending_commits and execution_state_item.execution.status == ExecutionStatus.NOT_PROCESSED)
        # ]

        # # 1c) Combine the pending and skipped commits
        # new_execution_state = sorted(
        #     pending_commits + skipped_commits, key=lambda x: x.commit.date_utc, reverse=False
        # )

    try:
        # Update the status of unprocessed items to PENDING
        new_execution_state = [
            update_execution_state_item(
                execution_state_item=item,
                invocation_id=invocation_id,
                status=ExecutionStatus.PENDING,
                timestamp=utc_now,
                input_parameters=input_params,
                version=item.execution.version
            )
            for input_params, item in unprocessed_execution_state_items_with_params
        ]

        # validate that at least one commit is pending, otherwise raise an error
        if not any([item.execution.status == ExecutionStatus.PENDING for item in new_execution_state]):
            message = "Commits were found but none are marked PENDING."
            logger.error(message)
            raise Exception(message)

        # 2) Preprocess the records for the state table (DynamoDB payload)
        items = format_execution_state_items(new_execution_state)

        logger.info(f'Adding items to state table: {json.dumps(items, indent=2)}')

        # 3) Load new commit records to the state table
        if len(items) > 0:
            # Sort by execution__version key
            items = sorted(items, key=lambda x: x["execution__version"], reverse=False)

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
            for item in new_execution_state
        ]
        execution_payload = sorted(
            execution_payload, key=lambda x: x["version"], reverse=False
        )

        # Send the payload to the processing queue for the state machine 
        for item in execution_payload:
            # add group ID and message deduplication ID to the message
            gfedb_processing_queue.send_message(
                MessageGroupId=f'{STAGE}-{APP_NAME}',
                MessageDeduplicationId=str(item["version"]),
                MessageBody=json.dumps(item)
            )

        message = f"Queued {len(execution_payload)} release(s) for processing"
        logger.info(message)
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": message,
                "payload": execution_payload
            }),
        }
    
    except Exception as e:
        import traceback

        message = f"Error processing releases: {e}\n{traceback.format_exc()}\n{json.dumps(event)}"
        logger.error(message)
        raise Exception(message)

    return

def generate_execution_id(sha: str, timestamp: str, version: int = None) -> str:
    """Generate an execution id for the state machine execution with format:
    {version}_{commit_sha}_{YYYYMMDD_HHMMSS}

    Args:
        message (dict): Message from SQS queue

    Returns:
        str: Execution id
    """
    return "_".join(
        [
            str(version),
            sha, #execution_state_item.commit.sha,
            str_to_datetime(timestamp).strftime("%Y%m%d_%H%M%S"),
        ]
    )

# @cache_pickle
def get_execution_state(table, sort_column="commit__date_utc", reverse_sort=True):
    # Retrieve execution state from table
    items = table.scan()["Items"]

    if not items:
        message = "No execution items found. Please populate the state table."
        logger.error(message)
        raise Exception(message)

    items = [
        {k: int(v) if isinstance(v, Decimal) else v for k, v in item.items()}
        for item in items
    ]
    items = sorted(items, key=lambda x: x[sort_column], reverse=reverse_sort)

    # TODO Deserialize and repack the items
    execution_state_items = [
        ExecutionStateItem(**restore_nested_json(item, split_on="__")) for item in items
    ]

    execution_state = ExecutionState(
        **{
            "created_utc": get_utc_now(),
            "items": execution_state_items,
        }
    )

    return execution_state


# @cache_json
# TODO return Commit class to make sure data is correct
def get_most_recent_commits(execution_state):
    # 1) Get the most recent commit date from DynamoDB using max(), add one second to it so the same commit is not returned (because of timestamp overlap)
    last_commit_date = max(
        [str_to_datetime(item.commit.date_utc) for item in execution_state]
    )

    # add minor offset to avoid duplicate commits
    since = str_from_datetime(last_commit_date + timedelta(seconds=1))

    # 2) Get the most recent commits from GitHub using since=<date> parameter
    return list_commits(GITHUB_REPOSITORY_OWNER, GITHUB_REPOSITORY_NAME, since=since, token=GITHUB_PERSONAL_ACCESS_TOKEN)


def select_most_recent_commit_for_release(commits: list[ExecutionStateItem], select_release_versions: list[int] = None) -> list[ExecutionStateItem]:

    # Parameterize for user input (chosen releases new or old) vs scheduled event (all new releases)
    if select_release_versions:
        release_versions = list(set([item.execution.version for item in commits if item.execution.version in select_release_versions]))
    else:
        # group by release version and get most recent by commit date (max date_utc)
        release_versions = list(set([item.execution.version for item in commits]))

    return [
        {
            version: max(
                [item for item in commits if item.execution.version == version],
                key=lambda x: x.commit.date_utc,
            )
        }
        for version in release_versions
    ]

def update_execution_state_item(
    execution_state_item: ExecutionStateItem,
    invocation_id: str,
    status: str,
    timestamp: str,
    input_parameters: dict = None,
    version: int = None
) -> ExecutionStateItem:
    
    execution_state_item.execution.invocation_id = invocation_id
    execution_state_item.execution.status = status
    execution_state_item.updated_utc = timestamp

    if input_parameters is not None and status == ExecutionStatus.PENDING:
        execution_state_item.execution.id = generate_execution_id(
            sha=execution_state_item.commit.sha,
            timestamp=execution_state_item.updated_utc,
            version=version
        )
        execution_state_item.execution.input_parameters = input_parameters
        # TODO Update format to s3://<data_bucket_name>/data/csv/<version>' for csv and s3://<data_bucket_name>/data/dat/<version>' for hla.dat for Glue Catalog
        execution_state_item.execution.s3_path = (
            f"s3://{data_bucket_name}/data/{execution_state_item.execution.version}"
        )

        # execution.date_utc is updated by the state machine. This is different from created_utc which is set by this function
        # execution_state_item.execution.date_utc = timestamp

        # Reset error if present from previous executions
        if execution_state_item.error is not None or execution_state_item.execution.date_utc is not None:
            execution_state_item.error = None
            execution_state_item.execution.date_utc = None


    return execution_state_item

def format_execution_state_items(new_execution_state: list[ExecutionStateItem]) -> list[dict]:
    return [
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


def get_branch_commits(branches: list[dict]) -> list[ExecutionStateItem]:

    # For each entry in all-branches, get the commit data and build the execution state item
    execution_state_items = []

    for item in branches:

        if not version_is_valid(item["name"], return_bool=True):
            continue

        release_version = item["name"]
        sha = item["commit"]["sha"]

        logger.info(f"Retrieving data for {sha}")
        commit_json = get_commit(
            GITHUB_REPOSITORY_OWNER,
            GITHUB_REPOSITORY_NAME,
            GITHUB_PERSONAL_ACCESS_TOKEN,
            sha,
        )
        assert sha == commit_json["sha"]

        commit = Commit(
            sha=commit_json["sha"],
            date_utc=commit_json["commit"]["author"]["date"],
            message=commit_json["commit"]["message"],
            html_url=commit_json["html_url"],
        )

        execution_state_item = ExecutionStateItem(
            created_utc=get_utc_now(),
            updated_utc=get_utc_now(),
            commit=commit,
            execution=ExecutionDetailsConfig(
                version=release_version,
                status="NOT_PROCESSED",
                date_utc=None,
                input_parameters=None,
            ),
            # error=None,
            # s3_path=None,
            repository=RepositoryConfig(
                **select_keys(
                    source_config.repositories[
                        GITHUB_REPOSITORY_OWNER + "/" + GITHUB_REPOSITORY_NAME
                    ].model_dump(),
                    ["owner", "name", "url"],
                )
            ),
        )
        execution_state_items.append(execution_state_item)

    return execution_state_items



def build_execution_state(branches, utc_now=None):

    utc_now = utc_now or get_utc_now()

    # Create ExecutionStateItems array from branch/commit sha pairs
    execution_state_items = get_branch_commits(branches)

    # Sort execution state items by date descending
    execution_state_items = sorted(
        execution_state_items, key=lambda x: x.commit.date_utc, reverse=True
    )

    # Package records as ExecutionState object to seed table
    execution_state = ExecutionState(
        **{
            "created_utc": utc_now,
            "items": execution_state_items,
        }
    )

    return execution_state

if __name__ == "__main__":
    from pathlib import Path

    # event = json.loads((Path(__file__).parent / "schedule-event.json").read_text())
    event = json.loads((Path(__file__).parent / "user-event.json").read_text())

    class MockContext:
        aws_request_id = "1234"

    lambda_handler(event, MockContext())
