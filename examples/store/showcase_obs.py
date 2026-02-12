# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""
Example script demonstrating object storage (OBS) operations with AioBotoClient
"""

import asyncio
import logging
import os
from pathlib import Path

from configs import BUCKET_NAME
from utils.output import write_output

from openjiuwen.core.common.logging import logger as openjiuwen_logger
from openjiuwen.core.foundation.store.object.aioboto_storage_client import AioBotoClient

EXAMPLE_ROOT = Path(__file__).resolve().parent
DATA_DIR = EXAMPLE_ROOT / "data"

# Configuration (feel free to edit)
TEST_OBJECT_NAME = "test/test.txt"
TEST_FILE_PATH = DATA_DIR / "test.txt"
DOWNLOAD_FILE_PATH = DATA_DIR / "download_test.txt"

# Control whether to test create_bucket and delete_bucket operations
# Set to False if your account doesn't have permission to create/delete buckets
HAS_BUCKET_RIGHT = False

# Clean up download file if exists
if DOWNLOAD_FILE_PATH.exists():
    os.remove(DOWNLOAD_FILE_PATH)


async def main():
    """Main example demonstrating object storage operations"""
    # Omit all INFO level logs
    openjiuwen_logger.set_level(logging.WARNING)

    # Initialize OBS client
    write_output("Initializing AioBotoClient...")
    aclient = AioBotoClient()
    write_output("Bucket name: %s", BUCKET_NAME)
    write_output("")

    # Track operation results
    operation_results = {}

    # Step 1: List objects before operations
    write_output("=" * 60)
    write_output("Step 1: Listing objects in bucket (before operations)")
    write_output("=" * 60)
    objects_before = await aclient.list_objects(bucket_name=BUCKET_NAME, object_prefix="")
    if objects_before is not None:
        object_count_before = len(objects_before)
        write_output("✓ Success: Found %d object(s) in bucket", object_count_before)
        operation_results["list_objects_before"] = True
    else:
        write_output("✗ Failed: Failed to list objects")
        object_count_before = 0
        operation_results["list_objects_before"] = False
    write_output("")

    # Step 2: Delete object if it exists (cleanup)
    write_output("=" * 60)
    write_output("Step 2: Cleaning up existing object (if any)")
    write_output("=" * 60)
    write_output("Deleting object: %s", TEST_OBJECT_NAME)
    delete_result = await aclient.delete_object(bucket_name=BUCKET_NAME, object_name=TEST_OBJECT_NAME)
    if delete_result:
        write_output("✓ Success: Object deleted (or didn't exist)")
        operation_results["delete_object_cleanup"] = True
    else:
        write_output("✗ Failed: Failed to delete object")
        operation_results["delete_object_cleanup"] = False
    write_output("")

    # Step 3: Upload file
    write_output("=" * 60)
    write_output("Step 3: Uploading file to object storage")
    write_output("=" * 60)
    if not TEST_FILE_PATH.exists():
        write_output("ERROR: Test file not found at %s", TEST_FILE_PATH)
        write_output("Please create the test file first")
        return

    write_output("Uploading file: %s", TEST_FILE_PATH)
    write_output("Object name: %s", TEST_OBJECT_NAME)
    upload_result = await aclient.upload_file(
        bucket_name=BUCKET_NAME, object_name=TEST_OBJECT_NAME, file_path=TEST_FILE_PATH
    )
    if upload_result:
        write_output("✓ Success: Upload completed successfully")
        operation_results["upload_file"] = True
    else:
        write_output("✗ Failed: Upload failed")
        operation_results["upload_file"] = False
        write_output("Please check the error logs above for details")
    write_output("")

    # Step 4: Download file
    write_output("=" * 60)
    write_output("Step 4: Downloading file from object storage")
    write_output("=" * 60)
    write_output("Downloading object: %s", TEST_OBJECT_NAME)
    write_output("Saving to: %s", DOWNLOAD_FILE_PATH)
    download_result = await aclient.download_file(
        bucket_name=BUCKET_NAME, object_name=TEST_OBJECT_NAME, file_path=DOWNLOAD_FILE_PATH
    )
    if download_result:
        write_output("✓ Success: Download completed successfully")
        operation_results["download_file"] = True
    else:
        write_output("✗ Failed: Download failed")
        operation_results["download_file"] = False
        write_output("Please check the error logs above for details")
    write_output("")

    # Step 5: Verify download
    write_output("=" * 60)
    write_output("Step 5: Verifying downloaded file")
    write_output("=" * 60)
    if DOWNLOAD_FILE_PATH.exists():
        original_size = TEST_FILE_PATH.stat().st_size
        downloaded_size = DOWNLOAD_FILE_PATH.stat().st_size
        write_output("Original file size: %d bytes", original_size)
        write_output("Downloaded file size: %d bytes", downloaded_size)
        if original_size == downloaded_size:
            write_output("✓ PASS: File sizes match")
        else:
            write_output("✗ FAIL: File sizes do not match")
    else:
        write_output("✗ FAIL: Downloaded file not found")
    write_output("")

    # Step 6: List objects after operations
    write_output("=" * 60)
    write_output("Step 6: Listing objects in bucket (after operations)")
    write_output("=" * 60)
    objects_after = await aclient.list_objects(bucket_name=BUCKET_NAME, object_prefix="")
    if objects_after is not None:
        object_count_after = len(objects_after)
        write_output("✓ Success: Found %d object(s) in bucket", object_count_after)
        operation_results["list_objects_after"] = True
        if object_count_after > object_count_before:
            write_output("✓ PASS: Object count increased (upload successful)")
    else:
        write_output("✗ Failed: Failed to list objects")
        object_count_after = object_count_before
        operation_results["list_objects_after"] = False
    write_output("")

    # Step 7: Cleanup - delete uploaded object
    write_output("=" * 60)
    write_output("Step 7: Cleaning up uploaded object")
    write_output("=" * 60)
    write_output("Deleting object: %s", TEST_OBJECT_NAME)
    delete_result = await aclient.delete_object(bucket_name=BUCKET_NAME, object_name=TEST_OBJECT_NAME)
    if delete_result:
        write_output("✓ Success: Object deleted successfully")
        operation_results["delete_object_final"] = True
    else:
        write_output("✗ Failed: Failed to delete object")
        operation_results["delete_object_final"] = False
    write_output("")

    # Step 8: Test create_bucket and delete_bucket (optional)
    write_output("=" * 60)
    write_output("Step 8: Testing create_bucket and delete_bucket operations")
    write_output("=" * 60)
    if HAS_BUCKET_RIGHT:
        test_bucket_name = f"{BUCKET_NAME}-test-create"
        write_output("Attempting to create test bucket: %s", test_bucket_name)
        write_output("Note: This may fail if bucket already exists or you don't have permission")
        create_result = await aclient.create_bucket(bucket_name=test_bucket_name, location="ap-southeast-1")
        if create_result:
            write_output("✓ Success: Test bucket created successfully")
            operation_results["create_bucket"] = True
            # Clean up the test bucket
            write_output("Cleaning up test bucket...")
            delete_bucket_result = await aclient.delete_bucket(bucket_name=test_bucket_name)
            if delete_bucket_result:
                write_output("✓ Success: Test bucket deleted successfully")
                operation_results["delete_bucket"] = True
            else:
                write_output("✗ Failed: Failed to delete test bucket")
                operation_results["delete_bucket"] = False
        else:
            write_output("✗ Failed: Failed to create test bucket (may already exist or no permission)")
            operation_results["create_bucket"] = False
            operation_results["delete_bucket"] = None  # Not attempted (bucket not created)
    else:
        write_output("Skipped: Bucket operations not tested (HAS_BUCKET_RIGHT=False)")
        operation_results["create_bucket"] = None  # Not attempted due to permission
        operation_results["delete_bucket"] = None  # Not attempted due to permission
    write_output("")

    # Summary
    write_output("=" * 60)
    write_output("Summary:")
    write_output("=" * 60)

    # Upload operation
    upload_status = "✓ Success" if operation_results.get("upload_file") else "✗ Failed"
    write_output(
        "%s Upload operation: %s", upload_status, "Completed" if operation_results.get("upload_file") else "Failed"
    )

    # Download operation
    download_status = "✓ Success" if operation_results.get("download_file") else "✗ Failed"
    write_output(
        "%s Download operation: %s",
        download_status,
        "Completed" if operation_results.get("download_file") else "Failed",
    )

    # File verification
    file_verification = DOWNLOAD_FILE_PATH.exists() and operation_results.get("download_file")
    if file_verification:
        original_size = TEST_FILE_PATH.stat().st_size if TEST_FILE_PATH.exists() else 0
        downloaded_size = DOWNLOAD_FILE_PATH.stat().st_size if DOWNLOAD_FILE_PATH.exists() else 0
        if original_size == downloaded_size:
            write_output("✓ File verification: Passed (sizes match: %d bytes)", original_size)
        else:
            write_output(
                "✗ File verification: Failed (sizes don't match: %d vs %d bytes)", original_size, downloaded_size
            )
    else:
        write_output("✗ File verification: Failed (file not found or download failed)")

    # Delete object operations
    delete_cleanup_status = "✓ Success" if operation_results.get("delete_object_cleanup") else "✗ Failed"
    write_output(
        "%s Delete object (cleanup): %s",
        delete_cleanup_status,
        "Completed" if operation_results.get("delete_object_cleanup") else "Failed",
    )

    delete_final_status = "✓ Success" if operation_results.get("delete_object_final") else "✗ Failed"
    write_output(
        "%s Delete object (final): %s",
        delete_final_status,
        "Completed" if operation_results.get("delete_object_final") else "Failed",
    )

    # List objects operations
    list_before_status = "✓ Success" if operation_results.get("list_objects_before") else "✗ Failed"
    write_output(
        "%s List objects (before): %s",
        list_before_status,
        "Completed" if operation_results.get("list_objects_before") else "Failed",
    )

    list_after_status = "✓ Success" if operation_results.get("list_objects_after") else "✗ Failed"
    write_output(
        "%s List objects (after): %s",
        list_after_status,
        "Completed" if operation_results.get("list_objects_after") else "Failed",
    )

    # Create/Delete bucket operations
    if operation_results.get("create_bucket") is not None:
        create_bucket_status = "✓ Success" if operation_results.get("create_bucket") else "✗ Failed"
        write_output(
            "%s Create bucket: %s",
            create_bucket_status,
            "Completed" if operation_results.get("create_bucket") else "Failed",
        )
    else:
        write_output("- Create bucket: Skipped (HAS_BUCKET_RIGHT=False)")

    if operation_results.get("delete_bucket") is not None:
        delete_bucket_status = "✓ Success" if operation_results.get("delete_bucket") else "✗ Failed"
        write_output(
            "%s Delete bucket: %s",
            delete_bucket_status,
            "Completed" if operation_results.get("delete_bucket") else "Failed",
        )
    else:
        write_output("- Delete bucket: Skipped (HAS_BUCKET_RIGHT=False or bucket not created)")

    write_output("")

    # Overall result
    all_critical_ops = [
        operation_results.get("upload_file"),
        operation_results.get("download_file"),
        operation_results.get("delete_object_final"),
    ]
    if all(all_critical_ops):
        write_output("✓ All critical object storage operations completed successfully!")
    else:
        write_output("✗ Some operations failed. Please check the logs above for details.")


if __name__ == "__main__":
    asyncio.run(main())
